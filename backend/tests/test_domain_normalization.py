"""
Normalização, comparabilidade e pontuação (diretriz §9–§13, §42.3 e §42.4).

Três afirmações que o produto faz e que estes testes existem para sustentar:

  1. **ausência de evidência não é desempenho zero** — `NOT_FOUND` tira o
     indicador da comparação, não a nota do provedor;
  2. **todos são medidos na mesma régua** — o conjunto `V` é comum, e nunca há
     renormalização por provedor;
  3. **nada é inventado quando a conta não fecha** — divisão indefinida marca o
     indicador como não comparável em vez de produzir um número.
"""

import pytest

from domain.methodology import load_methodology
from domain.normalization import (
    EXCLUDED_INVALID_FOR_COMPARISON,
    EXCLUDED_MISSING_FOR_SOME,
    EXCLUDED_NON_DISCRIMINATIVE,
    EXCLUDED_NO_EVIDENCE,
    EXCLUDED_NO_WEIGHT,
    STATUS_FOUND,
    STATUS_NOT_FOUND,
    STATUS_PARTIAL,
    PerformanceInput,
    build_comparability_set,
    normalize_values,
    renormalize_weights,
    value_from_category,
)
from domain.scoring import compute_scores

PROVEDORES = [
    {"id": "aws", "name": "AWS"},
    {"id": "gcp", "name": "Google Cloud"},
    {"id": "azure", "name": "Microsoft Azure"},
]
IDS = [p["id"] for p in PROVEDORES]


@pytest.fixture
def metodologia():
    return load_methodology()


def desempenho(indicator_id, valores, status=STATUS_FOUND):
    return [
        PerformanceInput(
            provider_id=pid,
            indicator_id=indicator_id,
            status=STATUS_NOT_FOUND if valor is None else status,
            value=valor,
        )
        for pid, valor in valores.items()
    ]


# --- Normalização quantitativa (§9, §42.3) ---------------------------------


def test_beneficio_divide_pelo_maximo():
    """r_ij = x_ij / max_i(x_ij) — disponibilidade, energia renovável."""
    resultado, motivo = normalize_values({"a": 99.99, "b": 99.9, "c": 99.5}, "benefit")
    assert motivo is None
    assert resultado["a"] == pytest.approx(1.0)
    assert resultado["b"] == pytest.approx(99.9 / 99.99)
    assert max(resultado.values()) == pytest.approx(1.0)


def test_minimizacao_divide_o_minimo_pelo_valor():
    """r_ij = min_i(x_ij) / x_ij — PUE, latência."""
    resultado, motivo = normalize_values({"a": 1.10, "b": 1.20, "c": 1.50}, "minimize")
    assert motivo is None
    assert resultado["a"] == pytest.approx(1.0)  # melhor PUE
    assert resultado["c"] == pytest.approx(1.10 / 1.50)
    assert resultado["a"] > resultado["b"] > resultado["c"]


def test_minimizacao_inverte_a_ordem_do_beneficio():
    valores = {"a": 1.0, "b": 2.0}
    beneficio, _ = normalize_values(valores, "benefit")
    minimizacao, _ = normalize_values(valores, "minimize")
    assert beneficio["b"] > beneficio["a"]
    assert minimizacao["a"] > minimizacao["b"]


def test_valores_iguais_dao_normalizadas_iguais():
    resultado, motivo = normalize_values({"a": 5.0, "b": 5.0}, "benefit")
    assert motivo is None
    assert resultado["a"] == resultado["b"] == pytest.approx(1.0)


# --- Casos indefinidos (§9.3) ----------------------------------------------


def test_todos_zerados_em_beneficio_nao_discrimina():
    """max = 0 → x/max é 0/0. Marcar, não inventar."""
    resultado, motivo = normalize_values({"a": 0.0, "b": 0.0}, "benefit")
    assert resultado == {}
    assert motivo == EXCLUDED_NON_DISCRIMINATIVE


def test_zero_em_minimizacao_e_invalido_para_comparacao():
    """min/x com x = 0 é divisão por zero — não há valor de recurso."""
    resultado, motivo = normalize_values({"a": 0.0, "b": 1.2}, "minimize")
    assert resultado == {}
    assert motivo == EXCLUDED_INVALID_FOR_COMPARISON


