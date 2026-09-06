"""
Rubricas qualitativas — o Quadro 23 da dissertação.

O quadro define cinco níveis de atendimento, com a condição da evidência e o
valor de cada um. Quatro pontuam; o quinto, "Não identificado", registra "—" e
tem tratamento próprio:

    "O nível 'Não identificado' não recebe valor igual a zero, uma vez que a
    ausência de evidência não é interpretada como desempenho inferior do
    provedor. Nessa situação, aplica-se o procedimento definido na Seção 4.4.1.3
    para informações ausentes."

É a distinção que estes testes existem para proteger. Um `nao_identificado`
convertido em 0,00 passaria despercebido — o cálculo roda, o ranking sai, e o
provedor é punido por um silêncio do documento. E a assimetria do modo binário é
da mesma família: 0,00 exige evidência **explícita** de não atendimento, não a
falta de menção.
"""

import json

import pytest

from domain.methodology import MethodologyConfigError, get_methodology, load_methodology
from domain.normalization import (
    STATUS_FOUND,
    STATUS_INVALID,
    STATUS_NOT_FOUND,
    value_from_category,
)

# Quadro 23, coluna "Nível de atendimento" → coluna "Valor".
QUADRO_23_VALORES = {
    "nao_identificado": None,
    "baixo": 0.25,
    "moderado": 0.50,
    "alto": 0.75,
    "completo": 1.00,
}

# Quadro 23, coluna "Condição da evidência", ao pé da letra.
QUADRO_23_CONDICOES = {
    "nao_identificado": "Não há evidência documental suficiente para confirmar o atendimento",
    "baixo": "Evidência limitada, indicando atendimento parcial inicial ao indicador",
    "moderado": "Evidência demonstra atendimento parcial ao indicador",
    "alto": "Evidência demonstra atendimento substancial ao indicador",
    "completo": "Evidência demonstra atendimento integral às condições previstas para o indicador",
}


@pytest.fixture
def metodologia():
    return get_methodology()


@pytest.fixture
def rubrica(metodologia):
    return metodologia.rubrics["nivel_atendimento"]


@pytest.fixture
def qualitativo(metodologia):
    return metodologia.by_id("security_certifications")


# --- Fidelidade ao Quadro 23 ------------------------------------------------


def test_niveis_e_valores_sao_os_do_quadro_23(rubrica):
    assert dict(rubrica.categories) == QUADRO_23_VALORES


@pytest.mark.parametrize("nivel,condicao", QUADRO_23_CONDICOES.items())
def test_condicoes_da_evidencia_sao_as_do_quadro_23(rubrica, nivel, condicao):
    assert rubrica.condition_for(nivel) == condicao


def test_todo_indicador_qualitativo_usa_a_escala_do_quadro_23(metodologia):
    """
    A garantia é sobre a **escala**, não sobre o nome da rubrica.

    Cada indicador qualitativo passou a ter rubrica própria, porque a condição
    genérica do quadro classificava os três provedores no mesmo nível em 96% a
    100% das células — e um indicador que dá o mesmo valor a todos ocupa peso na
    Equação 5 sem participar da ordenação. O que as rubricas específicas mudam é
    a coluna "Condição da evidência"; os níveis e os valores continuam sendo os
    do Quadro 23, e é isso que este teste protege.
    """
    qualitativos = [i for i in metodologia.indicators if i.is_qualitative]
    assert qualitativos
    assert all(i.rubric is not None for i in qualitativos)
    for indicador in qualitativos:
        assert dict(indicador.rubric.categories) == QUADRO_23_VALORES, (
            f"{indicador.id}: a rubrica {indicador.rubric.name!r} alterou a escala "
            "do Quadro 23 — os níveis e os valores não podem mudar."
        )


def test_cada_indicador_qualitativo_tem_rubrica_propria(metodologia):
    """
    Duas rubricas iguais em condição voltariam a empatar os provedores no mesmo
    nível: o refinamento só existe enquanto as condições forem distintas.
    """
    qualitativos = [i for i in metodologia.indicators if i.is_qualitative]
    nomes = [i.rubric.name for i in qualitativos]
    assert len(set(nomes)) == len(nomes), f"rubrica reaproveitada entre indicadores: {nomes}"


def test_rubricas_especificas_nao_repetem_a_condicao_generica(metodologia):
    """
    Se a condição específica for igual à do quadro, nada mudou de fato — e o
    indicador continua sem discriminar, agora com outro nome.
    """
    pontuaveis = [n for n, v in QUADRO_23_VALORES.items() if v is not None]
    for indicador in (i for i in metodologia.indicators if i.is_qualitative):
        iguais = [
            nivel
            for nivel in pontuaveis
            if indicador.rubric.condition_for(nivel) == QUADRO_23_CONDICOES[nivel]
        ]
        assert not iguais, (
            f"{indicador.id}: os níveis {iguais} repetem a condição genérica do "
            "Quadro 23 e não distinguem este indicador de nenhum outro."
        )


def test_rubricas_nao_estao_mais_provisorias():
    """TODO ACADÊMICO 02 resolvido: as categorias vêm da dissertação."""
    from config import get_settings

    escalas = json.loads(get_settings().methodology_scales_path.read_text(encoding="utf-8"))
    assert escalas["default_rubrics"]["status"] == "decided"


# --- "Não identificado" não é zero (§4.4.1.3) -------------------------------


