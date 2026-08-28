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


def _stub_rag(monkeypatch, modulo, por_consulta):
    """
    Substitui a recuperação nos dois pontos de entrada.

    A busca passou a ser em lote (`search_many`) para embutir as 39 consultas de
    uma vez. Substituir só `search` deixaria o teste passar por um caminho que a
    aplicação não usa mais; substituir os dois mantém o teste sobre o código real
    e preserva a intenção de cada stub — `por_consulta(provider_id)` devolve o que
    aquela consulta recupera.
    """
    monkeypatch.setattr(
        modulo.rag,
        "search",
        lambda query, top_k=None, session_id=None, provider_id=None: por_consulta(provider_id),
    )
    monkeypatch.setattr(
        modulo.rag,
        "search_many",
        lambda consultas, top_k=None, session_id=None: [
            por_consulta(provider_id) for _, provider_id in consultas
        ],
    )


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
    # `chunks` são os chunk_id entregues. O mapa reproduz o que o produtor monta:
    # o rótulo curto do bloco (T1, T2…) e o próprio chunk_id resolvem para o
    # chunk_id real.
    fontes = {}
    for indice, real in enumerate(chunks):
        fontes[f"T{indice + 1}"] = real
        fontes[real] = real
    # `log is not None`, e não `log or ...`: GuardrailLog define __len__, então
    # um log vazio é falsy e o atalho criaria um segundo log, engolindo os
    # eventos que o teste veio conferir.
    return evidence._validate_finding(
        bruto,
        indicator,
        "aws",
        fontes,
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


def test_consulta_leva_todos_os_termos_do_quadro_27(metodologia):
    """
    §5.2: os termos do Quadro 27 são "elementos orientadores na construção das
    consultas". A diretriz não prevê recorte, e uma versão anterior truncava em 8.
    """
    import rag

    indicador = metodologia.by_id("sustainability_carbon_emissions")
    assert len(indicador.search_terms) > 8  # o caso que o recorte anterior afetava
    consulta = rag.query_for_indicator(indicador)
    for termo in indicador.search_terms:
        assert termo in consulta


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
        category="alto",
    )
    finding = _validar(bruto, certificacoes, metodologia)
    assert finding.status == STATUS_FOUND
    assert finding.value == pytest.approx(0.75)  # Quadro 23: "alto" → 0,75
    assert finding.category == "alto"


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


def test_partial_sem_valor_e_resposta_valida_e_nao_violacao(disponibilidade, metodologia):
    """
    A regra 12 do prompt manda usar PARTIAL **sem valor** para meta futura ou
    recorte parcial. Tratar isso como INVALID acusaria o modelo de violar a
    regra que ele cumpriu — e enchia o log de guardrail de rejeição falsa.
    """
    log = GuardrailLog()
    bruto = _bruto(
        evidence_status="PARTIAL",
        value=None,
        unit=None,
        summary="Compromisso de 100% de energia renovável até 2025.",
    )
    finding = _validar(bruto, disponibilidade, metodologia, log=log)

    assert finding.status == STATUS_PARTIAL
    assert finding.value is None
    assert finding.rejection is None
    assert log.as_dicts() == []
    # A fonte fica: o relatório precisa mostrar em que trecho o modelo se apoiou.
    assert finding.source_chunk_id == "chunk-1"


def test_qualitativo_partial_sem_categoria_nao_e_recusado(certificacoes, metodologia):
    log = GuardrailLog()
    bruto = _bruto(
        indicator_id=certificacoes.id,
        evidence_status="PARTIAL",
        nature="qualitative",
        value=None,
        unit=None,
        category=None,
        summary="O documento cita auditorias, sem dizer quais certificações.",
    )
    finding = _validar(bruto, certificacoes, metodologia, log=log)

    assert finding.status == STATUS_PARTIAL
    assert finding.category is None
    assert log.as_dicts() == []


