"""
Extração das evidências documentais (§4.4.1, §19 e Quadro 26).

O que estes testes protegem é a fronteira entre o componente probabilístico e o
determinístico. A LLM pode errar de todas as formas — citar indicador que não
existe, inventar identificador de trecho, escolher categoria fora da rubrica,
omitir metade da lista, devolver texto no lugar de número. Nenhum desses erros
pode virar desempenho de provedor; todos precisam virar `INVALID` ou
`NOT_FOUND`, com motivo registrado.

O caso mais sutil é o das unidades: dois provedores publicando "90 %" e "0,9"
produzem uma comparação que roda sem erro e não significa nada. A cobertura
disso está em `test_unidades_divergentes_*`.
"""

import asyncio
import json

import pytest

import evidence
from domain.methodology import load_methodology
from domain.normalization import (
    STATUS_FOUND,
    STATUS_INVALID,
    STATUS_NOT_FOUND,
    STATUS_PARTIAL,
    build_comparability_set,
    renormalize_weights,
)
from domain.scoring import compute_scores
from guardrails import GuardrailLog
from llm.client import LLMRunRecord, StructuredResult
from llm.schemas import IndicatorEvidence

PROVEDORES = [
    {"id": "aws", "name": "AWS"},
    {"id": "gcp", "name": "Google Cloud"},
]


@pytest.fixture
def metodologia():
    return load_methodology()


@pytest.fixture
def disponibilidade(metodologia):
    """Indicador quantitativo de benefício."""
    return metodologia.by_id("performance_availability")


@pytest.fixture
def certificacoes(metodologia):
    """Indicador qualitativo com rubrica ordinal."""
    return metodologia.by_id("security_certifications")


def _chunk(chunk_id="chunk-1", provider="aws"):
    return {
        "page_content": "Disponibilidade mensal de 99,99% conforme SLA.",
        "score": 0.2,
        "chunk_id": chunk_id,
        "document_id": f"doc-{provider}",
        "file_name": f"{provider}.pdf",
        "page": 3,
        "provider": provider,
    }


def _bruto(**kwargs):
    base = {
        "indicator_id": "performance_availability",
        "evidence_status": "FOUND",
        "nature": "quantitative",
        "value": 99.99,
        "unit": "%",
        "category": None,
        "summary": "SLA de 99,99%.",
        "source_chunk_id": "chunk-1",
        "source_document": "aws.pdf",
    }
    base.update(kwargs)
    return IndicatorEvidence(**base)


def _validar(bruto, indicator, metodologia, chunks=("chunk-1",), log=None):
    # `log is not None`, e não `log or ...`: GuardrailLog define __len__, então
    # um log vazio é falsy e o atalho criaria um segundo log, engolindo os
    # eventos que o teste veio conferir.
    return evidence._validate_finding(
        bruto,
        indicator,
        "aws",
        list(chunks),
        metodologia,
        log if log is not None else GuardrailLog(),
    )


# --- Consulta por indicador (§16 e Quadro 27) -------------------------------


def test_consulta_usa_o_nome_e_os_termos_do_indicador(disponibilidade):
    import rag

    consulta = rag.query_for_indicator(disponibilidade, "AWS")
    assert consulta.startswith("AWS: ")
    assert "Disponibilidade dos serviços" in consulta
    # Termos do Quadro 27, que vivem no indicators.json e não no código.
    assert "uptime" in consulta and "SLA" in consulta


def test_consulta_nao_despeja_a_lista_inteira_de_termos(metodologia):
    """Consulta com 15 siglas aponta para o assunto, não para o indicador."""
    import rag
    from rag.queries import MAX_SEARCH_TERMS

    indicador = metodologia.by_id("sustainability_carbon_emissions")
    assert len(indicador.search_terms) > MAX_SEARCH_TERMS
    consulta = rag.query_for_indicator(indicador)
    assert consulta.count(",") <= MAX_SEARCH_TERMS


# --- Validação da saída da LLM (§19) ---------------------------------------


def test_evidencia_quantitativa_valida_vira_desempenho(disponibilidade, metodologia):
    finding = _validar(_bruto(), disponibilidade, metodologia)
    assert finding.status == STATUS_FOUND
    assert finding.value == pytest.approx(99.99)
    assert finding.unit == "%"


