"""
Matriz do AHP montada a partir dos julgamentos do bloco D.

A mudança do bloco D foi de UX e de modelagem da resposta; a matemática do AHP
não mudou. Estes testes fixam as propriedades que a matriz precisa manter para
que os pesos, λmax, IC e RC continuem significando o que sempre significaram.
"""

import numpy as np
import pytest

from ahp import CONSISTENCY_THRESHOLD, derive_criteria_weights, judgments_to_pairwise_matrix
from schemas import CRITERIA, QuestionnaireResponse

IDENTITY = {"respondent": "gestor@ufscar.br", "respondent_role": "Coordenador de TI"}


def comparison(question_id, left, right, preference, intensity=None):
    return {
        "question_id": question_id,
        "choice": None,
        "text": None,
        "pairwise": {
            "left": left,
            "right": right,
            "preference": preference,
            "intensity": intensity,
        },
    }


def judgments_from(*answers):
    return QuestionnaireResponse(**IDENTITY, answers=list(answers)).pairwise_judgments()


def test_matriz_e_reciproca_com_diagonal_unitaria():
    judgments = judgments_from(
        comparison("c1", "sustainability", "performance", "performance", "moderate"),
        comparison("c2", "sustainability", "security", "security", "strong"),
        comparison("c3", "performance", "security", "security", "moderate"),
    )
    _, matrix, missing = judgments_to_pairwise_matrix(judgments, CRITERIA)

    n = len(CRITERIA)
    assert missing == []
    for i in range(n):
        assert matrix[i][i] == 1.0  # a_ii = 1
        for j in range(n):
            assert matrix[i][j] == pytest.approx(1.0 / matrix[j][i])  # a_ij = 1/a_ji


def test_direcao_do_par_define_a_celula():
    judgments = judgments_from(
        comparison("c2", "sustainability", "security", "security", "strong"),
    )
    keys, matrix, _ = judgments_to_pairwise_matrix(judgments, CRITERIA)
    i, j = keys.index("sustainability"), keys.index("security")

    assert matrix[i][j] == pytest.approx(0.2)  # 1/5
    assert matrix[j][i] == pytest.approx(5.0)


def test_igual_importancia_deixa_as_duas_celulas_em_1():
    judgments = judgments_from(comparison("c3", "performance", "security", "equal"))
    keys, matrix, _ = judgments_to_pairwise_matrix(judgments, CRITERIA)
    i, j = keys.index("performance"), keys.index("security")

    assert matrix[i][j] == 1.0
    assert matrix[j][i] == 1.0


def test_par_sem_resposta_fica_explicito_como_lacuna():
    judgments = judgments_from(
        comparison("c2", "sustainability", "security", "security", "strong"),
    )
    _, _, missing = judgments_to_pairwise_matrix(judgments, CRITERIA)
    assert set(missing) == {"sustainability|performance", "performance|security"}


def test_tudo_igual_da_pesos_iguais_e_consistencia_perfeita():
    result = derive_criteria_weights(
        judgments_from(
            comparison("c1", "sustainability", "performance", "equal"),
            comparison("c2", "sustainability", "security", "equal"),
            comparison("c3", "performance", "security", "equal"),
        ),
        CRITERIA,
    )

    for c in CRITERIA:
        assert result["weights"][c] == pytest.approx(1 / 3)
    assert result["lambda_max"] == pytest.approx(len(CRITERIA))
    assert result["consistency_ratio"] == pytest.approx(0.0)
    assert result["is_consistent"] is True


def test_julgamentos_coerentes_ficam_abaixo_do_limite_de_saaty():
    # Segurança > Desempenho > Sustentabilidade, sem contradição de ordem.
    result = derive_criteria_weights(
        judgments_from(
            comparison("c1", "sustainability", "performance", "performance", "moderate"),
            comparison("c2", "sustainability", "security", "security", "strong"),
            comparison("c3", "performance", "security", "security", "moderate"),
        ),
        CRITERIA,
    )

    assert result["consistency_ratio"] <= CONSISTENCY_THRESHOLD
    assert result["is_consistent"] is True
    assert result["weights"]["security"] > result["weights"]["performance"]
    assert result["weights"]["performance"] > result["weights"]["sustainability"]
    assert sum(result["weights"].values()) == pytest.approx(1.0)


def test_julgamentos_circulares_estouram_o_limite():
    # A > B, B > C, mas C > A: contradição que o RC precisa denunciar.
    result = derive_criteria_weights(
        judgments_from(
            comparison("c1", "sustainability", "performance", "sustainability", "extreme"),
            comparison("c3", "performance", "security", "performance", "extreme"),
            comparison("c2", "sustainability", "security", "security", "extreme"),
        ),
        CRITERIA,
    )

    assert result["consistency_ratio"] > CONSISTENCY_THRESHOLD
    assert result["is_consistent"] is False


def test_pesos_sao_o_autovetor_principal_da_matriz():
    """A·w = λmax·w — verifica que os pesos vêm mesmo do autovetor, não de uma média."""
    judgments = judgments_from(
        comparison("c1", "sustainability", "performance", "performance", "moderate"),
        comparison("c2", "sustainability", "security", "security", "strong"),
        comparison("c3", "performance", "security", "security", "moderate"),
    )
    result = derive_criteria_weights(judgments, CRITERIA)
    keys, matrix, _ = judgments_to_pairwise_matrix(judgments, CRITERIA)

    w = np.array([result["weights"][k] for k in keys])
    assert np.allclose(matrix @ w, result["lambda_max"] * w, atol=1e-6)


def test_mesma_resposta_produz_sempre_os_mesmos_pesos():
    """Determinismo: nenhuma etapa do bloco D depende de LLM ou de aleatoriedade."""
    answers = [
        comparison("c1", "sustainability", "performance", "performance", "very_strong"),
        comparison("c2", "sustainability", "security", "security", "extreme"),
        comparison("c3", "performance", "security", "equal"),
    ]
    primeiro = derive_criteria_weights(judgments_from(*answers), CRITERIA)
    segundo = derive_criteria_weights(judgments_from(*answers), CRITERIA)

    assert primeiro == segundo


def test_formato_novo_e_antigo_produzem_a_mesma_matriz():
    """Análises salvas antes da mudança continuam reproduzíveis célula a célula."""
    novo = judgments_from(
        comparison("comp_sust_perf", "sustainability", "performance", "performance", "moderate"),
        comparison("comp_sust_sec", "sustainability", "security", "security", "strong"),
        comparison("comp_perf_sec", "performance", "security", "security", "moderate"),
    )
    antigo = judgments_from(
        {
            "question_id": "comp_sust_perf",
            "choice": "Desempenho Operacional é moderadamente mais importante que Sustentabilidade",
            "text": None,
        },
        {
            "question_id": "comp_sust_sec",
            "choice": "Segurança da Informação é fortemente mais importante que Sustentabilidade",
            "text": None,
        },
        {
            "question_id": "comp_perf_sec",
            "choice": "Segurança da Informação é moderadamente mais importante que Desempenho Operacional",
            "text": None,
        },
    )

    _, m_novo, _ = judgments_to_pairwise_matrix(novo, CRITERIA)
    _, m_antigo, _ = judgments_to_pairwise_matrix(antigo, CRITERIA)
    assert np.allclose(m_novo, m_antigo)