def test_valor_negativo_em_minimizacao_e_invalido():
    resultado, motivo = normalize_values({"a": -1.0, "b": 1.0}, "minimize")
    assert motivo == EXCLUDED_INVALID_FOR_COMPARISON


def test_sem_valores_nao_ha_evidencia():
    resultado, motivo = normalize_values({}, "benefit")
    assert motivo == EXCLUDED_NO_EVIDENCE


# --- Medição absoluta: indicadores de rubrica ------------------------------


def test_absoluto_preserva_o_valor_da_rubrica():
    """
    O Quadro 23 já entrega a nota numa escala fixa de 0 a 1. Dividir pelo maior
    a converteria em relativa, e a nota deixaria de significar o mesmo em dois
    relatórios diferentes.
    """
    resultado, motivo = normalize_values(
        {"a": 0.75, "b": 0.5, "c": 1.0}, "benefit", absolute=True
    )
    assert motivo is None
    assert resultado == {"a": 0.75, "b": 0.5, "c": 1.0}


def test_absoluto_distingue_todos_ruins_de_todos_otimos():
    """
    O caso que motivou a mudança. No modo relativo os dois cenários davam 1,000
    para todo mundo: uma dimensão em que ninguém atende recebia nota máxima, e
    ficava indistinguível de uma em que todos atendem integralmente.
    """
    todos_baixos = {"a": 0.25, "b": 0.25, "c": 0.25}
    todos_completos = {"a": 1.0, "b": 1.0, "c": 1.0}

    relativo_baixo, _ = normalize_values(todos_baixos, "benefit")
    relativo_completo, _ = normalize_values(todos_completos, "benefit")
    assert relativo_baixo == relativo_completo  # o defeito, preservado como registro

    absoluto_baixo, _ = normalize_values(todos_baixos, "benefit", absolute=True)
    absoluto_completo, _ = normalize_values(todos_completos, "benefit", absolute=True)
    assert absoluto_baixo != absoluto_completo
    assert all(v == 0.25 for v in absoluto_baixo.values())
    assert all(v == 1.0 for v in absoluto_completo.values())


def test_absoluto_nao_depende_do_concorrente():
    """Um `alto` vale 0,75 esteja ele sozinho ou ao lado de um `completo`."""
    sozinho, _ = normalize_values({"a": 0.75, "b": 0.75}, "benefit", absolute=True)
    acompanhado, _ = normalize_values({"a": 0.75, "b": 1.0}, "benefit", absolute=True)
    assert sozinho["a"] == acompanhado["a"] == 0.75


def test_absoluto_recusa_valor_fora_da_escala():
    """Fora de [0,1] não é rubrica: é engano de configuração, e precisa aparecer."""
    resultado, motivo = normalize_values({"a": 1.4, "b": 0.75}, "benefit", absolute=True)
    assert resultado == {}
    assert motivo == EXCLUDED_INVALID_FOR_COMPARISON


def test_qualitativo_usa_medicao_absoluta_no_conjunto_comparavel(metodologia):
    """
    A escolha é por tipo de indicador: `build_comparability_set` liga o modo
    absoluto nos qualitativos e mantém a divisão pelo máximo nos quantitativos.
    """
    iam = metodologia.by_id("security_iam")
    assert iam.is_qualitative

    conjunto = build_comparability_set(
        [
            PerformanceInput("aws", "security_iam", STATUS_FOUND, 0.75),
            PerformanceInput("gcp", "security_iam", STATUS_FOUND, 0.75),
        ],
        ["aws", "gcp"],
        ["security_iam"],
        metodologia,
    )
    assert "security_iam" in conjunto.valid
    normalizados = conjunto.normalized_by()
    # Relativo daria 1,0 para os dois; absoluto preserva o nível declarado.
    assert normalizados[("aws", "security_iam")] == 0.75
    assert normalizados[("gcp", "security_iam")] == 0.75


# --- Rubrica qualitativa (§10.1) -------------------------------------------


def test_categoria_permitida_vira_valor_pela_rubrica(metodologia):
    indicador = metodologia.by_id("security_iam")
    valor, status = value_from_category(indicador, "completo")
    assert valor == pytest.approx(1.0)
    assert status == STATUS_FOUND


def test_categoria_fora_da_allowlist_e_invalida(metodologia):
    """§19: a categoria precisa pertencer à allowlist — 'quase' não é valor."""
    indicador = metodologia.by_id("security_iam")
    valor, status = value_from_category(indicador, "muito_bom")
    assert valor is None
    assert status == "INVALID"


