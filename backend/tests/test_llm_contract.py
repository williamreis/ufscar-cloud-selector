"""
Contrato da camada de LLM: prompts versionados e saída validada (§25, §28.2, §35).

O que estes testes fixam é a fronteira da §2.1: a LLM entrega texto que precisa
caber num schema, e o que não couber **não vira dado**. Nenhum caminho aqui
transforma resposta malformada em valor aceito.

O modelo é substituído por um duplo em todos os casos — a suíte não depende de
rede nem de chave de API.
"""

import asyncio
import time
from dataclasses import replace

import pytest
from pydantic import BaseModel

from config import ProviderProfile, get_settings, reload_settings
from llm import client as llm_client
from llm.client import (
    STATUS_OK,
    STATUS_OUTPUT_INVALID,
    STATUS_UNAVAILABLE,
    LangChainLLMClient,
    extract_json_object,
)
from llm.prompts import Prompt, PromptRenderError, get, registered_versions
from llm.schemas import PreferenceNotes


class Resposta(BaseModel):
    notes: str


class FakeMessage:
    def __init__(self, content, usage=None):
        self.content = content
        self.usage_metadata = usage or {}


class FakeModel:
    """Devolve as respostas na ordem dada e guarda o que recebeu."""

    def __init__(self, *respostas):
        self._respostas = list(respostas)
        self.chamadas = []

    async def ainvoke(self, messages):
        self.chamadas.append(messages)
        resposta = self._respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta


class ModeloQuebrado:
    async def ainvoke(self, messages):
        raise RuntimeError("conexão recusada")


@pytest.fixture(autouse=True)
def _sem_cooldown_vazado(tmp_path, monkeypatch):
    """
    A espera de cota é estado de processo **e de disco** (ver `llm.client`).

    Sem zerar entre os testes, um 429 encenado num teste faria o seguinte pular o
    provedor primário — e o teste passaria ou falharia conforme a ordem da suíte.
    O arquivo é redirecionado para o tmp pelo mesmo motivo, elevado: sem isso a
    suíte grava no volume de dados real e a recusa encenada sobrevive à própria
    suíte, atrapalhando a execução seguinte e o backend da máquina.
    """
    llm_client.reset_provider_cooldowns()
    fake = replace(get_settings(), llm_cooldown_state_path=tmp_path / "llm_cooldown.json")
    monkeypatch.setattr(llm_client, "get_settings", lambda: fake)
    yield
    llm_client.reset_provider_cooldowns()


def _run(coro):
    return asyncio.run(coro)


def _prompt():
    return get("PROMPT_PREFERENCE_NOTES_V1").render(
        criteria_weights="{}", relevance="{}", qa_pairs="<USER_CONTEXT>oi</USER_CONTEXT>"
    )


# --- Registro de prompts (§28.2) -------------------------------------------


def test_prompt_tem_identificador_e_versao():
    prompt = get("PROMPT_PREFERENCE_NOTES_V1")
    assert prompt.id == "PROMPT_PREFERENCE_NOTES_V1"
    assert prompt.version == "1"
    assert "PROMPT_PREFERENCE_NOTES_V1" in registered_versions()


def test_prompt_desconhecido_falha_alto():
    with pytest.raises(KeyError):
        get("PROMPT_INEXISTENTE_V1")


def test_variavel_ausente_e_erro():
    with pytest.raises(PromptRenderError):
        get("PROMPT_PREFERENCE_NOTES_V1").render(criteria_weights="{}")


def test_variavel_desconhecida_e_erro():
    """Protege contra renomear a variável no template e esquecer do chamador."""
    with pytest.raises(PromptRenderError):
        get("PROMPT_PREFERENCE_NOTES_V1").render(
            criteria_weights="{}", relevance="{}", qa_pairs="x", inventada="y"
        )


def test_chave_no_conteudo_nao_e_reinterpretada():
    """O texto do gestor não pode ser lido como marcador de template."""
    prompt = Prompt(id="P_TESTE_V1", version="1", system="s", user_template="X: {{valor}}")
    rendered = prompt.render(valor="{{criteria_weights}} e { chave }")
    assert rendered.user == "X: {{criteria_weights}} e { chave }"


def test_regras_de_contencao_estao_no_system():
    """As proibições da §2.1 precisam viajar com o prompt, não com o chamador."""
    system = get("PROMPT_PREFERENCE_NOTES_V1").system
    assert "USER_CONTEXT" in system
    for proibicao in ("não recalcule", "não crie", "não selecione"):
        assert proibicao in system.lower()


# --- Recorte de JSON -------------------------------------------------------


@pytest.mark.parametrize(
    "bruto,esperado",
    [
        ('{"notes":"x"}', {"notes": "x"}),
        ('```json\n{"notes":"x"}\n```', {"notes": "x"}),
        ('Claro! {"notes":"a } b"} pronto', {"notes": "a } b"}),
        ('{"notes":"aninhado","extra":{"a":1}}', {"notes": "aninhado", "extra": {"a": 1}}),
        ("sem json", None),
        ('{"quebrado":', None),
        ("", None),
    ],
)
def test_recorte_de_json(bruto, esperado):
    assert extract_json_object(bruto) == esperado


