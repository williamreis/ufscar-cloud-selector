"""
Termos do Quadro 27 encontrados dentro de um trecho recuperado.

`queries.py` monta a consulta com os termos do indicador; este módulo faz o
caminho de volta — dado o trecho que a consulta trouxe, diz **quais daqueles
termos aparecem no texto**. É o mesmo Quadro 27, lido do outro lado da
recuperação.

Serve à §4.4: se a recuperação é "orientada pelos indicadores previamente
definidos", o relatório deve poder mostrar, trecho a trecho, que termo do
indicador o sustenta. Sem isso, o leitor vê a citação e o rótulo do indicador
sem nada que ligue um ao outro.

Duas cautelas que o texto dos PDFs impõe:

  - **acento e caixa não podem separar termos.** "Eficiência Energética" no
    relatório e "eficiência energética" no Quadro são o mesmo termo; a
    comparação é feita sobre uma forma dobrada (sem acento, em minúsculas) que
    preserva os índices do texto original.

  - **a quebra de linha do PDF fica no meio das expressões.** "Power Usage\n
    Effectiveness" precisa casar com "Power Usage Effectiveness", então o espaço
    do termo vira `\\s+` na busca.

O casamento exige fronteira não alfanumérica dos dois lados — "ms" não casa
dentro de "terms" —, e um termo cujas ocorrências estão todas dentro de outro
termo maior não é devolvido: "A-PUE" no texto anuncia "A-PUE", não "PUE".
"""

import re
import unicodedata
from typing import Dict, List, Sequence, Tuple


def _fold_char(ch: str) -> str:
    """
    Versão sem acento e em minúscula de um caractere, com o mesmo comprimento.

    O comprimento importa: os índices do texto dobrado são usados para recortar
    o texto original, então uma decomposição que virasse dois caracteres
    desalinharia tudo. Quando isso aconteceria, o caractere fica como está.
    """
    base = "".join(
        c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c)
    )
    dobrado = base.lower()
    return dobrado if len(dobrado) == 1 else ch.lower()[:1] or ch


def _fold(texto: str) -> str:
    return "".join(_fold_char(ch) for ch in texto)


def _pattern(termo: str) -> re.Pattern:
    """Regex do termo já dobrado, tolerante à quebra de linha entre palavras."""
    partes = [re.escape(p) for p in _fold(termo).split() if p]
    if not partes:
        return re.compile(r"(?!)")  # termo vazio não casa com nada
    return re.compile(r"(?<![0-9a-z])" + r"\s+".join(partes) + r"(?![0-9a-z])")


def terms_found(text: str, terms: Sequence[str]) -> List[str]:
    """
    Os termos que aparecem em `text`, na ordem em que aparecem.

    Devolve o termo **como o Quadro 27 o escreve**, não como o documento o
    escreveu: a tag no relatório nomeia o termo da pesquisa.
    """
    if not text or not terms:
        return []

    dobrado = _fold(text)

    ocorrencias: Dict[str, List[Tuple[int, int]]] = {}
    for termo in terms:
        limpo = (termo or "").strip()
        if not limpo or limpo in ocorrencias:
            continue
        spans = [m.span() for m in _pattern(limpo).finditer(dobrado)]
        if spans:
            ocorrencias[limpo] = spans

    encontrados = []
    for termo, spans in ocorrencias.items():
        if all(_contido_em_outro(span, termo, ocorrencias) for span in spans):
            continue
        encontrados.append((min(inicio for inicio, _ in spans), termo))

    return [termo for _, termo in sorted(encontrados)]


def _contido_em_outro(
    span: Tuple[int, int],
    termo: str,
    ocorrencias: Dict[str, List[Tuple[int, int]]],
) -> bool:
    """`span` cai inteiro dentro da ocorrência de um termo mais longo?"""
    inicio, fim = span
    for outro, outros_spans in ocorrencias.items():
        if outro == termo:
            continue
        for o_inicio, o_fim in outros_spans:
            maior = (o_fim - o_inicio) > (fim - inicio)
            if maior and o_inicio <= inicio and fim <= o_fim:
                return True
    return False


__all__ = ["terms_found"]
