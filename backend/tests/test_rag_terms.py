"""
Termos do Quadro 27 encontrados no trecho (§4.4 e §5.2).

O Quadro 27 orienta a construção da consulta; este é o caminho de volta — dado
o trecho recuperado, quais termos daquele indicador o documento traz. O que os
testes protegem é a leitura do relatório: uma tag errada afirma, para quem
audita, que o documento diz uma palavra que ele não diz.

Os casos difíceis vêm todos do texto real dos PDFs: acento que o relatório não
usa, quebra de linha no meio da expressão, sigla contida em sigla maior.
"""

import pytest

from rag.terms import terms_found

TERMOS = (
    "PUE",
    "A-PUE",
    "Power Usage Effectiveness",
    "eficiência energética",
    "energy efficiency",
    "WUE",
)


def test_termo_presente_vira_tag():
    assert terms_found("Our data center PUE was 1.15 in 2024.", TERMOS) == ["PUE"]


def test_termo_ausente_nao_vira_tag():
    assert terms_found("This section is about billing.", TERMOS) == []


def test_texto_vazio_e_lista_vazia_nao_quebram():
    assert terms_found("", TERMOS) == []
    assert terms_found("PUE", ()) == []


def test_a_tag_usa_a_grafia_do_quadro_e_nao_a_do_documento():
    """
    A tag nomeia o termo da pesquisa. "Eficiencia Energetica" no relatório é o
    "eficiência energética" do Quadro 27 — e é assim que aparece no relatório.
    """
    assert terms_found("Eficiencia Energetica dos data centers", TERMOS) == [
        "eficiência energética"
    ]


def test_caixa_nao_separa_termos():
    assert terms_found("POWER USAGE EFFECTIVENESS", TERMOS) == [
        "Power Usage Effectiveness"
    ]


def test_quebra_de_linha_do_pdf_nao_separa_a_expressao():
    """O extrator de texto corta a expressão ao fim da linha; o termo é um só."""
    assert terms_found("Power Usage\nEffectiveness (PUE)", TERMOS) == [
        "Power Usage Effectiveness",
        "PUE",
    ]


def test_termo_dentro_de_palavra_nao_casa():
    """Sem fronteira, o "ms" da linha de latência casaria dentro de "terms"."""
    assert terms_found("Many terms and items.", ("ms",)) == []
    assert terms_found("Latência média de 45 ms.", ("ms",)) == ["ms"]


def test_sigla_contida_em_sigla_maior_nao_vira_tag_propria():
    """No texto, "A-PUE" anuncia A-PUE; anunciar também PUE duplica a leitura."""
    assert terms_found("The A-PUE metric is reported.", TERMOS) == ["A-PUE"]


def test_a_ordem_e_a_do_trecho():
    """A tag acompanha a leitura do trecho, não a ordem da lista do Quadro."""
    texto = "WUE de 0,15 L/kWh; a eficiência energética também melhorou."
    assert terms_found(texto, TERMOS) == ["WUE", "eficiência energética"]


def test_termo_repetido_aparece_uma_vez():
    assert terms_found("PUE, PUE e mais PUE.", TERMOS) == ["PUE"]


@pytest.mark.parametrize("texto", ["CO₂e", "CO2e"])
def test_subscrito_do_quadro_casa_com_a_forma_simples_do_documento(texto):
    """O Quadro escreve com subscrito; os relatórios escrevem em ASCII."""
    assert terms_found(f"emissões medidas em {texto}", ("CO₂e",)) == ["CO₂e"]