# --- Geração estruturada (§25) ---------------------------------------------


def test_saida_valida_e_aceita_na_primeira_tentativa():
    modelo = FakeModel(FakeMessage('{"notes":"tudo certo"}'))
    result = _run(LangChainLLMClient(model=modelo).structured_generate(_prompt(), Resposta))

    assert result.ok
    assert result.data.notes == "tudo certo"
    assert result.run.status == STATUS_OK
    assert result.run.attempts == 1
    assert len(modelo.chamadas) == 1


def test_saida_invalida_gera_um_retry_com_correcao():
    """§25: 1ª falha → retry controlado com mensagem de correção."""
    modelo = FakeModel(FakeMessage("desculpe, não entendi"), FakeMessage('{"notes":"agora sim"}'))
    result = _run(LangChainLLMClient(model=modelo).structured_generate(_prompt(), Resposta))

    assert result.ok and result.run.attempts == 2
    assert len(modelo.chamadas) == 2
    # A segunda chamada reapresenta o schema em vez de repetir o pedido original.
    correcao = str(modelo.chamadas[1][-1])
    assert "JSON" in correcao and "properties" in correcao


def test_duas_falhas_viram_llm_output_invalid():
    modelo = FakeModel(FakeMessage("nada"), FakeMessage("nada de novo"))
    result = _run(LangChainLLMClient(model=modelo).structured_generate(_prompt(), Resposta))

    assert not result.ok
    assert result.data is None  # nada é aproveitado
    assert result.run.status == STATUS_OUTPUT_INVALID
    assert result.run.attempts == 2


def test_json_valido_fora_do_schema_e_rejeitado():
    """Formato certo, campo errado: continua não sendo dado."""
    modelo = FakeModel(FakeMessage('{"peso":0.9}'), FakeMessage('{"peso":0.9}'))
    result = _run(LangChainLLMClient(model=modelo).structured_generate(_prompt(), Resposta))
    assert result.run.status == STATUS_OUTPUT_INVALID
    assert result.data is None


def test_provedor_indisponivel_nao_vira_dado():
    result = _run(
        LangChainLLMClient(model=ModeloQuebrado()).structured_generate(_prompt(), Resposta)
    )
    assert result.run.status == STATUS_UNAVAILABLE
    assert result.data is None
    assert "conexão recusada" in result.run.error


# --- Registro de execução (§27) --------------------------------------------


def test_execucao_registra_o_que_a_auditoria_exige():
    modelo = FakeModel(
        FakeMessage(
            '{"notes":"ok"}',
            usage={"input_tokens": 120, "output_tokens": 30, "total_tokens": 150},
        )
    )
    run = _run(LangChainLLMClient(model=modelo).structured_generate(_prompt(), Resposta)).run

    assert run.prompt_id == "PROMPT_PREFERENCE_NOTES_V1"
    assert run.prompt_version == "1"
    assert run.provider and run.model
    assert run.latency_ms >= 0
    assert (run.input_tokens, run.output_tokens, run.total_tokens) == (120, 30, 150)
    assert len(run.input_hash) == 64 and len(run.output_hash) == 64
    assert run.run_id


def test_tokens_ausentes_ficam_nulos_nao_zero():
    """§27 diz 'quando disponível' — zero seria uma medição que não houve."""
    modelo = FakeModel(FakeMessage('{"notes":"ok"}'))
    run = _run(LangChainLLMClient(model=modelo).structured_generate(_prompt(), Resposta)).run
    assert run.input_tokens is None and run.total_tokens is None


def test_entradas_iguais_produzem_o_mesmo_hash():
    modelo = FakeModel(FakeMessage('{"notes":"ok"}'), FakeMessage('{"notes":"ok"}'))
    cliente = LangChainLLMClient(model=modelo)
    a = _run(cliente.structured_generate(_prompt(), Resposta)).run
    b = _run(cliente.structured_generate(_prompt(), Resposta)).run
    assert a.input_hash == b.input_hash
    assert a.run_id != b.run_id


def test_schema_de_notas_nao_admite_numero_de_peso():
    """A saída do prompt de justificativa é texto — não há campo por onde entrar peso."""
    assert set(PreferenceNotes.model_fields) == {"notes"}


# --- Limite de taxa do provedor (§26) --------------------------------------


class RateLimitError(Exception):
    """Imita o 429 do Groq: status no atributo e janela de espera na mensagem."""

    def __init__(self, mensagem, status_code=429):
        super().__init__(mensagem)
        self.status_code = status_code


def _sem_espera(monkeypatch):
    """Substitui o sleep para que o teste meça a decisão, não o relógio."""
    dormidas = []

    async def falso_sleep(segundos):
        dormidas.append(segundos)

    monkeypatch.setattr(llm_client.asyncio, "sleep", falso_sleep)
    return dormidas