def test_quantitativo_sem_valor_declarado_found_continua_invalido(
    disponibilidade, metodologia
):
    """A tolerância acima é do PARTIAL. Dizer FOUND sem número segue fora das regras."""
    log = GuardrailLog()
    finding = _validar(_bruto(value=None), disponibilidade, metodologia, log=log)
    assert finding.status == STATUS_INVALID
    assert any(e["rule_id"] == "EVIDENCE_MISSING_VALUE" for e in log.as_dicts())


# --- Unidade conforme o indicador (§5.4 e Quadro 22) ------------------------


def test_unidade_fora_das_esperadas_e_recusada(metodologia):
    """
    §5.4: a validação da saída evita que "informações fora do formato esperado
    sejam incorporadas ao processo de avaliação". A unidade é parte do formato.
    """
    log = GuardrailLog()
    # A eficiência energética é o veículo do teste porque continua quantitativa
    # com lista de unidades (ratio, PUE). O carbono servia aqui antes de virar
    # qualitativo — e um indicador sem `expected_units` aceita qualquer unidade,
    # então o teste passaria sem exercer a regra.
    eficiencia = metodologia.by_id("sustainability_energy_efficiency")
    bruto = _bruto(
        indicator_id="sustainability_energy_efficiency",
        value=12000.0,
        unit="tCO2e",
    )
    finding = _validar(bruto, eficiencia, metodologia, log=log)

    assert finding.status == STATUS_INVALID
    assert any(e["rule_id"] == "EVIDENCE_UNEXPECTED_UNIT" for e in log.as_dicts())


def test_carbono_e_qualitativo_com_rubrica(metodologia):
    """
    O carbono é avaliado pela rubrica do Quadro 23, não pela razão do Quadro 22.

    A troca é a decisão registrada em `_decisao` no indicators.json: exigir
    intensidade (gCO2e/kWh) mantinha o indicador correto no papel e inútil na
    prática — nenhum relatório dos provedores publica essa razão, então ele saía
    do conjunto comparável em toda avaliação. O que este teste protege é que a
    troca seja completa: sobrar `expected_units` faria a validação quantitativa
    continuar rodando sobre um indicador que já não produz valor.
    """
    carbono = metodologia.by_id("sustainability_carbon_emissions")
    assert carbono.data_type == "qualitative"
    assert carbono.rubric is not None
    assert not carbono.expected_units


def test_emissao_absoluta_nao_vira_nota(metodologia):
    """
    A razão pela qual o total absoluto nunca entrou continua valendo: comparar
    tCO2e entre provedores puniria o maior por ser maior. Antes isso era barrado
    pela lista de unidades; agora é estrutural — sendo qualitativo, o indicador
    só aceita categoria, e um `value` numérico não tem por onde entrar.
    """
    log = GuardrailLog()
    carbono = metodologia.by_id("sustainability_carbon_emissions")
    bruto = _bruto(
        indicator_id="sustainability_carbon_emissions",
        nature="quantitative",
        value=12000.0,
        unit="tCO2e",
        category=None,
    )
    finding = _validar(bruto, carbono, metodologia, log=log)

    assert finding.status == STATUS_INVALID
    assert finding.value is None


@pytest.mark.parametrize(
    "categoria,valor",
    [("alto", 0.75), ("completo", 1.0), ("nao_identificado", None)],
)
def test_categorias_do_carbono_seguem_a_rubrica(metodologia, categoria, valor):
    """Os níveis do Quadro 23 valem para o carbono como valem para os demais qualitativos."""
    from domain.normalization import value_from_category

    carbono = metodologia.by_id("sustainability_carbon_emissions")
    assert value_from_category(carbono, categoria)[0] == valor


@pytest.mark.parametrize(
    "texto,esperado",
    [
        (">= 99.99%", 99.99),
        ("≥ 99,99%", 99.99),
        ("no mínimo 99,9%", 99.9),
        ("at least 99.95 percent", 99.95),  # "percent" por extenso é sufixo aceito
        ("99.99%", 99.99),
        ("1.09 PUE", 1.09),
        ("1-minute intervals", None),
        ("entre 10 e 50 ms", None),
        ("Uptime Percentage", None),
        (None, None),
    ],
)
def test_numero_com_comparador_e_lido_sem_inferencia(texto, esperado):
    """
    O operador é formato, não valor: removê-lo devolve o mesmo número que está
    escrito. Faixa, texto sem número e período de amostragem continuam fora,
    porque ali não há um valor único a comparar.
    """
    from evidence import numero_do_texto

    assert numero_do_texto(texto) == esperado


