"""
Refinamento das consultas do RAG pelo Bloco E (§4.5.1).

A §4.5.1 dá ao Bloco E uma função e um limite, e os dois precisam de teste:

    função — "interpretadas pela LLM e associadas aos indicadores previamente
    definidos, contribuindo para o refinamento das consultas utilizadas pelo
    mecanismo RAG";

    limite — "não alteram os pesos das dimensões calculados pelo AHP nem os pesos
    locais dos indicadores obtidos a partir dos Blocos A, B e C".

O limite é estrutural: a função devolve termos de busca, e termos de busca não
têm por onde virar peso. O que estes testes protegem é a estrutura permanecer
assim — e o que entra na consulta continuar sendo um refinamento, não uma
substituição do critério da pesquisa.
"""

import asyncio

import pytest

import query_refinement
import rag
from domain.methodology import load_methodology
from guardrails import GuardrailLog
from llm.client import LLMRunRecord, StructuredResult

TEXTO = [
    {"pergunta": "Requisitos de segurança?", "resposta": "Exigimos replicação geográfica."},
]


@pytest.fixture
def metodologia():
    return load_methodology()


@pytest.fixture
def indicadores(metodologia):
    return [
        metodologia.by_id("security_backup_recovery"),
        metodologia.by_id("performance_availability"),
    ]


class _FakeLLM:
    def __init__(self, payload, status="OK"):
        self._payload = payload
        self._status = status
        self.prompt = None

    async def structured_generate(self, prompt, schema, max_attempts=2):
        self.prompt = prompt
        run = LLMRunRecord(
            run_id="r1", prompt_id=prompt.prompt_id, prompt_version=prompt.prompt_version,
            provider="fake", model="fake", status=self._status, latency_ms=1,
            attempts=1, input_hash="h",
        )
        if self._status != "OK":
            return StructuredResult(run=run, data=None, raw_text="")
        return StructuredResult(run=run, data=schema(**self._payload), raw_text="")


def _refinar(monkeypatch, indicadores, payload, status="OK", qa=TEXTO, log=None):
    # Cache desligado: estes testes verificam o que a LLM recebe e o que o código
    # faz com a resposta dela. Com o cache ligado, uma leitura guardada de outra
    # execução responderia antes da chamada e o `fake` nunca veria o prompt —
    # o teste passaria a medir o cache em vez do refinamento.
    monkeypatch.setenv("LLM_CACHE_ENABLED", "false")
    from config import reload_settings

    reload_settings()
    fake = _FakeLLM(payload, status)
    monkeypatch.setattr(query_refinement, "get_llm_client", lambda: fake)
    hints, run = asyncio.run(
        query_refinement.refine_queries(
            qa_pairs=qa,
            indicators=indicadores,
            guardrail_log=log if log is not None else GuardrailLog(),
        )
    )
    return hints, run, fake


# --- Função: os termos chegam à consulta -----------------------------------


def test_termos_do_gestor_entram_na_consulta(monkeypatch, indicadores, metodologia):
    payload = {
        "refinements": [
            {"indicator_id": "security_backup_recovery", "terms": ["replicação geográfica"]}
        ]
    }
    hints, _, _ = _refinar(monkeypatch, indicadores, payload)
    assert hints["security_backup_recovery"] == ("replicação geográfica",)

    consulta = rag.query_for_indicator(
        metodologia.by_id("security_backup_recovery"), "AWS", hints["security_backup_recovery"]
    )
    assert "replicação geográfica" in consulta


def test_termos_da_pesquisa_vem_antes_dos_do_gestor(metodologia):
    """
    §4.5.1 fala em refinamento, não em substituição. O vetor da consulta é a
    média do que está nela: os termos do Quadro 27 precisam vir primeiro e
    sempre, ou "refinar" viraria "trocar o critério".
    """
    indicador = metodologia.by_id("security_backup_recovery")
    consulta = rag.query_for_indicator(indicador, None, ["replicação geográfica"])

    assert consulta.index(indicador.name) == 0
    assert consulta.index("backup") < consulta.index("replicação geográfica")


def test_consulta_sem_refinamento_continua_valida(metodologia):
    indicador = metodologia.by_id("performance_availability")
    assert rag.query_for_indicator(indicador, "AWS") == rag.query_for_indicator(
        indicador, "AWS", ()
    )


def test_sem_texto_livre_a_llm_nao_e_chamada(monkeypatch, indicadores):
    """Sem requisito descrito não há o que refinar — e nada a inventar."""
    chamou = []

    class Cliente:
        async def structured_generate(self, prompt, schema, max_attempts=2):
            chamou.append(prompt)
            raise AssertionError("não deveria chamar a LLM sem texto do gestor")

    monkeypatch.setattr(query_refinement, "get_llm_client", lambda: Cliente())
    hints, run = asyncio.run(
        query_refinement.refine_queries(
            qa_pairs=[{"pergunta": "Requisitos?", "resposta": "   "}],
            indicators=indicadores,
        )
    )
    assert hints == {} and run is None and not chamou


# --- Limite: nada disso vira peso ------------------------------------------


def test_schema_nao_tem_onde_escrever_peso():
    """
    §4.5.1: o Bloco E não altera pesos. A garantia é a forma do dado — não há
    campo de peso, prioridade ou relevância na saída deste prompt.
    """
    from llm.schemas import IndicatorQueryHint

    campos = set(IndicatorQueryHint.model_fields)
    assert campos == {"indicator_id", "terms"}
    assert not campos & {"weight", "peso", "priority", "relevance", "score"}