def _cliente(modelo, **overrides):
    """
    Cliente com um duplo no lugar do modelo e **sem cadeia de fallback**.

    Zerar `llm_fallbacks` é deliberado: o comportamento de espera destes testes é
    o do último elo da cadeia, e sem fixá-lo aqui a suíte passaria a depender de
    haver, ou não, uma OPENROUTER_API_KEY no `.env` de quem roda.
    """
    overrides.setdefault("llm_fallbacks", ())
    base = llm_client.get_settings()
    return LangChainLLMClient(settings=replace(base, **overrides), model=modelo)


_MENSAGEM_429 = (
    "Error code: 429 - {'error': {'message': 'Rate limit reached for model "
    "`openai/gpt-oss-120b` ... on tokens per minute (TPM): Limit 8000, Used 7729, "
    "Requested 4480. Please try again in 31.567499999s.', 'code': 'rate_limit_exceeded'}}"
)


@pytest.mark.parametrize(
    "mensagem,esperado",
    [
        (_MENSAGEM_429, pytest.approx(31.5675)),
        ("Rate limit reached. Please try again in 1m2.5s.", pytest.approx(62.5)),
        ("Rate limit reached, sem janela informada", None),
    ],
)
def test_janela_de_espera_sai_da_resposta_do_provedor(mensagem, esperado):
    assert llm_client._suggested_wait(RateLimitError(mensagem)) == esperado


def test_cabecalho_retry_after_tem_precedencia_sobre_a_mensagem():
    class ComResposta(RateLimitError):
        class response:  # noqa: N801 — imita o objeto do SDK
            headers = {"retry-after": "12"}

    assert llm_client._suggested_wait(ComResposta(_MENSAGEM_429)) == 12.0


def test_rate_limit_e_espera_nao_indisponibilidade(monkeypatch):
    """O 429 passa sozinho: esperar e repetir preserva a extração da dimensão."""
    dormidas = _sem_espera(monkeypatch)
    modelo = FakeModel(RateLimitError(_MENSAGEM_429), FakeMessage('{"notes":"depois da espera"}'))

    result = _run(_cliente(modelo).structured_generate(_prompt(), Resposta))

    assert result.ok and result.data.notes == "depois da espera"
    # A espera é de transporte: não consome a tentativa de correção da §25.
    assert result.run.attempts == 1
    assert len(dormidas) == 1
    assert 31.5 <= dormidas[0] <= 33.0  # janela pedida pelo provedor, mais jitter
    assert result.attempts_detail[0]["rate_limit_waits"] == 1


def test_rate_limit_insistente_termina_em_unavailable(monkeypatch):
    """Esgotadas as esperas, é falha declarada — nunca pontuação presumida."""
    dormidas = _sem_espera(monkeypatch)
    modelo = FakeModel(*[RateLimitError(_MENSAGEM_429) for _ in range(4)])

    result = _run(
        _cliente(modelo, llm_rate_limit_retries=3).structured_generate(_prompt(), Resposta)
    )

    assert result.run.status == STATUS_UNAVAILABLE
    assert result.data is None
    assert len(dormidas) == 3


def test_espera_maior_que_o_teto_falha_de_imediato(monkeypatch):
    """Segurar a requisição além do teto só empurraria o timeout ao usuário."""
    dormidas = _sem_espera(monkeypatch)
    modelo = FakeModel(RateLimitError("Rate limit reached. Please try again in 600s."))

    result = _run(
        _cliente(modelo, llm_rate_limit_max_wait_s=60.0).structured_generate(_prompt(), Resposta)
    )

    assert result.run.status == STATUS_UNAVAILABLE
    assert dormidas == []


def test_erro_que_nao_e_rate_limit_nao_espera(monkeypatch):
    """Chave inválida não melhora com o tempo: repetir seria só demora."""
    dormidas = _sem_espera(monkeypatch)
    modelo = FakeModel(RateLimitError("Error code: 401 - invalid_api_key", status_code=401))

    result = _run(_cliente(modelo).structured_generate(_prompt(), Resposta))

    assert result.run.status == STATUS_UNAVAILABLE
    assert dormidas == []


def test_contagem_de_token_na_mensagem_nao_e_confundida_com_429(monkeypatch):
    """'Used 4290' não é limite de taxa: casar '429' solto faria esperar à toa."""
    dormidas = _sem_espera(monkeypatch)
    modelo = FakeModel(RuntimeError("Error code: 500 - internal error (Used 4290 tokens)"))

    result = _run(_cliente(modelo).structured_generate(_prompt(), Resposta))

    assert result.run.status == STATUS_UNAVAILABLE
    assert dormidas == []


# --- Cadeia de provedores (§35.2) ------------------------------------------


_MENSAGEM_SEM_SALDO = (
    "Error code: 402 - {'error': {'message': '402 Insufficient credits. "
    "Add more using https://openrouter.ai/credits', 'code': 402}}"
)