def test_sla_com_operador_vira_evidencia_valida(metodologia):
    """
    O caso concreto: o SLA do Compute Engine publica "≥ 99.99%", o modelo devolvia
    `value` nulo com o texto em `extracted_value`, e a validação recusava por
    "sem valor numérico" — derrubando `performance_availability` para todos os
    provedores por falta de par comparável.
    """
    log = GuardrailLog()
    disponibilidade = metodologia.by_id("performance_availability")
    bruto = _bruto(
        indicator_id="performance_availability",
        value=None,
        unit="%",
        extracted_value="≥ 99.99%",
    )
    finding = _validar(bruto, disponibilidade, metodologia, log=log)

    assert finding.status == STATUS_FOUND
    assert finding.value == pytest.approx(99.99)
    assert any(e["rule_id"] == "EVIDENCE_VALUE_RECOVERED_FROM_TEXT" for e in log.as_dicts())


def test_texto_sem_numero_continua_recusado(metodologia):
    """A recuperação não pode virar porta dos fundos para evidência sem valor."""
    disponibilidade = metodologia.by_id("performance_availability")
    bruto = _bruto(
        indicator_id="performance_availability",
        value=None,
        unit="%",
        extracted_value="Uptime Percentage",
    )
    assert _validar(bruto, disponibilidade, metodologia).status == STATUS_INVALID


def test_rotulo_do_bloco_resolve_para_o_chunk_real(metodologia):
    """
    O modelo devolve `T1`; o que fica gravado é o `chunk_id` verdadeiro. O rótulo
    é endereço dentro de uma chamada, não identidade do trecho — a auditoria
    precisa apontar o trecho, não a posição dele no prompt.
    """
    disponibilidade = metodologia.by_id("performance_availability")
    bruto = _bruto(indicator_id="performance_availability", source_chunk_id="T2")
    finding = _validar(
        bruto, disponibilidade, metodologia, chunks=("chunk-alpha", "chunk-beta")
    )
    assert finding.status == STATUS_FOUND
    assert finding.source_chunk_id == "chunk-beta"


def test_document_id_citado_como_trecho_e_recusado(metodologia):
    """
    A recusa que motivou a mudança do bloco continua valendo: citar o
    identificador do documento no lugar do trecho não passa.
    """
    log = GuardrailLog()
    disponibilidade = metodologia.by_id("performance_availability")
    bruto = _bruto(
        indicator_id="performance_availability",
        source_chunk_id="07ef245377a4e02ef49cf5a17cee03f9",
    )
    finding = _validar(bruto, disponibilidade, metodologia, chunks=("chunk-1",), log=log)
    assert finding.status == STATUS_INVALID
    assert any(e["rule_id"] == "EVIDENCE_SOURCE_NOT_PROVIDED" for e in log.as_dicts())


def test_sinonimo_de_unidade_e_aceito(metodologia):
    """Reconhecer que "percent" e "%" são a mesma unidade não é converter grandeza."""
    from evidence import _unit_is_expected

    disponibilidade = metodologia.by_id("performance_availability")
    assert _unit_is_expected("percent", disponibilidade)
    assert _unit_is_expected("%", disponibilidade)
    assert not _unit_is_expected("ms", disponibilidade)


def test_indicador_sem_unidades_declaradas_aceita_qualquer(metodologia):
    """Lista vazia é "não foi especificado", não "nada é aceito"."""
    from dataclasses import replace

    from evidence import _unit_is_expected

    sem_lista = replace(metodologia.by_id("performance_latency"), expected_units=())
    assert _unit_is_expected("qualquer coisa", sem_lista)


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
    _stub_rag(monkeypatch, evidence, lambda pid: [_chunk(f"chunk-{pid}", pid)])
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