def test_categoria_qualitativa_e_convertida_pela_rubrica(certificacoes, metodologia):
    """§10.1: quem transforma categoria em número é a rubrica, não a LLM."""
    bruto = _bruto(
        indicator_id="security_certifications",
        nature="qualitative",
        value=None,
        unit=None,
        category="level_3",
    )
    finding = _validar(bruto, certificacoes, metodologia)
    assert finding.status == STATUS_FOUND
    assert finding.value == pytest.approx(0.75)  # ordinal_4 → level_3
    assert finding.category == "level_3"


def test_categoria_fora_da_rubrica_e_recusada(certificacoes, metodologia):
    log = GuardrailLog()
    bruto = _bruto(
        indicator_id="security_certifications",
        nature="qualitative",
        value=None,
        category="excelente",
    )
    finding = _validar(bruto, certificacoes, metodologia, log=log)

    assert finding.status == STATUS_INVALID
    assert finding.value is None
    assert "excelente" in finding.rejection
    assert any(e["rule_id"] == "EVIDENCE_CATEGORY_NOT_ALLOWED" for e in log.as_dicts())


def test_chunk_inventado_invalida_a_evidencia(disponibilidade, metodologia):
    """§19: a fonte precisa estar entre os trechos efetivamente fornecidos."""
    log = GuardrailLog()
    finding = _validar(
        _bruto(source_chunk_id="chunk-inexistente"),
        disponibilidade,
        metodologia,
        log=log,
    )
    assert finding.status == STATUS_INVALID
    assert any(e["rule_id"] == "EVIDENCE_SOURCE_NOT_PROVIDED" for e in log.as_dicts())


def test_quantitativo_sem_valor_nao_vira_zero(disponibilidade, metodologia):
    """Ausência de número é ausência, não desempenho nulo."""
    finding = _validar(_bruto(value=None), disponibilidade, metodologia)
    assert finding.status == STATUS_INVALID
    assert finding.value is None


def test_marcador_textual_de_ausencia_e_lido_como_nulo():
    """"não identificado" no lugar do número não derruba a resposta inteira."""
    bruto = IndicatorEvidence(
        indicator_id="performance_availability",
        evidence_status="NOT_FOUND",
        nature="insufficient",
        value="não identificado",
        summary="não identificado nas fontes recuperadas",
    )
    assert bruto.value is None


def test_not_found_preserva_a_frase_da_dissertacao(disponibilidade, metodologia):
    bruto = _bruto(
        evidence_status="NOT_FOUND",
        nature="insufficient",
        value=None,
        summary="não identificado nas fontes recuperadas",
    )
    finding = _validar(bruto, disponibilidade, metodologia)
    assert finding.status == STATUS_NOT_FOUND
    assert finding.summary == "não identificado nas fontes recuperadas"


def test_partial_fica_fora_do_conjunto_comparavel(disponibilidade, metodologia):
    """§29.2: sem decisão acadêmica, PARTIAL não vira meio ponto."""
    assert metodologia.partial_counts_as_comparable is False
    finding = _validar(_bruto(evidence_status="PARTIAL"), disponibilidade, metodologia)
    assert finding.status == STATUS_PARTIAL
    assert finding.to_performance().is_usable is False


# --- Comparabilidade entre alternativas (§4.4.1.1) --------------------------


def test_unidades_divergentes_tiram_o_indicador_da_avaliacao(metodologia):
    log = GuardrailLog()
    findings = [
        evidence.Finding("aws", "performance_availability", "performance", STATUS_FOUND, value=99.9, unit="%"),
        evidence.Finding("gcp", "performance_availability", "performance", STATUS_FOUND, value=0.999, unit="ratio"),
    ]
    ajustados = evidence.enforce_unit_consistency(findings, metodologia, log)

    assert {f.status for f in ajustados} == {STATUS_INVALID}
    assert any(e["rule_id"] == "EVIDENCE_UNIT_MISMATCH" for e in log.as_dicts())


def test_unidades_equivalentes_nao_invalidam_nada(metodologia):
    findings = [
        evidence.Finding("aws", "performance_availability", "performance", STATUS_FOUND, value=99.9, unit="%"),
        evidence.Finding("gcp", "performance_availability", "performance", STATUS_FOUND, value=99.5, unit="percent"),
    ]
    ajustados = evidence.enforce_unit_consistency(findings, metodologia, GuardrailLog())
    assert {f.status for f in ajustados} == {STATUS_FOUND}