def _cliente_em_cadeia(primario, fallback, **overrides):
    """Groq no primeiro elo, OpenRouter no segundo — a configuração padrão."""
    settings = replace(
        llm_client.get_settings(),
        llm_provider="groq",
        llm_model="openai/gpt-oss-120b",
        llm_api_key="chave-groq",
        llm_fallbacks=(ProviderProfile("openrouter", "meta-llama/llama-4-maverick:free", "k"),),
        **overrides,
    )
    return LangChainLLMClient(
        settings=settings, models={"groq": primario, "openrouter": fallback}
    )


def test_openrouter_assume_quando_o_groq_bate_no_limite(monkeypatch):
    dormidas = _sem_espera(monkeypatch)
    groq = FakeModel(RateLimitError(_MENSAGEM_429))
    openrouter = FakeModel(FakeMessage('{"notes":"respondido pelo fallback"}'))

    result = _run(_cliente_em_cadeia(groq, openrouter).structured_generate(_prompt(), Resposta))

    assert result.ok and result.data.notes == "respondido pelo fallback"
    # Com alternativa ociosa, esperar pelo primário seria trocar disponibilidade
    # por nada: a troca é imediata.
    assert dormidas == []
    assert len(openrouter.chamadas) == 1


def test_openrouter_assume_quando_falta_saldo_no_groq(monkeypatch):
    _sem_espera(monkeypatch)
    groq = FakeModel(RateLimitError(_MENSAGEM_SEM_SALDO, status_code=402))
    openrouter = FakeModel(FakeMessage('{"notes":"seguiu no fallback"}'))

    result = _run(_cliente_em_cadeia(groq, openrouter).structured_generate(_prompt(), Resposta))

    assert result.ok and result.data.notes == "seguiu no fallback"


def test_execucao_registra_quem_respondeu_e_de_quem_era_a_vez(monkeypatch):
    """§27: dizer que a evidência veio do Groq quando veio do OpenRouter é falso."""
    _sem_espera(monkeypatch)
    groq = FakeModel(RateLimitError(_MENSAGEM_429))
    openrouter = FakeModel(FakeMessage('{"notes":"ok"}'))

    run = _run(_cliente_em_cadeia(groq, openrouter).structured_generate(_prompt(), Resposta)).run

    assert run.provider == "openrouter"
    assert run.model == "meta-llama/llama-4-maverick:free"
    assert run.fallback_from == "groq"
    assert run.as_dict()["fallback_from"] == "groq"


def test_sem_troca_o_registro_nao_inventa_fallback():
    modelo = FakeModel(FakeMessage('{"notes":"ok"}'))
    run = _run(_cliente(modelo).structured_generate(_prompt(), Resposta)).run
    assert run.provider == get_settings().llm_provider
    assert run.fallback_from is None


def test_chave_recusada_nao_aciona_o_fallback(monkeypatch):
    """Erro de instalação precisa aparecer, não ser contornado em silêncio."""
    _sem_espera(monkeypatch)
    groq = FakeModel(RateLimitError("Error code: 401 - invalid_api_key", status_code=401))
    openrouter = FakeModel(FakeMessage('{"notes":"não deveria ser chamado"}'))

    result = _run(_cliente_em_cadeia(groq, openrouter).structured_generate(_prompt(), Resposta))

    assert result.run.status == STATUS_UNAVAILABLE
    assert result.run.provider == "groq"
    assert openrouter.chamadas == []


def test_saida_invalida_nao_troca_de_provedor(monkeypatch):
    """§25: rejeição é veredito, não convite a procurar um modelo mais dócil."""
    _sem_espera(monkeypatch)
    groq = FakeModel(FakeMessage("nada"), FakeMessage("nada de novo"))
    openrouter = FakeModel(FakeMessage('{"notes":"não deveria ser chamado"}'))

    result = _run(_cliente_em_cadeia(groq, openrouter).structured_generate(_prompt(), Resposta))

    assert result.run.status == STATUS_OUTPUT_INVALID
    assert result.run.provider == "groq"
    assert openrouter.chamadas == []


def test_cadeia_esgotada_termina_em_unavailable(monkeypatch):
    """Os dois recusam: falha declarada no último elo, sem pontuação presumida."""
    dormidas = _sem_espera(monkeypatch)
    groq = FakeModel(RateLimitError(_MENSAGEM_429))
    openrouter = FakeModel(*[RateLimitError(_MENSAGEM_429) for _ in range(3)])

    result = _run(
        _cliente_em_cadeia(groq, openrouter, llm_rate_limit_retries=2).structured_generate(
            _prompt(), Resposta
        )
    )

    assert result.run.status == STATUS_UNAVAILABLE
    assert result.run.provider == "openrouter"
    assert result.data is None
    # O último elo não tem para quem passar: aí sim usa o orçamento de esperas.
    assert len(dormidas) == 2


def test_fallback_sem_chave_fica_fora_da_cadeia(monkeypatch):
    """Fallback sem credencial trocaria erro de cota por erro de configuração."""
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "chave-groq")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDERS", "openrouter")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    settings = reload_settings()
    try:
        assert settings.llm_fallbacks == ()
        assert [p.provider for p in settings.llm_chain] == ["groq"]
    finally:
        reload_settings()


