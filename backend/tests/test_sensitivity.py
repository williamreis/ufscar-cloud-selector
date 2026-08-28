"""
Sensibilidade do ranking aos pesos das dimensões.

O que estes testes protegem é a honestidade da medida, não o valor dela: uma
liderança frágil precisa aparecer como frágil, uma robusta como robusta, e a
análise não pode em hipótese alguma mexer no ranking que está medindo.
"""

import pytest

from domain.sensitivity import PASSO, analyze

# Dois indicadores, um por dimensão, com peso local inteiro dentro da sua.
# Assim o peso da dimensão passa direto para o indicador e a aritmética do teste
# fica conferível a olho.
LOCAIS = {"ind_sust": 1.0, "ind_perf": 1.0}
DIM_POR_IND = {"ind_sust": "sustainability", "ind_perf": "performance"}
VALIDOS = ["ind_sust", "ind_perf"]


def _analisar(pesos, normalizados, ranking, tolerancia=0.02):
    return analyze(
        ranking=ranking,
        dimension_weights=pesos,
        local_weights=LOCAIS,
        dimension_by_indicator=DIM_POR_IND,
        normalized=normalizados,
        valid_indicators=VALIDOS,
        tie_break_tolerance=tolerancia,
    )


def test_lideranca_apertada_vira_com_pouco():
    """
    A vence por sustentabilidade, B por desempenho, e os pesos estão quase
    equilibrados. Deslocar um pouco o peso é suficiente para trocar o líder — e
    é isso que o relatório precisa dizer.
    """
    pesos = {"sustainability": 0.52, "performance": 0.48}
    normalizados = {
        ("a", "ind_sust"): 1.0, ("a", "ind_perf"): 0.8,
        ("b", "ind_sust"): 0.8, ("b", "ind_perf"): 1.0,
    }
    ranking = [{"id": "a", "score": 0.904}, {"id": "b", "score": 0.896}]

    r = _analisar(pesos, normalizados, ranking)
    assert r is not None
    assert r.leader == "a"
    assert not r.robust
    frágil = r.most_fragile
    assert frágil is not None
    assert frágil.flip_leader == "b"
    # Basta atravessar o equilíbrio: menos de 5 pontos de peso.
    assert abs(frágil.flip_delta) <= 0.05


def test_lideranca_dominante_nao_vira():
    """Quem lidera em TODOS os indicadores não perde por remanejo de peso."""
    pesos = {"sustainability": 0.5, "performance": 0.5}
    normalizados = {
        ("a", "ind_sust"): 1.0, ("a", "ind_perf"): 1.0,
        ("b", "ind_sust"): 0.6, ("b", "ind_perf"): 0.7,
    }
    ranking = [{"id": "a", "score": 1.0}, {"id": "b", "score": 0.65}]

    r = _analisar(pesos, normalizados, ranking)
    assert r.robust
    assert r.most_fragile is None
    assert all(d.flip_delta is None for d in r.dimensions)


def test_margem_menor_que_a_tolerancia_e_sinalizada():
    """
    A margem para o 2º colocado cabe dentro da margem de indiferença: o relatório
    precisa poder dizer que a ordem não é uma distinção do instrumento.
    """
    pesos = {"sustainability": 0.5, "performance": 0.5}
    normalizados = {
        ("a", "ind_sust"): 1.0, ("a", "ind_perf"): 0.9,
        ("b", "ind_sust"): 0.99, ("b", "ind_perf"): 0.9,
    }
    ranking = [{"id": "a", "score": 0.950}, {"id": "b", "score": 0.945}]

    r = _analisar(pesos, normalizados, ranking, tolerancia=0.02)
    assert r.margin == pytest.approx(0.005)
    assert r.margin_within_tolerance is True


def test_margem_confortavel_nao_e_sinalizada():
    pesos = {"sustainability": 0.5, "performance": 0.5}
    normalizados = {
        ("a", "ind_sust"): 1.0, ("a", "ind_perf"): 1.0,
        ("b", "ind_sust"): 0.5, ("b", "ind_perf"): 0.5,
    }
    ranking = [{"id": "a", "score": 1.0}, {"id": "b", "score": 0.5}]

    r = _analisar(pesos, normalizados, ranking, tolerancia=0.02)
    assert r.margin_within_tolerance is False


def test_pesos_reescalados_continuam_somando_um():
    """
    A perturbação move uma dimensão e reescala as outras proporcionalmente. Se a
    soma escapasse de 1, os pesos efetivos do cenário testado não seriam
    comparáveis com os do cenário real, e a resposta não significaria nada.
    """
    from domain.sensitivity import _reescala

    pesos = {"a": 0.5, "b": 0.3, "c": 0.2}
    novo = _reescala(pesos, "a", 0.8)
    assert sum(novo.values()) == pytest.approx(1.0)
    assert novo["a"] == pytest.approx(0.8)
    # b e c mantêm a proporção 3:2 entre si.
    assert novo["b"] / novo["c"] == pytest.approx(0.3 / 0.2)


def test_sem_segundo_colocado_nao_ha_o_que_analisar():
    r = _analisar(
        {"sustainability": 1.0},
        {("a", "ind_sust"): 1.0},
        [{"id": "a", "score": 1.0}],
    )
    assert r is None


def test_conjunto_comparavel_vazio_devolve_nulo():
    r = analyze(
        ranking=[{"id": "a", "score": 0.0}, {"id": "b", "score": 0.0}],
        dimension_weights={"sustainability": 1.0},
        local_weights=LOCAIS,
        dimension_by_indicator=DIM_POR_IND,
        normalized={},
        valid_indicators=[],
        tie_break_tolerance=0.02,
    )
    assert r is None


def test_analise_nao_altera_o_ranking_recebido():
    """A medida é sobre o resultado; tocá-lo seria trocar o objeto medido."""
    pesos = {"sustainability": 0.5, "performance": 0.5}
    normalizados = {
        ("a", "ind_sust"): 1.0, ("a", "ind_perf"): 0.8,
        ("b", "ind_sust"): 0.8, ("b", "ind_perf"): 1.0,
    }
    ranking = [{"id": "a", "score": 0.9}, {"id": "b", "score": 0.9}]
    copia = [dict(r) for r in ranking]

    _analisar(pesos, normalizados, ranking)
    assert ranking == copia


def test_passo_da_varredura_e_fino_o_bastante_para_meio_ponto():
    """Um passo grosso demais reportaria 'não vira' onde vira com pouco."""
    assert PASSO <= 0.005
