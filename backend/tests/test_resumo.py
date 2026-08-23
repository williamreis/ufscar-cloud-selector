"""
O RESUMO.md descreve o produto que existe.

O arquivo é marcado "Documento para incorporação em dissertação acadêmica", e foi
justamente isso que tornou o problema grave: ele descrevia Streamlit, Chroma e uma
LLM que calculava os pesos — três afirmações falsas, numa fonte destinada a virar
texto de defesa. Ninguém percebeu porque nada as conferia.

Estes testes conferem. Não avaliam redação: verificam que o documento não afirma
tecnologia que saiu, que as tecnologias citadas estão declaradas, e que a
descrição do questionário e dos prompts bate com a configuração em vigor.

Um teste que lê prosa é frágil por natureza, então o que se checa aqui é o
mínimo que pega a regressão real — arquitetura anterior descrita como se fosse a
atual — e não o estilo do texto.
"""

import json
import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
RESUMO = RAIZ / "RESUMO.md"


# Título da seção que fala das divergências com o texto da dissertação. Ela cita
# de propósito o que saiu do produto, então fica fora da busca por termos banidos.
SECAO_DIVERGENCIAS = "### Divergências conhecidas"


@pytest.fixture(scope="module")
def texto():
    return RESUMO.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def minusculo(texto):
    """
    Texto em minúsculas e com espaços colapsados.

    A quebra de linha do Markdown cai no meio das frases, e uma busca literal por
    "não atribui pontuação" falharia por causa de um `\n` — um falso negativo que
    ensinaria a ignorar o teste.
    """
    return " ".join(texto.lower().split())


@pytest.fixture(scope="module")
def descricao(minusculo):
    """Só a descrição do produto, sem a seção de divergências com a dissertação."""
    return minusculo.split(SECAO_DIVERGENCIAS.lower(), 1)[0]


# --- Tecnologias que saíram do produto -------------------------------------

# (termo, o que o substituiu) — cada um destes já esteve no documento.
SUBSTITUIDAS = [
    ("streamlit", "o frontend é React + TypeScript"),
    ("chroma", "o banco vetorial é FAISS"),
    ("pandas", "a agregação é NumPy puro desde a Equação 5"),
]


@pytest.mark.parametrize("termo,substituto", SUBSTITUIDAS, ids=[t for t, _ in SUBSTITUIDAS])
def test_nao_cita_tecnologia_que_saiu(descricao, termo, substituto):
    assert termo not in descricao, f"RESUMO.md cita {termo!r}, mas {substituto}."


def test_nao_atribui_o_calculo_dos_pesos_a_llm(descricao):
    """
    A afirmação mais grave da versão anterior: "o LLM retorna pesos normalizados
    para os três critérios". Contradiz o código e a tese da §5.4.
    """
    for frase in ("llm retorna pesos", "llm calcula os pesos", "pesos definidos pelo llm"):
        assert frase not in descricao


def test_afirma_que_a_llm_nao_pontua(minusculo):
    """A separação entre probabilístico e determinístico precisa estar dita."""
    assert "não atribui pontuação" in minusculo


# --- Tecnologias citadas existem de fato -----------------------------------


def _requirements() -> str:
    return (RAIZ / "requirements.txt").read_text(encoding="utf-8").lower()


def _package_json() -> dict:
    d = json.loads((RAIZ / "frontend" / "package.json").read_text(encoding="utf-8"))
    return {**d.get("dependencies", {}), **d.get("devDependencies", {})}


BACKEND_CITADAS = ["fastapi", "uvicorn", "pydantic", "langchain", "faiss", "numpy",
                   "sqlalchemy", "pytest", "sentence-transformers"]

FRONTEND_CITADAS = {
    "react": "react",
    "typescript": "typescript",
    "vite": "vite",
    "tailwind": "tailwindcss",
    "react router": "react-router-dom",
    "recharts": "recharts",
}


@pytest.mark.parametrize("pacote", BACKEND_CITADAS)
def test_dependencia_de_backend_citada_esta_declarada(minusculo, pacote):
    # O documento escreve "Sentence Transformers" e o requirements,
    # "sentence-transformers": comparar sem o hífen cobre as duas grafias.
    def sem_hifen(t: str) -> str:
        return t.replace("-", " ")

    assert sem_hifen(pacote) in sem_hifen(minusculo), (
        f"RESUMO.md deixou de citar {pacote!r}."
    )
    assert pacote in _requirements(), (
        f"RESUMO.md cita {pacote!r}, que não está em requirements.txt."
    )