def test_cadeia_padrao_e_groq_seguido_de_openrouter(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_FALLBACK_PROVIDERS", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "chave-groq")
    monkeypatch.setenv("OPENROUTER_API_KEY", "chave-openrouter")
    # Fixado aqui pelo mesmo motivo que `_cliente` zera a cadeia: sem isto o teste
    # lê o OPENROUTER_MODEL do `.env` de quem roda e passa a afirmar coisas sobre a
    # instalação local em vez de sobre o código.
    monkeypatch.setenv("OPENROUTER_MODEL", "modelo/do-openrouter")

    settings = reload_settings()
    try:
        assert [p.provider for p in settings.llm_chain] == ["groq", "openrouter"]
        # Cada elo com o modelo e a chave dele: o do primário no fallback pediria
        # ao OpenRouter um nome de modelo que só existe no Groq.
        primario, fallback = settings.llm_chain
        assert primario.model == "openai/gpt-oss-120b" and primario.api_key == "chave-groq"
        assert fallback.model == "modelo/do-openrouter" and fallback.api_key == "chave-openrouter"
    finally:
        reload_settings()


def test_default_do_openrouter_e_gratuito(monkeypatch):
    """
    Sem OPENROUTER_MODEL no ambiente, o fallback é uma variante ":free".

    Um valor que ninguém escolheu não pode começar a gastar crédito. Quem tem
    saldo e quer usá-lo tira o sufixo no `.env` — e é por isso que o teste acima
    não pode exigir `:free` do modelo *configurado*, só deste default.
    """
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "chave-openrouter")

    settings = reload_settings()
    try:
        fallback = next(p for p in settings.llm_chain if p.provider == "openrouter")
        assert fallback.model.endswith(":free")
    finally:
        reload_settings()


def test_override_de_modelo_nao_vaza_para_o_fallback(monkeypatch):
    """LLM_MODEL diz qual modelo usar no primário — não em todos os provedores."""
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "chave-groq")
    monkeypatch.setenv("OPENROUTER_API_KEY", "chave-openrouter")
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-oss-20b")

    settings = reload_settings()
    try:
        assert settings.llm_model == "openai/gpt-oss-20b"
        assert settings.llm_fallbacks[0].model != "openai/gpt-oss-20b"
    finally:
        reload_settings()


def test_resposta_truncada_e_diagnosticada_como_truncada():
    """
    Cortada no teto de tokens ≠ malformada.

    A diferença é acionável: uma pede outro prompt, a outra pede
    LLM_MAX_TOKENS maior. Confundir as duas foi o que escondeu, por uma
    avaliação inteira, que a extração perdia todas as dimensões por truncamento.
    """

    class Truncada(FakeMessage):
        response_metadata = {"finish_reason": "length"}

    cortado = '{"notes": "começou a responder mas não fech'
    modelo = FakeModel(Truncada(cortado), Truncada(cortado))
    result = _run(_cliente(modelo).structured_generate(_prompt(), Resposta))

    assert result.run.status == STATUS_OUTPUT_INVALID
    assert "truncada" in result.run.error and "LLM_MAX_TOKENS" in result.run.error


# --- Memória da recusa por cota --------------------------------------------


def test_provedor_que_recusou_por_cota_e_pulado_na_chamada_seguinte(monkeypatch):
    """
    A janela que o provedor informou vale para as chamadas seguintes.

    Uma avaliação faz uma chamada por (provedor × dimensão). Sem esta memória,
    todas batem no primário só para ouvir o mesmo 429 — nove ida-e-voltas
    jogados fora, e o gestor esperando por todas.
    """
    _sem_espera(monkeypatch)
    groq = FakeModel(RateLimitError(_MENSAGEM_429))  # uma recusa só: não há segunda
    openrouter = FakeModel(
        FakeMessage('{"notes":"primeira"}'), FakeMessage('{"notes":"segunda"}')
    )
    cliente = _cliente_em_cadeia(groq, openrouter)

    primeira = _run(cliente.structured_generate(_prompt(), Resposta))
    segunda = _run(cliente.structured_generate(_prompt(), Resposta))

    assert primeira.ok and segunda.ok
    assert primeira.data.notes == "primeira" and segunda.data.notes == "segunda"
    # A segunda nem tentou a Groq: o duplo tinha uma resposta só e não estourou.
    assert len(groq.chamadas) == 1
    assert len(openrouter.chamadas) == 2
    assert segunda.run.provider == "openrouter" and segunda.run.fallback_from == "groq"


def test_espera_de_cota_expira_sozinha(monkeypatch):
    """O compasso de espera é um respiro, não um banimento."""
    _sem_espera(monkeypatch)
    groq = FakeModel(RateLimitError(_MENSAGEM_429), FakeMessage('{"notes":"groq de volta"}'))
    openrouter = FakeModel(FakeMessage('{"notes":"fallback"}'))
    cliente = _cliente_em_cadeia(groq, openrouter)

    _run(cliente.structured_generate(_prompt(), Resposta))

    # Adianta o relógio para além da janela de 31s que a mensagem pediu.
    agora = llm_client.time.monotonic()
    monkeypatch.setattr(llm_client.time, "monotonic", lambda: agora + 120)

    segunda = _run(cliente.structured_generate(_prompt(), Resposta))
    assert segunda.ok and segunda.data.notes == "groq de volta"
    assert segunda.run.provider == "groq" and segunda.run.fallback_from is None


