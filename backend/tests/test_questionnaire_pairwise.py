"""
Fronteira da API: o que o questionário envia → julgamentos do AHP.

Cobre as duas garantias do bloco D no servidor: nenhuma razão de Saaty entra
pronta pelo payload, e uma comparação incompleta ou incoerente é recusada antes
de virar peso.
"""

import pytest
from pydantic import ValidationError

from schemas import QuestionAnswer, QuestionnaireResponse

IDENTITY = {"respondent": "gestor@ufscar.br", "respondent_role": "Coordenador de TI"}


def comparison(question_id, left, right, preference, intensity=None):
    return {
        "question_id": question_id,
        "question_text": f"Entre {left} e {right}, qual dimensão deve ter maior prioridade?",
        "choice": None,
        "text": None,
        "pairwise": {
            "left": left,
            "right": right,
            "preference": preference,
            "intensity": intensity,
        },
    }


def response(*answers):
    return QuestionnaireResponse(**IDENTITY, answers=list(answers))


# --- Julgamentos ------------------------------------------------------------


def test_tres_comparacoes_viram_tres_julgamentos():
    q = response(
        comparison("comp_sust_perf", "sustainability", "performance", "sustainability", "moderate"),
        comparison("comp_sust_sec", "sustainability", "security", "security", "strong"),
        comparison("comp_perf_sec", "performance", "security", "equal"),
    )
    judgments = q.pairwise_judgments()

    assert set(judgments) == {
        "sustainability|performance",
        "sustainability|security",
        "performance|security",
    }
    assert judgments["sustainability|performance"]["ratio"] == pytest.approx(3.0)
    assert judgments["sustainability|security"]["ratio"] == pytest.approx(0.2)
    assert judgments["performance|security"]["ratio"] == pytest.approx(1.0)


def test_julgamento_guarda_a_resposta_e_nao_so_o_numero():
    q = response(comparison("comp_sust_sec", "sustainability", "security", "security", "strong"))
    j = q.pairwise_judgments()["sustainability|security"]

    assert j["preference"] == "security"
    assert j["intensity"] == "strong"
    assert j["saaty_intensity"] == 5
    assert j["choice"] == (
        "Segurança da Informação é fortemente mais importante que Sustentabilidade"
    )


def test_par_novo_nao_precisa_de_registro_no_codigo():
    """O processamento é pelo tipo da resposta, não por uma lista de question_ids."""
    q = response(comparison("q42", "performance", "security", "performance", "extreme"))
    assert q.pairwise_judgments()["performance|security"]["ratio"] == pytest.approx(9.0)


def test_payload_nao_tem_por_onde_enviar_um_peso_pronto():
    """Campos desconhecidos como `ahp_value` são descartados na desserialização."""
    answer = QuestionAnswer(
        question_id="comp_sust_sec",
        pairwise={
            "left": "sustainability",
            "right": "security",
            "preference": "security",
            "intensity": "strong",
            "ahp_value": 500,
        },
    )
    assert not hasattr(answer.pairwise, "ahp_value")
    assert answer.resolved_pairwise()["matrix_value"] == pytest.approx(0.2)


# --- Validação na entrada ---------------------------------------------------


def test_preferencia_sem_intensidade_nao_passa_pela_api():
    with pytest.raises(ValidationError):
        response(comparison("comp_sust_sec", "sustainability", "security", "security"))


def test_igual_com_intensidade_nao_passa_pela_api():
    with pytest.raises(ValidationError):
        response(comparison("comp_sust_sec", "sustainability", "security", "equal", "moderate"))


def test_dimensao_desconhecida_nao_passa_pela_api():
    with pytest.raises(ValidationError):
        response(comparison("comp_x", "sustainability", "custo", "custo", "strong"))


# --- Separação entre os blocos ---------------------------------------------


def test_relevancia_ignora_as_comparacoes():
    """Blocos A–C alimentam a média 1–5; o bloco D não entra nela (nem vice-versa)."""
    q = response(
        {"question_id": "sust_q1", "choice": "Decisivo (critério indispensável)", "text": None},
        {"question_id": "sec_q1", "choice": "Relevante", "text": None},
        comparison("comp_sust_sec", "sustainability", "security", "security", "strong"),
    )
    relevance = q.relevance_by_criterion()

    assert relevance["sustainability"] == 5.0
    assert relevance["security"] == 3.0
    assert relevance["performance"] == 3.0  # sem resposta: ponto neutro


def test_comparacao_nao_aparece_como_resposta_sem_efeito():
    q = response(comparison("comp_sust_sec", "sustainability", "security", "equal"))
    assert q.unscored_answers() == []


def test_llm_recebe_a_comparacao_em_texto():
    """A justificativa é redigida a partir de frases, não de estruturas."""
    q = response(comparison("comp_sust_sec", "sustainability", "security", "security", "strong"))
    (pair,) = q.qa_for_llm()
    assert "fortemente mais importante" in pair["resposta"]


# --- Registro de auditoria --------------------------------------------------


def test_payload_de_auditoria_preenche_a_resposta_em_texto():
    """
    `submission_answers.choice` continua tendo o que foi respondido em cada
    pergunta, enquanto o julgamento estruturado permanece íntegro no payload.
    """
    q = response(comparison("comp_sust_sec", "sustainability", "security", "security", "strong"))
    (answer,) = q.audit_payload()["answers"]

    assert answer["choice"] == (
        "Segurança da Informação é fortemente mais importante que Sustentabilidade"
    )
    assert answer["pairwise"]["preference"] == "security"
    assert answer["pairwise"]["intensity"] == "strong"


# --- Envios no formato antigo ----------------------------------------------


def test_envio_antigo_continua_produzindo_os_mesmos_pesos():
    """
    Uma análise gravada antes da mudança de formato tem a alternativa em texto e
    nenhum campo `pairwise`. Ela precisa continuar reproduzível.
    """
    q = response(
        {
            "question_id": "comp_sust_sec",
            "question_text": "**18.** Ao comparar Sustentabilidade e Segurança da Informação…",
            "choice": "Segurança da Informação é fortemente mais importante que Sustentabilidade",
            "text": None,
        }
    )
    j = q.pairwise_judgments()["sustainability|security"]

    assert j["ratio"] == pytest.approx(0.2)
    assert j["preference"] == "security"
    assert j["intensity"] == "strong"