def test_nao_identificado_nao_vira_zero(qualitativo):
    valor, status = value_from_category(qualitativo, "nao_identificado")
    assert valor is None
    assert status == STATUS_NOT_FOUND


def test_nao_identificado_e_categoria_valida_e_nao_erro(rubrica, qualitativo):
    """
    A LLM que responde "nao_identificado" acertou a regra. Tratá-la como saída
    inválida diria ao gestor que o modelo errou, quando o que houve foi o
    documento não sustentar o indicador.
    """
    assert rubrica.is_allowed("nao_identificado")
    _valor, status = value_from_category(qualitativo, "nao_identificado")
    assert status != STATUS_INVALID


def test_categoria_desconhecida_continua_invalida(qualitativo):
    """Distinta de "não identificado": aqui o modelo saiu da allowlist (§19)."""
    valor, status = value_from_category(qualitativo, "excelente")
    assert valor is None
    assert status == STATUS_INVALID


@pytest.mark.parametrize(
    "nivel,esperado",
    [(k, v) for k, v in QUADRO_23_VALORES.items() if v is not None],
)
def test_niveis_pontuaveis_convertem_pelo_quadro(qualitativo, nivel, esperado):
    valor, status = value_from_category(qualitativo, nivel)
    assert valor == pytest.approx(esperado)
    assert status == STATUS_FOUND


def test_categorias_pontuaveis_excluem_a_sem_valor(rubrica):
    assert set(rubrica.scored_categories) == {"baixo", "moderado", "alto", "completo"}


# --- Modo binário (parágrafo final do Quadro 23) ---------------------------


def test_binario_tem_a_assimetria_do_quadro_23(metodologia):
    """
    "1,00 quando a evidência estiver documentalmente comprovada e 0,00 quando
    houver evidência **explícita** de não atendimento" — e um terceiro caso, o
    silêncio do documento, que não é nenhum dos dois.
    """
    binario = metodologia.rubrics["binario"]
    assert binario.categories["comprovado"] == 1.0
    assert binario.categories["nao_atendido"] == 0.0
    assert binario.categories["nao_identificado"] is None


# --- Validação da configuração ---------------------------------------------


def _escalas(rubricas, tmp_path):
    caminho = tmp_path / "scales.json"
    caminho.write_text(
        json.dumps(
            {
                "relevance_coefficients": {
                    "values": {"decisivo": 5},
                    "labels": {"Decisivo (critério indispensável)": "decisivo"},
                },
                "default_rubrics": rubricas,
                "ahp": {"weight_method": "column_mean", "random_index": {"3": 0.58}},
            }
        ),
        encoding="utf-8",
    )
    return caminho


def test_rubrica_so_com_categorias_nulas_e_recusada(tmp_path):
    """Uma rubrica em que nada pontua não avalia nada — falha ao carregar."""
    caminho = _escalas(
        {"nivel_atendimento": {"mode": "ordinal", "categories": {"nao_identificado": None}}},
        tmp_path,
    )
    with pytest.raises(MethodologyConfigError, match="nenhuma categoria com valor"):
        load_methodology(scales_path=caminho)


def test_condicao_de_categoria_inexistente_e_recusada(tmp_path):
    """Condição órfã é sinal de rubrica editada pela metade."""
    caminho = _escalas(
        {
            "nivel_atendimento": {
                "mode": "ordinal",
                "categories": {"completo": 1.0},
                "conditions": {"completo": "ok", "inventado": "…"},
            }
        },
        tmp_path,
    )
    with pytest.raises(MethodologyConfigError, match="categorias inexistentes"):
        load_methodology(scales_path=caminho)


def test_valor_nao_numerico_continua_recusado(tmp_path):
    caminho = _escalas(
        {"nivel_atendimento": {"mode": "ordinal", "categories": {"completo": "muito"}}},
        tmp_path,
    )
    with pytest.raises(MethodologyConfigError, match="não numérico"):
        load_methodology(scales_path=caminho)


# --- O prompt carrega a regra, não só o rótulo -----------------------------


def test_prompt_leva_a_condicao_de_cada_nivel(qualitativo):
    """
    Sem a condição, "escolha entre baixo, moderado, alto e completo" não é uma
    regra — é um rótulo solto, e a classificação vira opinião do modelo.

    A condição cobrada é a **do indicador**, não a genérica do quadro: é ela que
    o modelo lê, e trocá-la é o mecanismo pelo qual a rubrica passa a distinguir
    um provedor do outro.
    """
    import evidence

    texto = evidence.describe_indicators([qualitativo])
    for nivel in qualitativo.rubric.allowed_categories:
        condicao = qualitativo.rubric.condition_for(nivel)
        assert condicao, f"nível {nivel} sem condição na rubrica {qualitativo.rubric.name!r}"
        assert f"- {nivel}: {condicao}" in texto


def test_prompt_leva_a_condicao_de_todos_os_qualitativos(metodologia):
    """A regra vale para os nove, não só para o indicador da fixture."""
    import evidence

    for indicador in (i for i in metodologia.indicators if i.is_qualitative):
        texto = evidence.describe_indicators([indicador])
        for nivel in indicador.rubric.allowed_categories:
            condicao = indicador.rubric.condition_for(nivel)
            assert condicao, f"{indicador.id}: nível {nivel} sem condição"
            assert f"- {nivel}: {condicao}" in texto