def test_refinamento_devolve_apenas_termos(monkeypatch, indicadores):
    """Campos extras inventados pela LLM não sobrevivem ao schema."""
    payload = {
        "refinements": [
            {
                "indicator_id": "security_backup_recovery",
                "terms": ["RPO"],
                "weight": 0.9,
                "priority": "máxima",
            }
        ]
    }
    hints, _, _ = _refinar(monkeypatch, indicadores, payload)
    assert hints == {"security_backup_recovery": ("RPO",)}


# --- Validação da saída -----------------------------------------------------


def test_indicador_fora_da_lista_e_descartado(monkeypatch, indicadores):
    """§4.4: os critérios são previamente definidos; a LLM não cria indicador."""
    log = GuardrailLog()
    payload = {"refinements": [{"indicator_id": "indicador_inventado", "terms": ["x"]}]}
    hints, _, _ = _refinar(monkeypatch, indicadores, payload, log=log)

    assert hints == {}
    assert any(e["rule_id"] == "QUERY_REFINEMENT_UNKNOWN_INDICATOR" for e in log.as_dicts())


def test_termo_longo_demais_e_descartado(monkeypatch, indicadores):
    """
    Um "termo" de duzentos caracteres não é termo: é o texto do gestor
    reaparecendo dentro do vetor da consulta, com peso desproporcional.
    """
    log = GuardrailLog()
    longo = "replicação " * 30
    payload = {
        "refinements": [
            {"indicator_id": "security_backup_recovery", "terms": [longo, "RPO de 15 minutos"]}
        ]
    }
    hints, _, _ = _refinar(monkeypatch, indicadores, payload, log=log)

    assert hints == {"security_backup_recovery": ("RPO de 15 minutos",)}
    assert any(e["rule_id"] == "QUERY_REFINEMENT_TERM_TOO_LONG" for e in log.as_dicts())
    # A amostra registrada é truncada — o log não vira cópia do texto do gestor.
    evento = next(e for e in log.as_dicts() if e["rule_id"] == "QUERY_REFINEMENT_TERM_TOO_LONG")
    assert len(evento["masked_sample"]) <= 45


def test_excesso_de_termos_e_cortado(monkeypatch, indicadores):
    payload = {
        "refinements": [
            {
                "indicator_id": "security_backup_recovery",
                "terms": ["a", "b", "c", "d", "e", "f"],
            }
        ]
    }
    hints, _, _ = _refinar(monkeypatch, indicadores, payload)
    assert len(hints["security_backup_recovery"]) == query_refinement.MAX_TERMS_PER_INDICATOR


def test_termos_repetidos_sao_deduplicados(monkeypatch, indicadores):
    payload = {
        "refinements": [
            {"indicator_id": "security_backup_recovery", "terms": ["RPO", "rpo", " RPO "]}
        ]
    }
    hints, _, _ = _refinar(monkeypatch, indicadores, payload)
    assert hints["security_backup_recovery"] == ("RPO",)


def test_lista_vazia_e_resposta_valida(monkeypatch, indicadores):
    """O gestor pode não ter escrito nada aplicável — isso não é erro."""
    hints, run, _ = _refinar(monkeypatch, indicadores, {"refinements": []})
    assert hints == {}
    assert run is not None and run.status == "OK"


def test_falha_da_llm_nao_bloqueia_a_avaliacao(monkeypatch, indicadores):
    """Sem refinamento as buscas seguem com os termos da pesquisa."""
    log = GuardrailLog()
    hints, run, _ = _refinar(
        monkeypatch, indicadores, {"refinements": []}, status="LLM_UNAVAILABLE", log=log
    )
    assert hints == {}
    assert run.status == "LLM_UNAVAILABLE"
    assert any(e["rule_id"] == "QUERY_REFINEMENT_UNAVAILABLE" for e in log.as_dicts())


# --- Prompt -----------------------------------------------------------------


def test_texto_do_gestor_vai_encapsulado(monkeypatch, indicadores):
    """§24: conteúdo de terceiro entra como dado, nunca como instrução."""
    _, _, fake = _refinar(monkeypatch, indicadores, {"refinements": []})
    assert "<USER_CONTEXT>" in fake.prompt.user
    assert "</USER_CONTEXT>" in fake.prompt.user
    assert "replicação geográfica" in fake.prompt.user


def test_prompt_nao_recebe_peso_nem_ranking(monkeypatch, indicadores):
    _, _, fake = _refinar(monkeypatch, indicadores, {"refinements": []})
    for termo in ("PESOS DAS DIMENSÕES", "ranking", "pontuação"):
        assert termo not in fake.prompt.user


def test_prompt_lista_somente_os_indicadores_com_peso(monkeypatch, indicadores):
    _, _, fake = _refinar(monkeypatch, indicadores, {"refinements": []})
    assert "security_backup_recovery" in fake.prompt.user
    assert "performance_availability" in fake.prompt.user
    # Indicador sem peso não é oferecido ao modelo.
    assert "sustainability_energy_efficiency" not in fake.prompt.user


def test_prompt_de_refinamento_esta_registrado():
    from llm.prompts import registered_versions

    assert registered_versions()["PROMPT_QUERY_REFINEMENT_V1"] == "1"
