"""
Montagem da memória de cálculo da agregação (§4.4.1.5 e §5.5).

Nada aqui decide: o cálculo já aconteceu em `domain/`. Este módulo só arruma os
mesmos números numa forma que o relatório consegue exibir e que uma pessoa
consegue refazer à mão — que é o que a §5.5 pede quando diz que o ranking "não
constitui apenas uma resposta final do sistema, mas um resultado cujo processo de
formação pode ser inspecionado".

A regra que orienta o formato: **toda linha exibida tem de trazer os fatores da
sua própria conta.** Uma célula que mostra só a contribuição obriga o leitor a
confiar; uma que mostra valor publicado → normalizado → peso efetivo →
contribuição pode ser conferida contra o PDF de origem.
"""

from typing import Any, Dict, List, Mapping, Sequence

from domain.methodology import Methodology
from domain.normalization import ComparabilitySet
from domain.scoring import ProviderScore, ScoringResult


def dimension_performance(score: ProviderScore) -> Dict[str, float]:
    """
    Desempenho do provedor em cada dimensão, na escala 0–1.

    É a média dos valores normalizados dos indicadores válidos da dimensão,
    ponderada pelos pesos efetivos **dentro** dela. Serve ao gráfico comparativo:
    diferente da contribuição, não carrega o peso da dimensão, então mede
    desempenho e não prioridade — dois provedores empatados em segurança têm o
    mesmo número aqui mesmo quando o gestor pesou segurança de forma diferente.

    Dimensão sem indicador válido fica **de fora** do mapa, em vez de aparecer
    como zero: nenhum desempenho foi medido ali.
    """
    somas: Dict[str, float] = {}
    pesos: Dict[str, float] = {}
    for contribution in score.contributions:
        somas[contribution.dimension] = somas.get(contribution.dimension, 0.0) + (
            contribution.effective_weight * contribution.normalized_value
        )
        pesos[contribution.dimension] = (
            pesos.get(contribution.dimension, 0.0) + contribution.effective_weight
        )
    return {
        dimension: somas[dimension] / peso
        for dimension, peso in pesos.items()
        if peso > 0
    }


def build_synthesis(
    scoring: ScoringResult,
    extraction: Any,
    comparability: ComparabilitySet,
    criteria_weights: Mapping[str, float],
    criteria_keys: Sequence[str],
    methodology: Methodology,
) -> Dict[str, Any]:
    """
    Memória de cálculo completa da Equação 5, provedor por provedor.

    `cells` é mantida chaveada por dimensão porque é o formato que a tabela
    `submission_rankings` grava desde a primeira versão — trocar a chave
    tornaria ilegível o histórico já persistido, sem ganho para o leitor.
    """
    findings_por_par = {
        (f.provider_id, f.indicator_id): f for f in getattr(extraction, "findings", ())
    }
    normalizados = comparability.normalized_by()
    nomes = {i.id: i.name for i in methodology.indicators}

    indicadores_meta: List[Dict[str, Any]] = []
    for indicator_id in comparability.valid:
        indicator = methodology.by_id(indicator_id)
        indicadores_meta.append(
            {
                "indicator_id": indicator.id,
                "name": indicator.name,
                "dimension": indicator.dimension,
                "data_type": indicator.data_type,
                "direction": indicator.direction,
                "effective_weight": round(
                    float(scoring.effective_weights.get(indicator.id, 0.0)), 6
                ),
            }
        )

    provedores: List[Dict[str, Any]] = []
    for score in scoring.scores:
        por_dimensao = {
            d: {
                "weight": round(float(criteria_weights.get(d, 0.0)), 6),
                "contribution": round(float(score.dimension_contributions.get(d, 0.0)), 6),
            }
            for d in criteria_keys
        }
        desempenho = dimension_performance(score)
        for dimension, valor in desempenho.items():
            if dimension in por_dimensao:
                por_dimensao[dimension]["performance"] = round(float(valor), 6)

        linhas: List[Dict[str, Any]] = []
        for indicator in methodology.indicators:
            finding = findings_por_par.get((score.provider_id, indicator.id))
            if finding is None:
                continue
            normalizado = normalizados.get((score.provider_id, indicator.id))
            peso = scoring.effective_weights.get(indicator.id)
            linhas.append(
                {
                    "indicator_id": indicator.id,
                    "name": nomes.get(indicator.id, indicator.id),
                    "dimension": indicator.dimension,
                    "data_type": indicator.data_type,
                    "direction": indicator.direction,
                    "status": finding.status,
                    "nature": finding.nature,
                    # "o valor ou característica extraída" (§5.4), como o
                    # documento a apresenta — ao lado do número que o cálculo usou.
                    "extracted_value": finding.extracted_value,
                    "original_value": finding.value,
                    "unit": finding.unit,
                    "category": finding.category,
                    # Condição do Quadro 23 que justifica o nível atribuído. É a
                    # regra pela qual a categoria foi escolhida — sem ela o
                    # relatório mostra um rótulo e pede confiança.
                    "category_condition": (
                        indicator.rubric.condition_for(finding.category)
                        if indicator.rubric and finding.category
                        else None
                    ),
                    "summary": finding.summary,
                    "rejection": finding.rejection,
                    "source_chunk_id": finding.source_chunk_id,
                    "source_document": finding.source_document,
                    "in_comparison": indicator.id in comparability.valid,
                    "excluded_reason": comparability.excluded.get(indicator.id),
                    "normalized_value": (
                        round(float(normalizado), 6) if normalizado is not None else None
                    ),
                    "effective_weight": round(float(peso), 6) if peso is not None else None,
                    "contribution": (
                        round(float(peso) * float(normalizado), 6)
                        if peso is not None and normalizado is not None
                        else None
                    ),
                }
            )

        provedores.append(
            {
                "id": score.provider_id,
                "name": score.provider_name,
                "rank": score.rank,
                "tied": score.tied,
                "score": round(float(score.score), 6),
                "cells": por_dimensao,
                "indicators": linhas,
            }
        )

    return {
        # Identifica o procedimento da §4.4.1.5. O valor anterior era
        # "distributive", de um método que não é o descrito na dissertação —
        # relatórios antigos continuam legíveis e declarando o que os produziu.
        "mode": "weighted_sum",
        "equation": "S_i = Σ_{j∈V} w'_j × r_ij",
        "criteria_order": list(criteria_keys),
        "dimension_weights": {k: round(float(v), 6) for k, v in criteria_weights.items()},
        "effective_weights": {
            k: round(float(v), 6) for k, v in scoring.effective_weights.items()
        },
        "valid_indicators": list(scoring.valid_indicators),
        "excluded_indicators": dict(scoring.excluded_indicators),
        "indicators": indicadores_meta,
        "providers": provedores,
        "tie_break_policy": scoring.tie_break_policy,
        "has_ties": scoring.has_ties,
        # Verificação de fechamento: os pesos efetivos somam 1 quando há conjunto
        # válido, e a pontuação de cada provedor fica entre 0 e 1.
        "effective_weight_sum": round(sum(scoring.effective_weights.values()), 6),
        "comparability_rate": comparability.comparability_rate,
    }


__all__ = ["build_synthesis", "dimension_performance"]
