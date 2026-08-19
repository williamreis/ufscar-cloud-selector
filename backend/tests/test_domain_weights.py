"""
Pesos locais e globais dos indicadores (diretriz §5.1, §7 e §42.2).

O que estes testes protegem é a fronteira entre "não informado" e "irrelevante".
A diretriz separa as duas coisas em três proibições, e o teste correspondente a
cada uma está marcado no docstring — porque o erro aqui não aparece como falha:
aparece como um resultado plausível construído sobre um julgamento que o gestor
nunca deu.
"""

import json

import pytest

from domain.methodology import MethodologyConfigError, load_methodology
from domain.weights import (
    RELEVANCE_ANSWERED,
    RELEVANCE_MISSING,
    RELEVANCE_UNKNOWN,
    collect_relevance,
    weights_from_answers,
)

PESOS_DIMENSAO = {"sustainability": 0.2, "performance": 0.3, "security": 0.5}

DECISIVO = "Decisivo (critério indispensável)"
MUITO = "Muito relevante"
RELEVANTE = "Relevante"
POUCO = "Pouco relevante"
IRRELEVANTE = "Irrelevante"
NAO_SEI = "Não sei / não tenho informações suficientes"


@pytest.fixture
def metodologia():
    return load_methodology()


def _todas(metodologia, resposta=RELEVANTE):
    return {i.question_id: resposta for i in metodologia.indicators if i.question_id}


# --- Coeficientes ----------------------------------------------------------


def test_alternativa_vira_coeficiente(metodologia):
    relevancias = collect_relevance({"sust_q1": DECISIVO}, metodologia)
    energia = next(r for r in relevancias if r.indicator_id == "sustainability_energy_efficiency")
    assert energia.coefficient == 5.0
    assert energia.state == RELEVANCE_ANSWERED


def test_nao_sei_vira_null_e_nao_zero(metodologia):
    """§5.1: registrar como null; não converter silenciosamente em 0."""
    relevancias = collect_relevance({"sust_q1": NAO_SEI}, metodologia)
    energia = next(r for r in relevancias if r.indicator_id == "sustainability_energy_efficiency")
    assert energia.coefficient is None
    assert energia.state == RELEVANCE_UNKNOWN


def test_nao_sei_e_pergunta_ausente_sao_estados_distintos(metodologia):
    """
    Os dois impedem o indicador de pesar, mas só um é resposta do gestor — e o
    relatório precisa poder dizer qual foi qual.
    """
    relevancias = collect_relevance({"sust_q1": NAO_SEI}, metodologia)
    estados = {r.indicator_id: r.state for r in relevancias}
    assert estados["sustainability_energy_efficiency"] == RELEVANCE_UNKNOWN
    assert estados["sustainability_renewable_energy"] == RELEVANCE_MISSING


def test_irrelevante_ainda_e_uma_resposta(metodologia):
    """TODO ACADÊMICO 01, decidido: `irrelevante` = 1 — pesa pouco, mas pesa."""
    relevancias = collect_relevance({"sust_q1": IRRELEVANTE}, metodologia)
    energia = next(r for r in relevancias if r.indicator_id == "sustainability_energy_efficiency")
    assert energia.coefficient == 1.0
    assert energia.state == RELEVANCE_ANSWERED
    assert energia.is_valid


def test_irrelevante_e_nao_sei_nao_se_confundem(metodologia):
    """
    "Importa pouco" é julgamento do gestor; "não sei" é ausência dele. Colapsar
    os dois é exatamente o que a §5.1 proíbe — e é por isso que um vira
    coeficiente e o outro vira nulo.
    """
    relevancias = collect_relevance(
        {"sust_q1": IRRELEVANTE, "sust_q2": NAO_SEI}, metodologia
    )
    por_id = {r.indicator_id: r for r in relevancias}

    irrelevante = por_id["sustainability_energy_efficiency"]
    nao_sei = por_id["sustainability_renewable_energy"]

    assert (irrelevante.coefficient, irrelevante.state) == (1.0, RELEVANCE_ANSWERED)
    assert (nao_sei.coefficient, nao_sei.state) == (None, RELEVANCE_UNKNOWN)


# --- Pesos locais (§42.2) --------------------------------------------------