def test_categoria_ausente_e_not_found_nao_zero(metodologia):
    indicador = metodologia.by_id("security_iam")
    valor, status = value_from_category(indicador, None)
    assert valor is None
    assert status == STATUS_NOT_FOUND


# --- Conjunto comparável (§11.1, §42.4) ------------------------------------


def test_indicador_com_evidencia_para_todos_entra(metodologia):
    conjunto = build_comparability_set(
        desempenho("performance_availability", {"aws": 99.99, "gcp": 99.9, "azure": 99.95}),
        IDS,
        ["performance_availability"],
        metodologia,
    )
    assert conjunto.valid == ("performance_availability",)
    assert conjunto.excluded == {}


def test_not_found_nao_vira_zero(metodologia):
    """§11 e §42.4: o indicador sai da comparação; o provedor não é penalizado."""
    conjunto = build_comparability_set(
        desempenho("performance_availability", {"aws": 99.99, "gcp": None, "azure": 99.95}),
        IDS,
        ["performance_availability"],
        metodologia,
    )
    assert conjunto.valid == ()
    assert conjunto.excluded["performance_availability"] == EXCLUDED_MISSING_FOR_SOME

    normalizados = {(n.provider_id, n.indicator_id): n for n in conjunto.normalized}
    gcp = normalizados[("gcp", "performance_availability")]
    assert gcp.normalized_value is None  # e não 0.0
    assert gcp.status == STATUS_NOT_FOUND


def test_conjunto_valido_e_o_mesmo_para_todos_os_provedores(metodologia):
    """§11.1, MUST: nunca renormalizar de forma diferente por provedor."""
    performances = [
        *desempenho("performance_availability", {"aws": 99.9, "gcp": 99.5, "azure": 99.99}),
        *desempenho("performance_latency", {"aws": 20.0, "gcp": None, "azure": 25.0}),
    ]
    conjunto = build_comparability_set(
        performances, IDS, ["performance_availability", "performance_latency"], metodologia
    )
    assert conjunto.valid == ("performance_availability",)

    # Nenhum provedor recebe valor normalizado para o indicador descartado.
    por_indicador = [n for n in conjunto.normalized if n.indicator_id == "performance_latency"]
    assert all(n.normalized_value is None for n in por_indicador)


def test_indicador_sem_peso_nao_entra_mesmo_com_evidencia(metodologia):
    """Peso nulo = o gestor não deu base; usar assim mesmo fabricaria julgamento."""
    conjunto = build_comparability_set(
        desempenho("performance_availability", {"aws": 99.9, "gcp": 99.5, "azure": 99.99}),
        IDS,
        [],  # nenhum indicador com peso
        metodologia,
    )
    assert conjunto.valid == ()
    assert conjunto.excluded["performance_availability"] == EXCLUDED_NO_WEIGHT


def test_partial_nao_conta_como_comparavel_por_padrao(metodologia):
    """§29.2: sem decisão acadêmica, PARTIAL não recebe peso arbitrário."""
    performances = [
        PerformanceInput("aws", "performance_availability", STATUS_PARTIAL, 99.9),
        PerformanceInput("gcp", "performance_availability", STATUS_FOUND, 99.5),
        PerformanceInput("azure", "performance_availability", STATUS_FOUND, 99.99),
    ]
    conjunto = build_comparability_set(
        performances, IDS, ["performance_availability"], metodologia
    )
    assert conjunto.valid == ()


def test_taxa_de_comparabilidade(metodologia):
    performances = [
        *desempenho("performance_availability", {"aws": 99.9, "gcp": 99.5, "azure": 99.99}),
        *desempenho("performance_latency", {"aws": 20.0, "gcp": None, "azure": 25.0}),
    ]
    conjunto = build_comparability_set(
        performances, IDS, ["performance_availability", "performance_latency"], metodologia
    )
    assert conjunto.comparability_rate == pytest.approx(0.5)


# --- Renormalização (§11.2, §42.4) -----------------------------------------


def test_pesos_validos_renormalizam_para_1():
    pesos = {"a": 0.2, "b": 0.3, "c": 0.5}
    efetivos = renormalize_weights(pesos, ["a", "b"])
    assert sum(efetivos.values()) == pytest.approx(1.0)
    assert efetivos["a"] == pytest.approx(0.4)
    assert efetivos["b"] == pytest.approx(0.6)


