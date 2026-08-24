"""
Contrato da camada de LLM: prompts versionados e saída validada (§25, §28.2, §35).

O que estes testes fixam é a fronteira da §2.1: a LLM entrega texto que precisa
caber num schema, e o que não couber **não vira dado**. Nenhum caminho aqui
transforma resposta malformada em valor aceito.

O modelo é substituído por um duplo em todos os casos — a suíte não depende de
rede nem de chave de API.
"""

import asyncio
from dataclasses import replace

import pytest
from pydantic import BaseModel

from config import get_settings
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
    base = get_settings()
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