def test_soma_dos_pesos_locais_e_1_por_dimensao(metodologia):
    conjunto = weights_from_answers(_todas(metodologia), PESOS_DIMENSAO, metodologia)
    for dimensao in metodologia.dimensions:
        assert conjunto.local_weight_sum(dimensao) == pytest.approx(1.0, abs=1e-9)


def test_pesos_locais_ficam_entre_0_e_1(metodologia):
    conjunto = weights_from_answers(_todas(metodologia), PESOS_DIMENSAO, metodologia)
    locais = [w.local_weight for w in conjunto.weights if w.local_weight is not None]
    assert locais and all(0.0 <= valor <= 1.0 for valor in locais)


def test_peso_local_e_proporcional_ao_coeficiente(metodologia):
    """l_j = v_j / Σ v_k, dentro da dimensão."""
    respostas = {
        "sec_q1": DECISIVO,     # 5
        "sec_q2": RELEVANTE,    # 3
        "sec_q3": POUCO,        # 2
        "sec_q4": IRRELEVANTE,  # 1  → total 11
    }
    conjunto = weights_from_answers(respostas, PESOS_DIMENSAO, metodologia)
    por_id = conjunto.by_indicator()
    assert por_id["security_certifications"].local_weight == pytest.approx(5 / 11)
    assert por_id["security_backup_recovery"].local_weight == pytest.approx(3 / 11)
    assert por_id["security_iam"].local_weight == pytest.approx(2 / 11)
    assert por_id["security_encryption"].local_weight == pytest.approx(1 / 11)


def test_dimensao_toda_irrelevante_ainda_produz_pesos(metodologia):
    """
    Com `irrelevante` = 1 há sempre denominador positivo enquanto houver
    resposta: uma dimensão inteiramente irrelevante distribui pesos iguais entre
    seus indicadores e **não** cai em revisão.

    Isso é diferente do fallback proibido pela §5.1: ali o problema é inventar
    pesos onde não houve resposta; aqui houve resposta em todas as perguntas, e
    ela foi a mesma.
    """
    respostas = {q: IRRELEVANTE for q in ("sec_q1", "sec_q2", "sec_q3", "sec_q4")}
    conjunto = weights_from_answers(respostas, PESOS_DIMENSAO, metodologia)

    assert "security" not in conjunto.dimensions_needing_review
    seguranca = [w for w in conjunto.weights if w.dimension == "security"]
    assert all(w.local_weight == pytest.approx(0.25) for w in seguranca)
    assert conjunto.local_weight_sum("security") == pytest.approx(1.0)


def test_denominador_zerado_pede_revisao_em_vez_de_dividir_por_zero(tmp_path):
    """
    Guarda de mecanismo, independente da escala em vigor: se uma configuração
    futura zerar todos os coeficientes de uma dimensão, o cálculo não divide por
    zero nem distribui pesos iguais — ele declara que precisa de revisão.
    """
    escalas = {
        "relevance_coefficients": {
            "values": {"irrelevante": 0, "nao_sei": None},
            "labels": {IRRELEVANTE: "irrelevante", NAO_SEI: "nao_sei"},
        },
        "default_rubrics": {"ordinal_4": {"mode": "ordinal", "categories": {"level_1": 1.0}}},
        "ahp": {"weight_method": "column_mean", "random_index": {"3": 0.58}},
    }
    caminho = tmp_path / "scales.json"
    caminho.write_text(json.dumps(escalas, ensure_ascii=False), encoding="utf-8")
    alterada = load_methodology(scales_path=caminho)

    respostas = {q: IRRELEVANTE for q in ("sec_q1", "sec_q2", "sec_q3", "sec_q4")}
    conjunto = weights_from_answers(respostas, PESOS_DIMENSAO, alterada)

    assert "security" in conjunto.dimensions_needing_review
    seguranca = [w for w in conjunto.weights if w.dimension == "security"]
    assert all(w.local_weight is None for w in seguranca)
    # O coeficiente respondido continua gravado, mesmo sem peso derivado dele.
    assert all(w.relevance_coefficient == 0.0 for w in seguranca)


