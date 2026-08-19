"""
Conversão determinística das comparações do bloco D para a escala de Saaty.

O que estes testes protegem é a promessa central do bloco D: o gestor responde
"qual dimensão" e "com que intensidade", e a razão que entra na matriz do AHP sai
disso por uma regra fixa — sem LLM, sem número vindo do cliente, e com o mesmo
resultado a cada execução.
"""

import pytest

import pairwise
from pairwise import EQUAL, PairwiseError

LEFT = "sustainability"
RIGHT = "security"


# --- Igual importância -----------------------------------------------------


def test_igual_importancia_vale_1_nos_dois_sentidos():
    resolved = pairwise.resolve(LEFT, RIGHT, EQUAL, None)
    assert resolved["saaty_intensity"] == 1
    assert resolved["matrix_value"] == 1
    assert resolved["intensity"] is None
    # a_ji = 1/a_ij = 1: a indiferença é simétrica por construção.
    assert pairwise.matrix_value(RIGHT, LEFT, EQUAL, None) == 1


def test_igual_importancia_nao_admite_intensidade():
    """"Igual + moderadamente" não é uma resposta pela metade: é inválida."""
    with pytest.raises(PairwiseError):
        pairwise.resolve(LEFT, RIGHT, EQUAL, "moderate")


# --- Dimensão da esquerda preferida ----------------------------------------


@pytest.mark.parametrize(
    "intensity,expected",
    [("moderate", 3), ("strong", 5), ("very_strong", 7), ("extreme", 9)],
)
def test_esquerda_preferida_usa_a_intensidade_direta(intensity, expected):
    resolved = pairwise.resolve(LEFT, RIGHT, LEFT, intensity)
    assert resolved["saaty_intensity"] == expected
    assert resolved["matrix_value"] == expected


# --- Dimensão da direita preferida (recíprocos) ----------------------------


@pytest.mark.parametrize(
    "intensity,saaty",
    [("moderate", 3), ("strong", 5), ("very_strong", 7), ("extreme", 9)],
)
def test_direita_preferida_usa_o_reciproco(intensity, saaty):
    resolved = pairwise.resolve(LEFT, RIGHT, RIGHT, intensity)
    # A intensidade informada é a mesma; o que muda é a direção da razão.
    assert resolved["saaty_intensity"] == saaty
    assert resolved["matrix_value"] == pytest.approx(1 / saaty)


def test_direita_fortemente_da_um_quinto():
    assert pairwise.matrix_value(LEFT, RIGHT, RIGHT, "strong") == pytest.approx(0.2)


def test_direita_extremamente_da_um_nono():
    assert pairwise.matrix_value(LEFT, RIGHT, RIGHT, "extreme") == pytest.approx(1 / 9)


def test_mesma_intensidade_em_direcoes_opostas_sao_reciprocas():
    a = pairwise.matrix_value(LEFT, RIGHT, LEFT, "strong")
    b = pairwise.matrix_value(LEFT, RIGHT, RIGHT, "strong")
    assert a * b == pytest.approx(1.0)


# --- Validação --------------------------------------------------------------


def test_preferencia_sem_intensidade_e_invalida():
    with pytest.raises(PairwiseError):
        pairwise.resolve(LEFT, RIGHT, RIGHT, None)


def test_intensidade_desconhecida_e_invalida():
    with pytest.raises(PairwiseError):
        pairwise.resolve(LEFT, RIGHT, LEFT, "muitissimo")


def test_preferencia_fora_do_par_e_invalida():
    with pytest.raises(PairwiseError):
        pairwise.resolve(LEFT, RIGHT, "performance", "strong")


def test_par_com_a_mesma_dimensao_dos_dois_lados_e_invalido():
    with pytest.raises(PairwiseError):
        pairwise.resolve(LEFT, LEFT, LEFT, "strong")


def test_dimensao_fora_do_conjunto_de_criterios_e_recusada():
    with pytest.raises(PairwiseError):
        pairwise.resolve(LEFT, "custo", EQUAL, None, ("sustainability", "performance", "security"))


# --- Descrição legível (memória de cálculo) --------------------------------


def test_descricao_nomeia_a_dimensao_priorizada():
    assert (
        pairwise.describe(LEFT, RIGHT, RIGHT, "strong")
        == "Segurança da Informação é fortemente mais importante que Sustentabilidade"
    )


def test_descricao_da_esquerda_inverte_a_frase():
    assert (
        pairwise.describe(LEFT, RIGHT, LEFT, "moderate")
        == "Sustentabilidade é moderadamente mais importante que Segurança da Informação"
    )


def test_descricao_da_indiferenca_nao_menciona_intensidade():
    texto = pairwise.describe(LEFT, RIGHT, EQUAL, None)
    assert texto == "Sustentabilidade e Segurança da Informação possuem igual importância"


# --- Compatibilidade com o formato antigo (alternativa em texto) -----------


@pytest.mark.parametrize(
    "choice,expected",
    [
        ("Sustentabilidade é fortemente mais importante que Segurança da Informação", 5.0),
        ("Sustentabilidade é muito fortemente mais importante que Segurança da Informação", 7.0),
        ("As duas dimensões possuem igual importância", 1.0),
        ("Segurança da Informação é moderadamente mais importante que Sustentabilidade", 1 / 3),
        ("Segurança da Informação é extremamente mais importante que Sustentabilidade", 1 / 9),
    ],
)
def test_envio_antigo_produz_a_mesma_razao(choice, expected):
    resolved = pairwise.from_legacy_choice(choice, (LEFT, RIGHT))
    assert resolved["matrix_value"] == pytest.approx(expected)


def test_envio_antigo_vira_resposta_estruturada():
    resolved = pairwise.from_legacy_choice(
        "Segurança da Informação é fortemente mais importante que Sustentabilidade", (LEFT, RIGHT)
    )
    assert resolved["preference"] == RIGHT
    assert resolved["intensity"] == "strong"
    assert resolved["matrix_value"] == pytest.approx(0.2)


def test_texto_desconhecido_nao_inventa_julgamento():
    assert pairwise.from_legacy_choice("qualquer coisa", (LEFT, RIGHT)) is None
    assert pairwise.from_legacy_choice("", (LEFT, RIGHT)) is None