def test_ultimo_elo_nunca_e_pulado(monkeypatch):
    """Pular todos devolveria falha sem ter chamado ninguém."""
    _sem_espera(monkeypatch)
    llm_client._provider_cooldown["openrouter"] = llm_client.time.monotonic() + 300
    groq = FakeModel(RateLimitError(_MENSAGEM_429))
    openrouter = FakeModel(FakeMessage('{"notes":"atendeu mesmo em espera"}'))

    result = _run(_cliente_em_cadeia(groq, openrouter).structured_generate(_prompt(), Resposta))

    assert result.ok and result.run.provider == "openrouter"


def test_falha_que_nao_e_cota_nao_gera_espera(monkeypatch):
    """Chave recusada não é congestionamento: não há janela para respeitar."""
    _sem_espera(monkeypatch)
    groq = FakeModel(RateLimitError("Error code: 401 - invalid_api_key", status_code=401))
    openrouter = FakeModel(FakeMessage('{"notes":"x"}'))

    _run(_cliente_em_cadeia(groq, openrouter).structured_generate(_prompt(), Resposta))

    assert llm_client._provider_cooldown == {}


_MENSAGEM_COTA_DIARIA = (
    "Error code: 429 - {'error': {'message': 'Rate limit reached for model "
    "`openai/gpt-oss-120b` on tokens per day (TPD): Limit 200000, Used 195210, "
    "Requested 7754. Please try again in 21m20.448s.', 'code': 'rate_limit_exceeded'}}"
)


def test_janela_de_cota_diaria_e_respeitada_por_inteiro(monkeypatch):
    """
    Pular não custa espera, logo não usa o orçamento de espera.

    O Groq pede 21 minutos ao estourar a cota diária. Truncar isso no teto de
    `LLM_RATE_LIMIT_MAX_WAIT_S` (60s) fazia as chamadas voltarem a bater na mesma
    recusa um minuto depois — que é o defeito que este teste tranca.
    """
    _sem_espera(monkeypatch)
    groq = FakeModel(RateLimitError(_MENSAGEM_COTA_DIARIA))
    openrouter = FakeModel(FakeMessage('{"notes":"ok"}'))

    _run(
        _cliente_em_cadeia(
            groq, openrouter, llm_rate_limit_max_wait_s=60.0, llm_cooldown_max_s=1800.0
        ).structured_generate(_prompt(), Resposta)
    )

    restante = llm_client._cooldown_restante("groq")
    assert restante > 1200  # os 21 minutos declarados, e não os 60s de espera


def test_teto_de_cooldown_limita_janela_absurda(monkeypatch):
    """Prazo declarado é do provedor; ficar horas fora dele é decisão nossa."""
    _sem_espera(monkeypatch)
    groq = FakeModel(RateLimitError("Rate limit reached. Please try again in 600m0s."))
    openrouter = FakeModel(FakeMessage('{"notes":"ok"}'))

    _run(
        _cliente_em_cadeia(groq, openrouter, llm_cooldown_max_s=1800.0).structured_generate(
            _prompt(), Resposta
        )
    )

    assert llm_client._cooldown_restante("groq") <= 1800


# --- Cota diária: o 429 que não passa esperando ------------------------------
#
# O Groq anuncia a cota diária em prosa ("try again in 21m20.448s") e o OpenRouter
# não anuncia em prosa nenhuma: manda o epoch de reposição, em milissegundos,
# aninhado no corpo do erro. As duas formas precisam produzir a mesma decisão —
# não esperar —, porque a janela é de horas e o orçamento de espera é de segundos.

_MENSAGEM_OPENROUTER_DIARIA = (
    "Error code: 429 - {'error': {'message': 'Rate limit exceeded: free-models-per-day. "
    "Add 5 credits to unlock 1000 free model requests per day', 'code': 429, "
    "'metadata': {'headers': {'X-RateLimit-Limit': '50', 'X-RateLimit-Remaining': '0', "
    "'X-RateLimit-Reset': '%d'}, 'limit_source': 'openrouter_free_tier_daily'}}}"
)


def _recusa_openrouter(segundos_ate_a_reposicao=7200.0):
    """O 429 real do OpenRouter, com o epoch de reposição em milissegundos."""
    epoch_ms = int((time.time() + segundos_ate_a_reposicao) * 1000)
    return RateLimitError(_MENSAGEM_OPENROUTER_DIARIA % epoch_ms)


def test_janela_do_openrouter_sai_do_epoch_de_reposicao():
    """Epoch em milissegundos no corpo do 429 é janela declarada, não ausência dela."""
    assert llm_client._suggested_wait(_recusa_openrouter(7200.0)) == pytest.approx(7200, abs=5)