def test_trecho_do_relatorio_leva_os_termos_do_quadro_27_que_contem(
    monkeypatch, metodologia, disponibilidade
):
    """
    §4.4: a recuperação é "orientada pelos indicadores previamente definidos".
    O trecho exibido no relatório carrega, por isso, os termos do Quadro 27 do
    indicador que o trouxe e que estão de fato no texto — é o que permite ao
    leitor conferir a ligação entre a citação e o indicador.
    """
    payload = {"findings": []}
    extraction, _ = _rodar_extracao(monkeypatch, metodologia, [disponibilidade], payload)

    trecho = extraction.evidences["aws"][0]
    # O `_chunk` diz "Disponibilidade mensal de 99,99% conforme SLA."
    assert trecho["matched_terms"] == ["disponibilidade", "SLA"]
    # Termo do indicador que o trecho não traz não vira tag.
    assert "uptime" not in trecho["matched_terms"]


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
    _stub_rag(monkeypatch, evidence, lambda pid: [_chunk(f"chunk-{pid}", pid)])

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
    _stub_rag(monkeypatch, evidence, lambda pid: [])
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
    assert "nao_identificado" in texto and "baixo" in texto and "completo" in texto
    assert "quantitativo" in texto and "qualitativo" in texto


def test_prompt_leva_os_termos_do_quadro_27(disponibilidade):
    """
    §5.2: os termos são "elementos orientadores na construção das consultas e na
    recuperação das evidências documentais" — as duas metades da frase.
    """
    texto = evidence.describe_indicators([disponibilidade])
    assert "termos relacionados:" in texto
    assert "uptime" in texto


def test_prompt_de_extracao_esta_registrado():
    from llm.prompts import registered_versions

    # v4: acrescentou ESTADO DA EVIDÊNCIA (regra 12).
    # v5: acrescentou as regras 5.1–5.4, de aplicação da rubrica.
    # v6: regra 6 passou a pedir o rótulo curto do bloco (`T1`) e o nome do
    # arquivo, no lugar dos dois hashes que o modelo confundia; e a regra 4.1
    # declarou `extracted_value`/`value`/`unit` juntos, depois de a primeira
    # redação fazer o modelo abandonar `unit` e derrubar o PUE dos três
    # provedores. A medição que
    # as motivou: 53 de 54 classificações qualitativas saíam `alto`, a rubrica de
    # quatro níveis operava com um, e os indicadores qualitativos deixavam de
    # separar os provedores.
    #
    # A versão entra no registro de cada execução (§27), então mudar o texto sem
    # mudar o número apagaria a diferença entre duas avaliações feitas com
    # prompts diferentes.
    assert registered_versions()["PROMPT_EVIDENCE_EXTRACTION_V1"] == "6"


# Instruções operacionais do Quadro 26, na ordem das linhas do quadro. O texto é
# o da dissertação; o teste falha se uma edição do prompt derrubar qualquer uma.
QUADRO_26 = [
    (
        "Papel do modelo",
        "Você é um assistente de apoio à decisão para seleção de provedores de "
        "Cloud Computing.",
    ),
    (
        "Escopo da análise",
        "Analise as evidências documentais exclusivamente em relação aos "
        "indicadores previamente definidos nesta pesquisa, organizados nas "
        "dimensões de sustentabilidade, segurança e desempenho operacional.",
    ),
    (
        "Uso das evidências",
        "Utilize apenas as evidências documentais recuperadas pelo mecanismo RAG",
    ),
    (
        "Extração estruturada",
        "Identifique e extraia o valor, característica, prática ou evidência "
        "explicitamente apresentada nos documentos para o indicador analisado, "
        "sem atribuir pontuação à alternativa.",
    ),
    (
        "Fonte da informação",
        "Utilize os documentos recuperados que sustentam a análise realizada",
    ),
    (
        "Síntese contextual",
        "uma síntese da evidência identificada, relacionando-a ao indicador "
        "correspondente e evitando interpretações que não estejam sustentadas "
        "pelos documentos recuperados.",
    ),
    (
        "Qualidade da evidência",
        "se a evidência recuperada é quantitativa",
    ),
    (
        "Qualidade da evidência (não é nota)",
        "sem utilizar essa classificação como pontuação da alternativa.",
    ),
    (
        "Restrição metodológica",
        "Não gere novos indicadores e não atribua pontuação quando não houver "
        "evidência documental suficiente.",
    ),
    (
        "Ausência de evidência",
        "não identificado nas fontes recuperadas",
    ),
]


