"""
Domínio determinístico da decisão multicritério (diretriz §5 a §13).

Este pacote é a "fonte de verdade do ranking" da §2.2. Nada aqui chama LLM, RAG,
banco ou rede: entram respostas do questionário e desempenhos já extraídos, saem
pesos, valores normalizados, pontuações e contribuições — sempre pelo mesmo
caminho e sempre com o mesmo resultado.

    methodology    configuração declarativa (indicadores, escalas, rubricas)
    weights        coeficiente → peso local → peso global
    normalization  evidência → valor comparável → conjunto V → renormalização
    scoring        pontuação, ranking e contribuição por indicador/dimensão
    sensitivity    quanto o peso de uma dimensão precisa mudar para trocar o líder

O motor AHP das dimensões continua em `ahp.py`, que já existia e é chamado daqui.
"""

from domain.methodology import (
    IndicatorConfig,
    Methodology,
    MethodologyConfigError,
    Rubric,
    get_methodology,
    load_methodology,
    reload_methodology,
)
from domain.normalization import (
    STATUS_FOUND,
    STATUS_INVALID,
    STATUS_NOT_FOUND,
    STATUS_PARTIAL,
    ComparabilitySet,
    NormalizedPerformance,
    PerformanceInput,
    build_comparability_set,
    normalize_values,
    renormalize_weights,
    value_from_category,
)
from domain.scoring import Contribution, ProviderScore, ScoringResult, compute_scores
from domain.sensitivity import DimensionSensitivity, SensitivityResult
from domain.sensitivity import analyze as analyze_sensitivity
from domain.weights import (
    RELEVANCE_ANSWERED,
    RELEVANCE_MISSING,
    RELEVANCE_UNKNOWN,
    IndicatorRelevance,
    IndicatorWeight,
    WeightSet,
    collect_relevance,
    compute_weights,
    indicators_for,
    weights_from_answers,
)

__all__ = [
    "RELEVANCE_ANSWERED",
    "RELEVANCE_MISSING",
    "RELEVANCE_UNKNOWN",
    "STATUS_FOUND",
    "STATUS_INVALID",
    "STATUS_NOT_FOUND",
    "STATUS_PARTIAL",
    "ComparabilitySet",
    "Contribution",
    "IndicatorConfig",
    "IndicatorRelevance",
    "IndicatorWeight",
    "Methodology",
    "MethodologyConfigError",
    "NormalizedPerformance",
    "PerformanceInput",
    "ProviderScore",
    "Rubric",
    "ScoringResult",
    "WeightSet",
    "build_comparability_set",
    "collect_relevance",
    "compute_scores",
    "DimensionSensitivity",
    "SensitivityResult",
    "analyze_sensitivity",
    "compute_weights",
    "get_methodology",
    "indicators_for",
    "load_methodology",
    "normalize_values",
    "reload_methodology",
    "renormalize_weights",
    "value_from_category",
    "weights_from_answers",
]