def test_epoch_de_reposicao_vencido_nao_vira_espera_negativa():
    """Relógio fora de sincronia manda tentar de novo, não esperar ao contrário."""
    assert llm_client._suggested_wait(_recusa_openrouter(-3600.0)) == 0.0


def test_cota_diaria_nao_gasta_o_orcamento_de_espera(monkeypatch):
    """
    No último elo, cota diária falha na hora — sem as quatro esperas.

    O orçamento de espera existe para o teto por minuto, que passa em segundos.
    Contra um limite que só vira no outro dia nenhuma tentativa tem chance, e
    insistir só entregava ao gestor a soma dos backoffs antes do mesmo
    LLM_UNAVAILABLE — cerca de 17s por chamada, multiplicados por LLM_CONCURRENCY.
    """
    dormidas = _sem_espera(monkeypatch)
    modelo = FakeModel(_recusa_openrouter())

    result = _run(
        _cliente(modelo, llm_rate_limit_retries=4).structured_generate(_prompt(), Resposta)
    )

    assert result.run.status == STATUS_UNAVAILABLE
    assert len(modelo.chamadas) == 1
    assert dormidas == []


def test_teto_por_minuto_continua_valendo_a_espera(monkeypatch):
    """
    A contraprova do teste acima: TPM não é TPD.

    Uma detecção de "diário" larga demais transformaria o limite por minuto —
    que passa sozinho em 31s e devolve a extração da dimensão — em
    LLM_UNAVAILABLE imediato.
    """
    dormidas = _sem_espera(monkeypatch)
    modelo = FakeModel(RateLimitError(_MENSAGEM_429), FakeMessage('{"notes":"depois da espera"}'))

    cliente = _cliente(modelo, llm_rate_limit_retries=2)
    result = _run(cliente.structured_generate(_prompt(), Resposta))

    assert result.ok
    assert len(dormidas) == 1


def test_cooldown_do_openrouter_sai_do_epoch_de_reposicao(monkeypatch):
    """Sem ler o epoch, a recusa do OpenRouter valia 5s de respiro — e voltava igual."""
    _sem_espera(monkeypatch)
    groq = FakeModel(_recusa_openrouter(7200.0))
    openrouter = FakeModel(FakeMessage('{"notes":"ok"}'))

    _run(
        _cliente_em_cadeia(groq, openrouter, llm_cooldown_max_s=1800.0).structured_generate(
            _prompt(), Resposta
        )
    )

    assert llm_client._cooldown_restante("groq") == pytest.approx(1800, abs=5)


def test_cota_diaria_sem_prazo_declarado_usa_o_teto_de_cooldown(monkeypatch):
    """
    Recusa que se diz diária mas não diz até quando: 5s de respiro é o palpite errado.

    Cinco segundos garantem que a próxima chamada colha a mesma recusa. Na falta de
    prazo do provedor, o teto de pular é o palpite honesto — pular não custa espera.
    """
    _sem_espera(monkeypatch)
    groq = FakeModel(RateLimitError("Rate limit exceeded: free-models-per-day"))
    openrouter = FakeModel(FakeMessage('{"notes":"ok"}'))

    _run(
        _cliente_em_cadeia(groq, openrouter, llm_cooldown_max_s=1800.0).structured_generate(
            _prompt(), Resposta
        )
    )

    assert llm_client._cooldown_restante("groq") > 1200


def test_primeira_leva_nao_colhe_a_mesma_recusa_em_coro():
    """
    Processo recém-subido, Groq em cota: **uma** chamada descobre isso, não quatro.

    Este é o caso que o cooldown sozinho não alcançava. Ele só é gravado quando a
    recusa *volta*, e com LLM_CONCURRENCY=3 as três chamadas já saíram antes disso
    — o log de produção mostrava três 429 idênticos do Groq em sequência, todos
    depois de "Application startup complete". Nada é semeado aqui de propósito: o
    estado começa vazio, como num backend que acabou de subir.
    """
    # Sem `_sem_espera` de propósito: ele troca o `asyncio.sleep` do módulo, e o
    # cenário abaixo precisa do sleep de verdade para ceder ao laço de eventos.
    # Não há espera a suprimir — com alternativa na cadeia, o 429 não espera.
    liberar = asyncio.Event()

    class GroqLento:
        def __init__(self):
            self.chamadas = 0

        async def ainvoke(self, messages):
            self.chamadas += 1
            await liberar.wait()
            raise RateLimitError(_MENSAGEM_429)

    class OpenRouterOk:
        def __init__(self):
            self.chamadas = 0

        async def ainvoke(self, messages):
            self.chamadas += 1
            return FakeMessage('{"notes":"fallback"}')

    groq, openrouter = GroqLento(), OpenRouterOk()
    cliente = _cliente_em_cadeia(groq, openrouter)

    async def cenario():
        sonda = asyncio.create_task(cliente.structured_generate(_prompt(), Resposta))
        await asyncio.sleep(0)  # deixa a sonda chegar ao await de rede
        irmas = [
            asyncio.create_task(cliente.structured_generate(_prompt(), Resposta))
            for _ in range(3)
        ]
        await asyncio.sleep(0)  # deixa as irmãs chegarem ao await da sondagem
        liberar.set()
        return [await sonda, *[await irma for irma in irmas]]

    resultados = asyncio.run(cenario())

    assert all(r.ok for r in resultados)
    assert groq.chamadas == 1  # uma sonda, não quatro
    assert openrouter.chamadas == 4
    # As irmãs não passaram pelo Groq: acordaram com o cooldown já gravado.
    assert all(
        any("pulado" in str(d.get("error")) for d in r.attempts_detail)
        for r in resultados[1:]
    )