@pytest.mark.parametrize("componente,instrucao", QUADRO_26, ids=[c for c, _ in QUADRO_26])
def test_prompt_reproduz_o_quadro_26(componente, instrucao):
    """Cada linha do Quadro 26 aparece no prompt principal (§5.2)."""
    from llm.prompts import get as get_prompt

    system = " ".join(get_prompt("PROMPT_EVIDENCE_EXTRACTION_V1").system.split())
    assert " ".join(instrucao.split()) in system, f"Quadro 26 → {componente} não está no prompt"


def test_prompt_define_quando_o_estado_e_found(disponibilidade):
    """
    A falha que motivou a regra 12: sem definição de `FOUND`, o modelo devolvia
    `PARTIAL` para extração completa (PUE de 1,15 com o trecho na mão), e
    `PARTIAL` fica fora do conjunto comparável — relatório inteiro de zeros com
    os valores corretos extraídos e descartados.
    """
    from llm.prompts import get as get_prompt

    system = " ".join(get_prompt("PROMPT_EVIDENCE_EXTRACTION_V1").system.split())
    assert "ESTADO DA EVIDÊNCIA" in system
    assert 'Use `evidence_status: "FOUND"` quando o trecho apresentar' in system
    # PARTIAL sem valor: é o que impede uma meta futura de entrar como valor.
    assert "meta ou compromisso futuro" in system
    assert 'Não devolva `"PARTIAL"` com `value` ou `category` preenchidos' in system


def test_prompt_declara_as_secoes_do_quadro_26():
    """As seções nomeadas tornam a conferência com o quadro direta."""
    from llm.prompts import get as get_prompt

    system = get_prompt("PROMPT_EVIDENCE_EXTRACTION_V1").system
    for secao in (
        "ESCOPO DA ANÁLISE",
        "USO DAS EVIDÊNCIAS",
        "EXTRAÇÃO ESTRUTURADA",
        "FONTE DA INFORMAÇÃO",
        "SÍNTESE CONTEXTUAL",
        "QUALIDADE DA EVIDÊNCIA",
        "RESTRIÇÃO METODOLÓGICA",
        "AUSÊNCIA DE EVIDÊNCIA",
    ):
        assert secao in system


def test_schema_tem_os_campos_que_a_secao_5_4_exige():
    """
    §5.4: indicador analisado, evidência identificada, natureza, valor ou
    característica extraída e referência à fonte documental.
    """
    from llm.schemas import IndicatorEvidence as Schema

    campos = set(Schema.model_fields)
    assert {"indicator_id", "summary", "nature", "extracted_value", "source_document"} <= campos
    # E nenhum campo onde caiba uma nota.
    assert not campos & {"score", "rating", "weight", "rank", "peso", "nota"}


def test_caracteristica_extraida_acompanha_a_categoria(certificacoes, metodologia):
    """
    O Quadro 26 manda extrair "o valor, característica, prática ou evidência".
    Nem tudo o que um documento apresenta cabe num número — a característica
    fica ao lado da categoria, para que a classificação seja conferível.
    """
    bruto = _bruto(
        indicator_id="security_certifications",
        nature="qualitative",
        value=None,
        unit=None,
        category="completo",
        extracted_value="ISO/IEC 27001, ISO/IEC 27017 e SOC 2",
    )
    finding = _validar(bruto, certificacoes, metodologia)
    assert finding.category == "completo"
    assert finding.value == pytest.approx(1.0)
    assert finding.extracted_value == "ISO/IEC 27001, ISO/IEC 27017 e SOC 2"
    # E chega ao domínio como procedência do valor, não como valor.
    assert finding.to_performance().raw_value == "ISO/IEC 27001, ISO/IEC 27017 e SOC 2"
