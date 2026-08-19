"""
Teste de referência obrigatório do AHP (diretriz §6.5 e §42.1).

A diretriz fixa uma matriz e os valores que ela deve produzir. É o teste que
amarra a implementação ao procedimento descrito na dissertação: se o método de
ponderação mudar sem decisão, este teste cai.

    A = [[1,   5,   7  ],
         [1/5, 1,   3  ],
         [1/7, 1/3, 1  ]]

    Segurança ≈ 0.724 · Desempenho ≈ 0.193 · Sustentabilidade ≈ 0.083
    λmax ≈ 3.066 · CI ≈ 0.033 · RI = 0.58 · CR ≈ 0.057

**Nota sobre o método.** Estes valores correspondem ao procedimento da §6.3
(normalizar por coluna, média das linhas). O método das potências, que o código
usava antes da Fase 1, devolve 0.7306 / 0.1884 / 0.0810 para a mesma matriz — uma
diferença de ~0.007 no primeiro peso, acima do que "pequenas diferenças de ponto
flutuante" comporta. Daí a tolerância apertada aqui.
"""

import numpy as np
import pytest

from ahp import derive_criteria_weights, judgments_to_pairwise_matrix, priority_vector

# Ordem das linhas/colunas da matriz da diretriz.
ORDEM = ("security", "performance", "sustainability")

MATRIZ = np.array(
    [
        [1.0, 5.0, 7.0],
        [1.0 / 5.0, 1.0, 3.0],
        [1.0 / 7.0, 1.0 / 3.0, 1.0],
    ]
)

RANDOM_INDEX = {1: 0.0, 2: 0.0, 3: 0.58, 4: 0.90, 5: 1.12}


@pytest.fixture
def referencia():
    w, lambda_max, ci, cr = priority_vector(
        MATRIZ, method="column_mean", random_index_table=RANDOM_INDEX
    )
    return {"weights": dict(zip(ORDEM, w)), "lambda_max": lambda_max, "ci": ci, "cr": cr}


# --- Os valores da §6.5 ----------------------------------------------------


def test_pesos_da_matriz_de_referencia(referencia):
    pesos = referencia["weights"]
    assert pesos["security"] == pytest.approx(0.724, abs=0.001)
    assert pesos["performance"] == pytest.approx(0.193, abs=0.001)
    assert pesos["sustainability"] == pytest.approx(0.083, abs=0.001)


def test_lambda_max_da_matriz_de_referencia(referencia):
    assert referencia["lambda_max"] == pytest.approx(3.066, abs=0.001)


def test_indice_de_consistencia_da_matriz_de_referencia(referencia):
    assert referencia["ci"] == pytest.approx(0.033, abs=0.001)


def test_razao_de_consistencia_da_matriz_de_referencia(referencia):
    assert referencia["cr"] == pytest.approx(0.057, abs=0.001)
    assert referencia["cr"] <= 0.10  # julgamento aceitável (§6.4)


# --- Invariantes (§6.3) ----------------------------------------------------


def test_pesos_somam_um_e_sao_positivos(referencia):
    pesos = referencia["weights"]
    assert sum(pesos.values()) == pytest.approx(1.0, abs=1e-9)
    assert all(peso > 0 for peso in pesos.values())


def test_ordem_das_prioridades(referencia):
    pesos = referencia["weights"]
    assert pesos["security"] > pesos["performance"] > pesos["sustainability"]


def test_metodo_das_potencias_nao_reproduz_a_fixture():
    """
    Documenta a divergência que motivou a mudança: o método anterior erra o
    primeiro peso em ~0.007, fora da tolerância da diretriz.
    """
    w, _, _, _ = priority_vector(MATRIZ, method="eigenvector", random_index_table=RANDOM_INDEX)
    assert w[0] == pytest.approx(0.7306, abs=0.001)
    assert abs(w[0] - 0.724) > 0.005


def test_metodo_desconhecido_falha_alto():
    with pytest.raises(ValueError):
        priority_vector(MATRIZ, method="media_geometrica")


# --- Consistência fora do limite (§6.4 e §42.1) ----------------------------


def test_julgamentos_circulares_estouram_o_limite():
    """A > B, B > C e C > A: incoerência que o CR precisa denunciar."""
    circular = np.array(
        [
            [1.0, 5.0, 1.0 / 5.0],
            [1.0 / 5.0, 1.0, 5.0],
            [5.0, 1.0 / 5.0, 1.0],
        ]
    )
    _, _, _, cr = priority_vector(circular, method="column_mean", random_index_table=RANDOM_INDEX)
    assert cr > 0.10


def test_matriz_perfeitamente_consistente_tem_cr_zero():
    consistente = np.array(
        [
            [1.0, 2.0, 4.0],
            [1.0 / 2.0, 1.0, 2.0],
            [1.0 / 4.0, 1.0 / 2.0, 1.0],
        ]
    )
    w, lambda_max, ci, cr = priority_vector(
        consistente, method="column_mean", random_index_table=RANDOM_INDEX
    )
    assert lambda_max == pytest.approx(3.0, abs=1e-9)
    assert ci == pytest.approx(0.0, abs=1e-9)
    assert cr == pytest.approx(0.0, abs=1e-9)
    # Numa matriz consistente os dois métodos coincidem.
    w_auto, *_ = priority_vector(consistente, method="eigenvector", random_index_table=RANDOM_INDEX)
    assert np.allclose(w, w_auto, atol=1e-9)


def test_reciprocidade_e_diagonal_da_fixture():
    for i in range(3):
        assert MATRIZ[i][i] == 1.0
        for j in range(3):
            assert MATRIZ[i][j] == pytest.approx(1.0 / MATRIZ[j][i])


def test_pipeline_completo_reproduz_a_fixture():
    """Mesmo resultado passando pelos julgamentos, não só pela matriz crua."""
    judgments = {
        "security|performance": {"ratio": 5.0},
        "security|sustainability": {"ratio": 7.0},
        "performance|sustainability": {"ratio": 3.0},
    }
    resultado = derive_criteria_weights(
        judgments,
        ORDEM,
        method="column_mean",
        consistency_threshold=0.10,
        random_index_table=RANDOM_INDEX,
    )

    _, matriz, faltando = judgments_to_pairwise_matrix(judgments, ORDEM)
    assert not faltando
    assert np.allclose(matriz, MATRIZ, atol=1e-9)

    assert resultado["weights"]["security"] == pytest.approx(0.724, abs=0.001)
    assert resultado["lambda_max"] == pytest.approx(3.066, abs=0.001)
    assert resultado["consistency_index"] == pytest.approx(0.033, abs=0.001)
    assert resultado["random_index"] == 0.58
    assert resultado["consistency_ratio"] == pytest.approx(0.057, abs=0.001)
    assert resultado["is_consistent"] is True
    assert resultado["weight_method"] == "column_mean"