def test_indicador_sem_resposta_fica_sem_peso_e_nao_com_zero(metodologia):
    """§5.1: `null` não é evidência de irrelevância — e zero afirmaria que é."""
    respostas = {"sec_q1": DECISIVO, "sec_q2": NAO_SEI}
    conjunto = weights_from_answers(respostas, PESOS_DIMENSAO, metodologia)
    por_id = conjunto.by_indicator()

    assert por_id["security_certifications"].local_weight == pytest.approx(1.0)
    assert por_id["security_backup_recovery"].local_weight is None
    assert por_id["security_backup_recovery"].global_weight is None


def test_nao_sei_nao_entra_no_denominador(metodologia):
    """O indicador sai da normalização; não puxa os demais para baixo."""
    respostas = {"sec_q1": DECISIVO, "sec_q2": DECISIVO, "sec_q3": NAO_SEI}
    conjunto = weights_from_answers(respostas, PESOS_DIMENSAO, metodologia)
    por_id = conjunto.by_indicator()
    assert por_id["security_certifications"].local_weight == pytest.approx(0.5)
    assert por_id["security_backup_recovery"].local_weight == pytest.approx(0.5)


def test_dimensao_sem_resposta_valida_pede_revisao(metodologia):
    """§5.1, NÃO FAZER: fallback silencioso para pesos iguais."""
    respostas = {"sec_q1": NAO_SEI, "sec_q2": NAO_SEI, "sec_q3": NAO_SEI, "sec_q4": NAO_SEI}
    conjunto = weights_from_answers(respostas, PESOS_DIMENSAO, metodologia)

    assert "security" in conjunto.dimensions_needing_review
    seguranca = [w for w in conjunto.weights if w.dimension == "security"]
    assert all(w.local_weight is None for w in seguranca)
    assert all(w.global_weight is None for w in seguranca)


def test_dimensao_completa_nao_pede_revisao(metodologia):
    conjunto = weights_from_answers(_todas(metodologia), PESOS_DIMENSAO, metodologia)
    assert conjunto.dimensions_needing_review == ()


# --- Pesos globais (§7 e §42.2) --------------------------------------------


def test_peso_global_e_o_produto_dos_dois_niveis(metodologia):
    conjunto = weights_from_answers(_todas(metodologia), PESOS_DIMENSAO, metodologia)
    for peso in conjunto.weights:
        esperado = peso.dimension_weight * peso.local_weight
        assert peso.global_weight == pytest.approx(esperado)


def test_soma_dos_pesos_globais_e_1(metodologia):
    """§7: antes de qualquer exclusão, Σ w_j ≈ 1."""
    conjunto = weights_from_answers(_todas(metodologia), PESOS_DIMENSAO, metodologia)
    assert sum(conjunto.global_weights().values()) == pytest.approx(1.0, abs=1e-9)


def test_soma_global_por_dimensao_reproduz_o_peso_da_dimensao(metodologia):
    conjunto = weights_from_answers(_todas(metodologia), PESOS_DIMENSAO, metodologia)
    for dimensao, peso_dimensao in PESOS_DIMENSAO.items():
        soma = sum(
            w.global_weight for w in conjunto.weights
            if w.dimension == dimensao and w.global_weight is not None
        )
        assert soma == pytest.approx(peso_dimensao, abs=1e-9)


def test_tres_niveis_convivem_sem_um_sobrescrever_o_outro(metodologia):
    """§7: persistir separadamente; nunca sobrescrever um nível com outro."""
    conjunto = weights_from_answers({"sec_q1": DECISIVO}, PESOS_DIMENSAO, metodologia)
    peso = conjunto.by_indicator()["security_certifications"]
    assert peso.relevance_coefficient == 5.0
    assert peso.local_weight == pytest.approx(1.0)
    assert peso.dimension_weight == 0.5
    assert peso.global_weight == pytest.approx(0.5)


# --- Mudança de escala por configuração (§42.2) ----------------------------