def test_renormalizacao_preserva_a_proporcao_entre_os_validos():
    pesos = {"a": 0.1, "b": 0.3}
    efetivos = renormalize_weights(pesos, ["a", "b"])
    assert efetivos["b"] / efetivos["a"] == pytest.approx(3.0)


def test_sem_indicador_valido_nao_ha_peso_efetivo():
    assert renormalize_weights({"a": 0.5}, []) == {}


# --- Pontuação e contribuições (§12, §13) ----------------------------------


def test_score_e_a_soma_das_contribuicoes(metodologia):
    performances = [
        *desempenho("performance_availability", {"aws": 99.99, "gcp": 99.9, "azure": 99.5}),
        *desempenho("performance_latency", {"aws": 20.0, "gcp": 10.0, "azure": 40.0}),
    ]
    ids = ["performance_availability", "performance_latency"]
    conjunto = build_comparability_set(performances, IDS, ids, metodologia)
    efetivos = renormalize_weights({"performance_availability": 0.6, "performance_latency": 0.4}, ids)

    resultado = compute_scores(PROVEDORES, conjunto, efetivos, metodologia)
    for score in resultado.scores:
        assert score.score == pytest.approx(sum(c.contribution for c in score.contributions))


def test_contribuicao_e_peso_vezes_normalizada(metodologia):
    conjunto = build_comparability_set(
        desempenho("performance_availability", {"aws": 100.0, "gcp": 50.0, "azure": 25.0}),
        IDS,
        ["performance_availability"],
        metodologia,
    )
    efetivos = renormalize_weights({"performance_availability": 0.7}, ["performance_availability"])
    resultado = compute_scores(PROVEDORES, conjunto, efetivos, metodologia)

    aws = next(s for s in resultado.scores if s.provider_id == "aws")
    contribuicao = aws.contributions[0]
    assert contribuicao.effective_weight == pytest.approx(1.0)  # único válido
    assert contribuicao.normalized_value == pytest.approx(1.0)
    assert contribuicao.contribution == pytest.approx(1.0)


def test_contribuicao_por_dimensao_soma_o_score(metodologia):
    """§13: a explicação por dimensão precisa fechar com a pontuação final."""
    performances = [
        *desempenho("performance_availability", {"aws": 99.9, "gcp": 99.5, "azure": 99.99}),
        *desempenho("sustainability_renewable_energy", {"aws": 90.0, "gcp": 100.0, "azure": 80.0}),
    ]
    ids = ["performance_availability", "sustainability_renewable_energy"]
    conjunto = build_comparability_set(performances, IDS, ids, metodologia)
    efetivos = renormalize_weights(
        {"performance_availability": 0.5, "sustainability_renewable_energy": 0.5}, ids
    )
    resultado = compute_scores(PROVEDORES, conjunto, efetivos, metodologia)

    for score in resultado.scores:
        assert sum(score.dimension_contributions.values()) == pytest.approx(score.score)


def test_ranking_e_decrescente(metodologia):
    # Valores bem separados de propósito: 100 / 80 / 60 normalizam para
    # 1,00 / 0,80 / 0,60, muito além da margem de indiferença. O teste é sobre a
    # ORDEM; misturar nele diferenças pequenas o transformaria, sem querer, num
    # teste de empate — que tem os seus próprios, logo abaixo.
    conjunto = build_comparability_set(
        desempenho("performance_availability", {"aws": 80.0, "gcp": 100.0, "azure": 60.0}),
        IDS,
        ["performance_availability"],
        metodologia,
    )
    efetivos = renormalize_weights({"performance_availability": 1.0}, ["performance_availability"])
    resultado = compute_scores(PROVEDORES, conjunto, efetivos, metodologia)

    scores = [s.score for s in resultado.scores]
    assert scores == sorted(scores, reverse=True)
    assert resultado.scores[0].provider_id == "gcp"
    assert [s.rank for s in resultado.scores] == [1, 2, 3]
    assert not resultado.has_ties