def test_provedor_que_responde_nao_serializa_as_chamadas_seguintes():
    """
    A sondagem vale enquanto o estado é desconhecido, e só.

    Depois que o provedor respondeu uma vez, a concorrência volta a correr solta.
    Sem isto, o mecanismo que economiza chamadas num provedor morto passaria a
    somar latência num provedor vivo — LLM_CONCURRENCY=3 viraria 1 na prática.
    """

    class GroqQueConta:
        def __init__(self):
            self.chamadas = []
            self.em_voo = 0
            self.pico = 0
            self.liberar = asyncio.Event()

        async def ainvoke(self, messages):
            self.chamadas.append(messages)
            self.em_voo += 1
            self.pico = max(self.pico, self.em_voo)
            await self.liberar.wait()
            self.em_voo -= 1
            return FakeMessage('{"notes":"ok"}')

    groq = GroqQueConta()
    cliente = _cliente_em_cadeia(groq, FakeModel())

    async def cenario():
        groq.liberar.set()
        await cliente.structured_generate(_prompt(), Resposta)  # a sonda
        groq.liberar.clear()

        trio = [
            asyncio.create_task(cliente.structured_generate(_prompt(), Resposta))
            for _ in range(3)
        ]
        for _ in range(3):
            await asyncio.sleep(0)  # deixa as três chegarem ao await de rede
        groq.liberar.set()
        return await asyncio.gather(*trio)

    resultados = asyncio.run(cenario())

    assert all(r.ok for r in resultados)
    assert groq.pico == 3  # as três em voo ao mesmo tempo, não enfileiradas


def test_espera_de_cota_sobrevive_ao_reinicio(monkeypatch, tmp_path):
    """
    Reiniciar o backend não devolve ao provedor em cota o crédito de ser tentado.

    O log de produção começava em "Application startup complete" e logo depois
    gastava chamadas num Groq que estava em cota diária havia 40 minutos —
    informação que o processo anterior tinha e perdeu ao morrer. A janela é
    gravada em instante absoluto justamente porque o relógio monotônico não
    atravessa reinício.
    """
    _sem_espera(monkeypatch)
    estado = tmp_path / "reinicio.json"

    groq = FakeModel(RateLimitError(_MENSAGEM_COTA_DIARIA))
    _run(
        _cliente_em_cadeia(
            groq, FakeModel(FakeMessage('{"notes":"ok"}')), llm_cooldown_state_path=estado
        ).structured_generate(_prompt(), Resposta)
    )
    assert estado.exists()

    # Reinício: tudo o que era memória do processo se perde.
    llm_client.reset_provider_cooldowns()
    assert llm_client._cooldown_restante("groq") == 0.0

    groq_novo = FakeModel(FakeMessage('{"notes":"não deveria ser chamado"}'))
    openrouter = FakeModel(FakeMessage('{"notes":"ok"}'))
    result = _run(
        _cliente_em_cadeia(
            groq_novo, openrouter, llm_cooldown_state_path=estado
        ).structured_generate(_prompt(), Resposta)
    )

    assert result.ok and result.run.provider == "openrouter"
    assert groq_novo.chamadas == []  # nem uma chamada gasta no provedor em cota


def test_janela_vencida_no_disco_nao_bloqueia_o_provedor(monkeypatch, tmp_path):
    """O registro expira sozinho: cache que não se limpa vira provedor desligado."""
    _sem_espera(monkeypatch)
    estado = tmp_path / "vencido.json"
    estado.write_text('{"groq": 1}', encoding="utf-8")  # epoch de 1970

    groq = FakeModel(FakeMessage('{"notes":"groq atendeu"}'))
    result = _run(
        _cliente_em_cadeia(
            groq, FakeModel(), llm_cooldown_state_path=estado
        ).structured_generate(_prompt(), Resposta)
    )

    assert result.ok and result.run.provider == "groq"


def test_estado_de_cota_corrompido_nao_derruba_a_chamada(monkeypatch, tmp_path):
    """É cache, não fonte de verdade: erro de leitura volta a ser só memória."""
    _sem_espera(monkeypatch)
    estado = tmp_path / "corrompido.json"
    estado.write_text("{isto não é json", encoding="utf-8")

    groq = FakeModel(FakeMessage('{"notes":"groq atendeu"}'))
    result = _run(
        _cliente_em_cadeia(
            groq, FakeModel(), llm_cooldown_state_path=estado
        ).structured_generate(_prompt(), Resposta)
    )

    assert result.ok and result.run.provider == "groq"