def test_mudar_a_escala_muda_os_pesos_sem_tocar_no_codigo(tmp_path, metodologia):
    """
    A decisão do TODO 01 é reversível por configuração: aqui `irrelevante` passa
    a valer 0 e o cálculo acompanha, sem alteração de código.
    """
    escalas = {
        "relevance_coefficients": {
            "values": {"decisivo": 5, "irrelevante": 0, "nao_sei": None},
            "labels": {
                DECISIVO: "decisivo",
                IRRELEVANTE: "irrelevante",
                NAO_SEI: "nao_sei",
            },
        },
        "default_rubrics": {"ordinal_4": {"mode": "ordinal", "categories": {"level_1": 1.0}}},
        "ahp": {"weight_method": "column_mean", "random_index": {"3": 0.58}},
    }
    caminho = tmp_path / "scales.json"
    caminho.write_text(json.dumps(escalas, ensure_ascii=False), encoding="utf-8")

    alterada = load_methodology(scales_path=caminho)
    respostas = {"sec_q1": DECISIVO, "sec_q2": IRRELEVANTE}

    # Escala em vigor (irrelevante = 1): o indicador irrelevante pesa 1 em 6.
    vigente = weights_from_answers(respostas, PESOS_DIMENSAO, metodologia).by_indicator()
    assert vigente["security_certifications"].local_weight == pytest.approx(5 / 6)
    assert vigente["security_backup_recovery"].local_weight == pytest.approx(1 / 6)

    # Escala alternativa (irrelevante = 0): ele deixa de pesar.
    com_zero = weights_from_answers(respostas, PESOS_DIMENSAO, alterada).by_indicator()
    assert com_zero["security_certifications"].local_weight == pytest.approx(1.0)
    assert com_zero["security_backup_recovery"].local_weight == pytest.approx(0.0)


# --- Validação da configuração ---------------------------------------------


def test_indicador_quantitativo_sem_direcao_e_recusado(tmp_path):
    indicadores = {
        "version": "1",
        "dimensions": {"security": {"name": "Segurança"}},
        "indicators": [
            {"id": "x", "dimension": "security", "name": "X", "data_type": "quantitative"}
        ],
    }
    (tmp_path / "indicators.json").write_text(json.dumps(indicadores), encoding="utf-8")
    with pytest.raises(MethodologyConfigError, match="direction"):
        load_methodology(indicators_path=tmp_path / "indicators.json")


def test_indicador_com_dimensao_desconhecida_e_recusado(tmp_path):
    indicadores = {
        "version": "1",
        "dimensions": {"security": {"name": "Segurança"}},
        "indicators": [
            {
                "id": "x",
                "dimension": "inventada",
                "name": "X",
                "data_type": "quantitative",
                "direction": "benefit",
            }
        ],
    }
    (tmp_path / "indicators.json").write_text(json.dumps(indicadores), encoding="utf-8")
    with pytest.raises(MethodologyConfigError, match="dimensão"):
        load_methodology(indicators_path=tmp_path / "indicators.json")


def test_dois_indicadores_na_mesma_pergunta_sao_recusados(tmp_path):
    """O mapeamento pergunta → indicador é 1:1; duplicar dobraria o peso."""
    indicadores = {
        "version": "1",
        "dimensions": {"security": {"name": "Segurança"}},
        "indicators": [
            {"id": "a", "dimension": "security", "name": "A", "data_type": "quantitative",
             "direction": "benefit", "question_id": "sec_q1"},
            {"id": "b", "dimension": "security", "name": "B", "data_type": "quantitative",
             "direction": "benefit", "question_id": "sec_q1"},
        ],
    }
    (tmp_path / "indicators.json").write_text(json.dumps(indicadores), encoding="utf-8")
    with pytest.raises(MethodologyConfigError, match="question_id"):
        load_methodology(indicators_path=tmp_path / "indicators.json")


def test_configuracao_real_carrega_e_cobre_o_questionario(metodologia):
    """Todo indicador aponta para uma pergunta, e há um indicador por pergunta fechada."""
    assert len(metodologia.indicators) == 13
    assert all(i.question_id for i in metodologia.indicators)
    assert len({i.question_id for i in metodologia.indicators}) == 13


def test_fingerprint_muda_quando_a_escala_muda(tmp_path, metodologia):
    (tmp_path / "scales.json").write_text(
        json.dumps(
            {
                "relevance_coefficients": {
                    "values": {"decisivo": 9},
                    "labels": {DECISIVO: "decisivo"},
                },
                "default_rubrics": {"ordinal_4": {"mode": "ordinal", "categories": {"level_1": 1.0}}},
                "ahp": {"weight_method": "column_mean", "random_index": {"3": 0.58}},
            }
        ),
        encoding="utf-8",
    )
    alterada = load_methodology(scales_path=tmp_path / "scales.json")
    assert alterada.fingerprint()["scales_hash"] != metodologia.fingerprint()["scales_hash"]