@pytest.mark.parametrize("citado,pacote", FRONTEND_CITADAS.items())
def test_dependencia_de_frontend_citada_esta_declarada(minusculo, citado, pacote):
    assert citado in minusculo, f"RESUMO.md deixou de citar {citado!r}."
    assert pacote in _package_json(), (
        f"RESUMO.md cita {citado!r}, que não está em frontend/package.json."
    )


def test_sqlite_e_a_persistencia_descrita(minusculo):
    assert "sqlite" in minusculo and "postgresql" in minusculo


# --- Questionário: o documento descreve o questionário em vigor ------------


@pytest.fixture(scope="module")
def questionario():
    caminho = RAIZ / "frontend" / "public" / "questions.json"
    return json.loads(caminho.read_text(encoding="utf-8"))


def test_total_de_perguntas_confere(minusculo, questionario):
    total = sum(len(s["questions"]) for s in questionario["sections"])
    assert total == 25
    assert "25 perguntas" in minusculo


def test_niveis_de_relevancia_conferem(minusculo, questionario):
    niveis = questionario["option_sets"]["relevance"]
    assert len(niveis) == 5
    assert "relevância (5 níveis)" in minusculo


def test_bloco_d_tem_as_tres_comparacoes(minusculo, questionario):
    comparacoes = [
        q
        for s in questionario["sections"]
        for q in s["questions"]
        if q["type"] == "pairwise"
    ]
    assert len(comparacoes) == 3
    assert "17–19" in minusculo


def test_intensidades_verbais_conferem(minusculo):
    import pairwise

    for rotulo in pairwise.INTENSITY_ADVERBS.values():
        assert rotulo in minusculo, f"RESUMO.md não cita a intensidade {rotulo!r}."


def test_bloco_e_declarado_sem_efeito_sobre_pesos(minusculo):
    """§4.5.1 — o limite do Bloco E é o que mais se presta a mal-entendido."""
    assert "o bloco e não altera peso" in minusculo


# --- Prompts: o documento lista os que existem -----------------------------


def test_numero_de_prompts_confere(texto):
    from llm.prompts import registered_versions

    registrados = registered_versions()
    # A tabela de prompts tem uma linha por prompt registrado.
    tabela = texto.split("| Prompt | Função |", 1)[1].split("\n\n", 1)[0]
    linhas = [l for l in tabela.splitlines() if l.startswith("|") and "---" not in l]
    assert len(linhas) == len(registrados), (
        f"{len(registrados)} prompts registrados, {len(linhas)} descritos no RESUMO.md."
    )


def test_prompt_principal_e_o_de_extracao(minusculo):
    assert "extração de evidências" in minusculo
    assert "prompt principal" in minusculo


# --- Auditoria: o documento descreve o que é gravado -----------------------


def test_registro_cita_o_que_e_persistido(minusculo):
    for item in ("guardrail", "consulta", "prompt", "indicador"):
        assert item in minusculo


def test_hash_do_questionario_e_mencionado(minusculo):
    assert "hash" in minusculo


# --- Limitações declaradas --------------------------------------------------


def test_ausencia_de_analise_de_sensibilidade_esta_declarada(minusculo):
    """§5.5 declara a ausência; o resumo não pode omiti-la."""
    assert "não contempla análise de sensibilidade" in minusculo


def test_divergencias_com_a_dissertacao_estao_registradas(texto):
    """
    O documento existe para ser incorporado ao texto. Onde produto e texto ainda
    divergem, quem lê precisa saber — senão a incorporação propaga o erro.
    """
    assert "Divergências conhecidas" in texto
    for secao in ("5.3", "4.2.3", "4.4.1"):
        assert secao in texto


# --- Coerência com os números do produto -----------------------------------


def test_treze_indicadores_e_tres_dimensoes(minusculo):
    from domain.methodology import get_methodology

    m = get_methodology()
    assert len(m.indicators) == 13
    assert "treze indicadores" in minusculo
    assert len(m.dimensions) == 3


def test_limiar_de_consistencia_confere(texto):
    from domain.methodology import get_methodology

    limiar = get_methodology().ahp_consistency_threshold
    assert limiar == 0.10
    assert "0,10" in texto


def test_equacao_da_agregacao_e_a_soma_ponderada(minusculo):
    assert "soma ponderada" in minusculo
    assert re.search(r"equação\s*5", minusculo)