def test_empate_nao_e_desfeito_por_criterio_oculto(metodologia):
    """§12 / TODO ACADÊMICO 03: exibir o empate, não resolvê-lo em silêncio."""
    conjunto = build_comparability_set(
        desempenho("performance_availability", {"aws": 99.9, "gcp": 99.9, "azure": 90.0}),
        IDS,
        ["performance_availability"],
        metodologia,
    )
    efetivos = renormalize_weights({"performance_availability": 1.0}, ["performance_availability"])
    resultado = compute_scores(PROVEDORES, conjunto, efetivos, metodologia)

    empatados = [s for s in resultado.scores if s.tied]
    assert len(empatados) == 2
    assert {s.provider_id for s in empatados} == {"aws", "gcp"}
    assert empatados[0].rank == empatados[1].rank
    assert resultado.has_ties


def test_margem_de_indiferenca_agrupa_pontuacoes_proximas(metodologia):
    """
    Abaixo da margem, duas pontuações não são tratadas como diferentes. O caso
    real: as simulações fechavam com 0,815 / 0,814 / 0,813, e o relatório coroava
    o primeiro como se 0,002 fosse uma classificação.
    """
    assert metodologia.tie_break_tolerance >= 0.01, (
        "com tolerância ~0 só empate exato é reconhecido, e ruído vira ordem"
    )
    # 99,99 · 99,98 · 90,0 → normalizados 1,0 · 0,9999 · 0,9001
    conjunto = build_comparability_set(
        desempenho("performance_availability", {"aws": 99.99, "gcp": 99.98, "azure": 90.0}),
        IDS,
        ["performance_availability"],
        metodologia,
    )
    efetivos = renormalize_weights(
        {"performance_availability": 1.0}, ["performance_availability"]
    )
    resultado = compute_scores(PROVEDORES, conjunto, efetivos, metodologia)

    por_id = {s.provider_id: s for s in resultado.scores}
    assert por_id["aws"].rank == por_id["gcp"].rank  # 0,0001 de diferença
    assert por_id["azure"].rank > por_id["aws"].rank  # 0,10 é diferença de verdade
    assert resultado.has_ties


def test_empate_e_medido_contra_o_lider_do_grupo(metodologia):
    """
    Comparar com o vizinho imediato encadeia: numa sequência em que cada par
    dista menos que a margem, o último acabaria empatado com o primeiro mesmo
    estando muito além dela. A âncora é o líder do grupo.
    """
    tol = metodologia.tie_break_tolerance
    # Escadinha: cada degrau tem 80% da margem, mas o total passa de duas margens.
    passo = tol * 0.8
    conjunto = build_comparability_set(
        desempenho(
            "performance_availability",
            {"aws": 100.0, "gcp": 100.0 * (1 - passo), "azure": 100.0 * (1 - 2 * passo)},
        ),
        IDS,
        ["performance_availability"],
        metodologia,
    )
    efetivos = renormalize_weights(
        {"performance_availability": 1.0}, ["performance_availability"]
    )
    resultado = compute_scores(PROVEDORES, conjunto, efetivos, metodologia)
    por_id = {s.provider_id: s for s in resultado.scores}

    assert por_id["gcp"].rank == por_id["aws"].rank  # dentro da margem do líder
    assert por_id["azure"].rank > por_id["aws"].rank  # 1,6 margem do líder: fora


def test_pesos_efetivos_do_resultado_somam_1(metodologia):
    ids = ["performance_availability", "performance_latency"]
    performances = [
        *desempenho("performance_availability", {"aws": 99.9, "gcp": 99.5, "azure": 99.99}),
        *desempenho("performance_latency", {"aws": 20.0, "gcp": 10.0, "azure": 40.0}),
    ]
    conjunto = build_comparability_set(performances, IDS, ids, metodologia)
    efetivos = renormalize_weights({"performance_availability": 0.3, "performance_latency": 0.1}, ids)
    resultado = compute_scores(PROVEDORES, conjunto, efetivos, metodologia)
    assert resultado.as_dict()["effective_weight_sum"] == pytest.approx(1.0)


def test_provedor_sem_indicador_valido_nao_e_omitido(metodologia):
    """Pontuar 0 por falta de base comparável é diferente de ter desempenho 0 —
    e o provedor precisa aparecer para que a diferença possa ser dita."""
    conjunto = build_comparability_set([], IDS, [], metodologia)
    resultado = compute_scores(PROVEDORES, conjunto, {}, metodologia)
    assert len(resultado.scores) == 3
    assert all(s.score == 0.0 for s in resultado.scores)
    assert resultado.valid_indicators == ()
