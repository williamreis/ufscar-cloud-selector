"""
Consultas de recuperação por dimensão.

**Provisório por natureza.** A §16 determina que a consulta-base seja definida
*por indicador*, em configuração declarativa com `search_terms`, e não por
dimensão. Enquanto a camada de indicadores não existir (Fase 1), estes termos
mantêm o comportamento atual: cada evidência recuperada fica ligada a uma
dimensão específica em vez de a uma busca genérica.

Quando o `IndicatorConfig` entrar, este módulo é substituído pela leitura da
configuração dos indicadores — não estendido.
"""

from typing import Dict

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
    return DIMENSION_QUERIES.get(dimension, dimension)


__all__ = ["DIMENSION_QUERIES", "query_for"]
