"""
Consultas de recuperação, montadas **por indicador** (diretriz §16).

A consulta-base de cada indicador vem da configuração declarativa — do campo
`search_terms` de `methodology/indicators.json`, que é o Quadro 27 da
dissertação em forma de dado. O código não guarda lista de termos: se um termo
precisa mudar, muda no JSON e entra no hash de versão da avaliação.

Duas decisões que valem registrar:

  - **o nome do provedor entra na consulta**, mas o filtro por provedor no
    índice continua sendo o que garante o isolamento. O nome ajuda o ranqueamento
    dentro do conjunto já filtrado; sozinho ele não separaria nada, porque a
    similaridade responde aos termos temáticos.

  - **o nome do indicador entra antes dos termos.** Os `search_terms` são siglas
    e sinônimos (PUE, DCiE, A-PUE...), e uma consulta composta só de siglas
    perde para uma que também diz, em linguagem natural, o que se procura.

`DIMENSION_QUERIES` continua aqui para a busca livre da área de gestão, que não
avalia indicador nenhum — não é mais o caminho da recomendação.
"""

from typing import Any, Dict, Optional, Sequence

# Quantos termos do `search_terms` entram na consulta. A lista completa de um
# indicador chega a 15 termos; concatenar todos dilui o vetor da consulta, que
# passa a apontar para o "assunto geral" em vez do indicador.
MAX_SEARCH_TERMS = 8

DIMENSION_QUERIES: Dict[str, str] = {
    "sustainability": (
        "eficiência energética do data center, energia renovável, emissões de carbono, "
        "PUE, water usage, metas de sustentabilidade"
    ),
    "performance": (
        "disponibilidade uptime SLA, latência, desempenho, escalabilidade, "
        "capacidade de computação, confiabilidade da infraestrutura"
    ),
    "security": (
        "segurança da informação, certificações ISO 27001 SOC 2 GDPR, criptografia, "
        "backup e recuperação de desastres, conformidade e auditoria"
    ),
}


def query_for(dimension: str) -> str:
    """Consulta genérica de uma dimensão. Usada fora do fluxo de avaliação."""
    return DIMENSION_QUERIES.get(dimension, dimension)


def query_for_indicator(indicator: Any, provider_name: Optional[str] = None) -> str:
    """
    Consulta-base de um indicador, opcionalmente ancorada no nome do provedor.

    Aceita qualquer objeto com `name` e `search_terms` — na prática um
    `IndicatorConfig`, mas a assinatura evita que este módulo importe o domínio
    só para uma anotação de tipo.
    """
    termos: Sequence[str] = tuple(getattr(indicator, "search_terms", ()) or ())
    partes = [str(getattr(indicator, "name", "") or "").strip()]
    partes.extend(t.strip() for t in termos[:MAX_SEARCH_TERMS] if t and t.strip())
    consulta = ", ".join(p for p in partes if p)
    if provider_name:
        return f"{provider_name}: {consulta}"
    return consulta


__all__ = ["DIMENSION_QUERIES", "MAX_SEARCH_TERMS", "query_for", "query_for_indicator"]
