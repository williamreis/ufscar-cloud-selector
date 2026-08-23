"""
Consultas de recuperação, montadas **por indicador** (§4.4 e §16).

A §4.4 é explícita sobre o que a recuperação não pode ser: "o processo de
recuperação não ocorre de forma aberta ou desvinculada dos critérios da pesquisa.
As consultas são orientadas pelos indicadores previamente definidos nas dimensões
de Sustentabilidade, Segurança e Desempenho Operacional."

Por isso não existe mais consulta por dimensão aqui. A consulta-base de cada
indicador vem da configuração declarativa — do campo `search_terms` de
`methodology/indicators.json`, que é o Quadro 27 em forma de dado. O código não
guarda lista de termos: se um termo precisa mudar, muda no JSON e entra no hash
de versão da avaliação.

Três decisões que valem registrar:

  - **o nome do provedor entra na consulta**, mas o filtro por provedor no
    índice continua sendo o que garante o isolamento. O nome ajuda o ranqueamento
    dentro do conjunto já filtrado; sozinho ele não separaria nada, porque a
    similaridade responde aos termos temáticos.

  - **o nome do indicador entra antes dos termos.** Os `search_terms` são siglas
    e sinônimos (PUE, DCiE, A-PUE...), e uma consulta composta só de siglas
    perde para uma que também diz, em linguagem natural, o que se procura.

  - **os termos do gestor entram por último e são poucos.** A §4.5.1 dá ao Bloco
    E a função de "refinamento das consultas", não de substituição: os termos da
    pesquisa vêm primeiro e sempre, e o refinamento acrescenta. A ordem importa
    porque o vetor da consulta é a média do que está nela — deixar o texto do
    gestor dominar transformaria "refinar" em "trocar o critério".
"""

from typing import Any, Optional, Sequence

# Quantos termos do `search_terms` entram na consulta. A lista completa de um
# indicador chega a 15 termos; concatenar todos dilui o vetor da consulta, que
# passa a apontar para o "assunto geral" em vez do indicador.
MAX_SEARCH_TERMS = 8

# Teto dos termos vindos do Bloco E. Baixo por construção: refinamento é ajuste
# de foco, não redefinição do que se procura.
MAX_EXTRA_TERMS = 4


def query_for_indicator(
    indicator: Any,
    provider_name: Optional[str] = None,
    extra_terms: Sequence[str] = (),
) -> str:
    """
    Consulta-base de um indicador, opcionalmente ancorada no nome do provedor e
    refinada pelos requisitos institucionais do gestor (§4.5.1).

    Aceita qualquer objeto com `name` e `search_terms` — na prática um
    `IndicatorConfig`, mas a assinatura evita que este módulo importe o domínio
    só para uma anotação de tipo.
    """
    termos: Sequence[str] = tuple(getattr(indicator, "search_terms", ()) or ())
    partes = [str(getattr(indicator, "name", "") or "").strip()]
    partes.extend(t.strip() for t in termos[:MAX_SEARCH_TERMS] if t and t.strip())
    partes.extend(t.strip() for t in extra_terms[:MAX_EXTRA_TERMS] if t and t.strip())
    consulta = ", ".join(p for p in partes if p)
    if provider_name:
        return f"{provider_name}: {consulta}"
    return consulta


__all__ = ["MAX_EXTRA_TERMS", "MAX_SEARCH_TERMS", "query_for_indicator"]