def test_qualitativo_nao_e_afetado_pela_regra_de_unidade(metodologia):
    findings = [
        evidence.Finding("aws", "security_certifications", "security", STATUS_FOUND, value=1.0, unit="certificações"),
        evidence.Finding("gcp", "security_certifications", "security", STATUS_FOUND, value=0.75, unit="norma"),
    ]
    ajustados = evidence.enforce_unit_consistency(findings, metodologia, GuardrailLog())
    assert {f.status for f in ajustados} == {STATUS_FOUND}


# --- Ponta a ponta: evidência → ranking -------------------------------------


class _FakeLLM:
    """Cliente de LLM controlado: devolve o payload que o teste definir."""

    def __init__(self, payload, status="OK"):
        self._payload = payload
        self._status = status
        self.chamadas = []

    async def structured_generate(self, prompt, schema, max_attempts=2):
        self.chamadas.append(prompt)
        run = LLMRunRecord(
            run_id="r1",
            prompt_id=prompt.prompt_id,
            prompt_version=prompt.prompt_version,
            provider="fake",
            model="fake",
            status=self._status,
            latency_ms=1,
            attempts=1,
            input_hash="h",
        )
        if self._status != "OK":
            return StructuredResult(run=run, data=None, raw_text="")
        return StructuredResult(run=run, data=schema(**self._payload), raw_text=json.dumps(self._payload))


def _rodar_extracao(monkeypatch, metodologia, indicadores, payload, status="OK"):
    fake = _FakeLLM(payload, status)
    monkeypatch.setattr(evidence, "get_llm_client", lambda: fake)
    monkeypatch.setattr(
        evidence.rag,
        "search",
        lambda query, top_k=None, session_id=None, provider_id=None: [
            _chunk(f"chunk-{provider_id}", provider_id)
        ],
    )
    resultado = asyncio.run(
        evidence.extract_performances(
            providers=PROVEDORES,
            indicators=indicadores,
            methodology=metodologia,
            guardrail_log=GuardrailLog(),
        )
    )
    return resultado, fake


def test_desempenho_extraido_alimenta_o_ranking(monkeypatch, metodologia, disponibilidade):
    """
    O teste que a lacuna do relatório de conformidade descreve: o valor sai do
    documento, passa pela normalização da Equação 1 e chega à Equação 5.
    """
    payload = {
        "findings": [
            {
                "indicator_id": "performance_availability",
                "evidence_status": "FOUND",
                "nature": "quantitative",
                "value": 99.99,
                "unit": "%",
                "summary": "SLA de 99,99%.",
                "source_chunk_id": "chunk-aws",
            }
        ]
    }
    extraction, _ = _rodar_extracao(monkeypatch, metodologia, [disponibilidade], payload)

    # O `source_chunk_id` do payload só existe para a AWS; para o GCP a citação
    # aponta para fora do contexto e a evidência é recusada, como manda a §19.
    aws = next(f for f in extraction.findings if f.provider_id == "aws")
    assert aws.status == STATUS_FOUND
    assert aws.value == pytest.approx(99.99)

    conjunto = build_comparability_set(
        extraction.performances(), ["aws", "gcp"], ["performance_availability"], metodologia
    )
    # Sem valor válido para todos, o indicador sai da comparação (§11.1) — e o
    # ranking não inventa nota para o provedor que ficou sem evidência.
    assert conjunto.valid == ()
    assert conjunto.excluded["performance_availability"] == "missing_for_some_providers"


def test_ranking_completo_quando_todos_tem_evidencia(monkeypatch, metodologia, disponibilidade):
    class Cliente:
        """Cada provedor publica um valor diferente, lido do próprio prompt."""

        async def structured_generate(self, prompt, schema, max_attempts=2):
            provedor = "aws" if "AWS" in prompt.user else "gcp"
            valor = 99.99 if provedor == "aws" else 99.50
            run = LLMRunRecord(
                run_id="r", prompt_id=prompt.prompt_id, prompt_version="1", provider="fake",
                model="fake", status="OK", latency_ms=1, attempts=1, input_hash="h",
            )
            dados = schema(
                findings=[
                    IndicatorEvidence(
                        indicator_id="performance_availability",
                        evidence_status="FOUND",
                        nature="quantitative",
                        value=valor,
                        unit="%",
                        summary=f"SLA de {valor}%.",
                        source_chunk_id=f"chunk-{provedor}",
                    )
                ]
            )
            return StructuredResult(run=run, data=dados, raw_text="")

    monkeypatch.setattr(evidence, "get_llm_client", lambda: Cliente())
    monkeypatch.setattr(
        evidence.rag,
        "search",
        lambda query, top_k=None, session_id=None, provider_id=None: [
            _chunk(f"chunk-{provider_id}", provider_id)
        ],
    )

    extraction = asyncio.run(
        evidence.extract_performances(
            providers=PROVEDORES,
            indicators=[disponibilidade],
            methodology=metodologia,
            guardrail_log=GuardrailLog(),
        )
    )

    conjunto = build_comparability_set(
        extraction.performances(), ["aws", "gcp"], ["performance_availability"], metodologia
    )
    assert conjunto.valid == ("performance_availability",)

    efetivos = renormalize_weights({"performance_availability": 0.4}, conjunto.valid)
    assert efetivos["performance_availability"] == pytest.approx(1.0)

    resultado = compute_scores(PROVEDORES, conjunto, efetivos, metodologia)
    primeiro, segundo = resultado.scores
    # Equação 1 (benefício): r = x / max(x). A AWS publica o maior valor.
    assert primeiro.provider_id == "aws"
    assert primeiro.score == pytest.approx(1.0)
    assert segundo.score == pytest.approx(99.50 / 99.99)


def test_indicador_omitido_pela_llm_vira_sem_evidencia(monkeypatch, metodologia, disponibilidade, certificacoes):
    """Omissão não é zero, e a lacuna fica registrada."""
    extraction, _ = _rodar_extracao(
        monkeypatch, metodologia, [disponibilidade, certificacoes], {"findings": []}
    )
    assert {f.status for f in extraction.findings} == {STATUS_NOT_FOUND}
    assert all(f.value is None for f in extraction.findings)


def test_indicador_fora_da_lista_e_descartado(monkeypatch, metodologia, disponibilidade):
    """Regra 3 do prompt: a LLM não define o que é avaliado."""
    payload = {
        "findings": [
            {
                "indicator_id": "indicador_inventado",
                "evidence_status": "FOUND",
                "nature": "quantitative",
                "value": 100.0,
                "summary": "…",
                "source_chunk_id": "chunk-aws",
            }
        ]
    }
    extraction, _ = _rodar_extracao(monkeypatch, metodologia, [disponibilidade], payload)
    assert {f.indicator_id for f in extraction.findings} == {"performance_availability"}
    assert {f.status for f in extraction.findings} == {STATUS_NOT_FOUND}


def test_llm_indisponivel_nao_produz_desempenho(monkeypatch, metodologia, disponibilidade):
    """§26: falha de extração vira ausência declarada, nunca valor presumido."""
    extraction, _ = _rodar_extracao(
        monkeypatch, metodologia, [disponibilidade], {"findings": []}, status="LLM_UNAVAILABLE"
    )
    assert {f.status for f in extraction.findings} == {STATUS_NOT_FOUND}
    assert all("indisponível" in (f.rejection or "").lower() for f in extraction.findings)


def test_sem_trecho_recuperado_a_llm_nao_e_chamada(monkeypatch, metodologia, disponibilidade):
    """Sem contexto documental não há o que interpretar — e nada a inventar."""
    chamou = []

    class Cliente:
        async def structured_generate(self, prompt, schema, max_attempts=2):
            chamou.append(prompt)
            raise AssertionError("não deveria chamar a LLM sem trechos")

    monkeypatch.setattr(evidence, "get_llm_client", lambda: Cliente())
    monkeypatch.setattr(
        evidence.rag, "search", lambda *a, **k: []
    )
    extraction = asyncio.run(
        evidence.extract_performances(
            providers=PROVEDORES,
            indicators=[disponibilidade],
            methodology=metodologia,
            guardrail_log=GuardrailLog(),
        )
    )
    assert not chamou
    assert {f.status for f in extraction.findings} == {STATUS_NOT_FOUND}


# --- Prompt (Quadro 26) -----------------------------------------------------


def test_prompt_lista_as_categorias_permitidas(certificacoes, disponibilidade):
    texto = evidence.describe_indicators([certificacoes, disponibilidade])
    assert "categorias permitidas: level_1, level_2, level_3, level_4" in texto
    assert "quantitativo" in texto and "qualitativo" in texto


def test_prompt_de_extracao_esta_registrado():
    from llm.prompts import registered_versions

    assert registered_versions()["PROMPT_EVIDENCE_EXTRACTION_V1"] == "1"
