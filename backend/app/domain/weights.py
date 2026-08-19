"""
Pesos locais e globais dos indicadores (diretriz §5.1 e §7).

    Resposta de relevância → coeficiente → normalização dentro da dimensão →
    peso local → × peso da dimensão (AHP) → peso global

O ponto que este módulo existe para proteger é o tratamento do "não sei". A §5.1
é explícita em três proibições, e as três têm a mesma raiz — não transformar
ausência de informação em informação:

  - `null` **não** vira `0` silenciosamente;
  - `null` **não** é evidência de irrelevância;
  - dimensão sem coeficiente válido **não** cai em pesos iguais, pede revisão.

A última é a mais fácil de errar sem perceber: distribuir peso igual parece
inofensivo, mas fabrica um julgamento que o gestor não deu — e ainda por cima o
esconde, porque o resultado sai com aparência de normalidade.

Os três níveis de peso são persistidos separadamente (§7): nenhum sobrescreve o
outro, e o relatório pode reconstruir a conta inteira.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from domain.methodology import IndicatorConfig, Methodology

# Estados do coeficiente de relevância de um indicador.
RELEVANCE_ANSWERED = "answered"
RELEVANCE_UNKNOWN = "unknown"  # "não sei / não tenho informações suficientes"
RELEVANCE_MISSING = "missing"  # pergunta não respondida ou sem correspondência


@dataclass(frozen=True)
class IndicatorRelevance:
    """Coeficiente de relevância de um indicador, com a procedência da resposta."""

    indicator_id: str
    dimension: str
    question_id: Optional[str]
    coefficient: Optional[float]
    state: str
    raw_answer: Optional[str] = None

    @property
    def is_valid(self) -> bool:
        """Só coeficiente informado e positivo participa da normalização."""
        return self.state == RELEVANCE_ANSWERED and self.coefficient is not None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "indicator_id": self.indicator_id,
            "dimension": self.dimension,
            "question_id": self.question_id,
            "relevance_coefficient": self.coefficient,
            "state": self.state,
            "answer": self.raw_answer,
        }


@dataclass(frozen=True)
class IndicatorWeight:
    """Os três níveis de peso de um indicador (§7), lado a lado."""

    indicator_id: str
    dimension: str
    relevance_coefficient: Optional[float]
    relevance_state: str
    local_weight: Optional[float]
    dimension_weight: Optional[float]
    global_weight: Optional[float]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "indicator_id": self.indicator_id,
            "dimension": self.dimension,
            "relevance_coefficient": self.relevance_coefficient,
            "relevance_state": self.relevance_state,
            "local_weight": self.local_weight,
            "dimension_weight": self.dimension_weight,
            "global_weight": self.global_weight,
        }


@dataclass(frozen=True)
class WeightSet:
    """Resultado do cálculo de pesos, com o que precisa de revisão declarado."""

    weights: Tuple[IndicatorWeight, ...]
    relevances: Tuple[IndicatorRelevance, ...]
    # Dimensões sem nenhum coeficiente válido: a interface deve pedir revisão
    # em vez de o cálculo seguir com peso inventado (§5.1).
    dimensions_needing_review: Tuple[str, ...]

    def by_indicator(self) -> Dict[str, IndicatorWeight]:
        return {w.indicator_id: w for w in self.weights}

    def global_weights(self) -> Dict[str, float]:
        """Pesos globais dos indicadores que têm peso — os demais ficam de fora."""
        return {
            w.indicator_id: w.global_weight
            for w in self.weights
            if w.global_weight is not None
        }

    def local_weight_sum(self, dimension: str) -> float:
        return sum(
            w.local_weight or 0.0 for w in self.weights if w.dimension == dimension
        )

    def as_dicts(self) -> List[Dict[str, Any]]:
        return [w.as_dict() for w in self.weights]


# ---------------------------------------------------------------------------
# Coeficientes de relevância
# ---------------------------------------------------------------------------


def collect_relevance(
    answers_by_question: Mapping[str, Optional[str]],
    methodology: Methodology,
) -> Tuple[IndicatorRelevance, ...]:
    """
    Traduz as respostas dos blocos A/B/C em coeficientes por indicador.

    `answers_by_question` mapeia `question_id` → texto da alternativa escolhida.
    Uma pergunta ausente, uma alternativa desconhecida e a alternativa "não sei"
    produzem estados **distintos** de propósito: os três impedem o indicador de
    entrar na normalização, mas só o terceiro é uma resposta do gestor, e o
    relatório precisa poder dizer qual foi qual.
    """
    resultado: List[IndicatorRelevance] = []

    for indicator in methodology.indicators:
        raw = answers_by_question.get(indicator.question_id) if indicator.question_id else None
        coefficient, recognized = methodology.coefficient_for_label(raw)

        if not recognized:
            state = RELEVANCE_MISSING
            coefficient = None
        elif coefficient is None:
            # Alternativa reconhecida com coeficiente nulo = "não sei" (§5.1).
            state = RELEVANCE_UNKNOWN
        else:
            state = RELEVANCE_ANSWERED

        resultado.append(
            IndicatorRelevance(
                indicator_id=indicator.id,
                dimension=indicator.dimension,
                question_id=indicator.question_id,
                coefficient=coefficient,
                state=state,
                raw_answer=raw,
            )
        )

    return tuple(resultado)


# ---------------------------------------------------------------------------
# Pesos locais e globais
# ---------------------------------------------------------------------------


def compute_weights(
    relevances: Sequence[IndicatorRelevance],
    dimension_weights: Mapping[str, float],
    methodology: Methodology,
) -> WeightSet:
    """
    Pesos locais (§5.1) e globais (§7).

        l_j = v_j / Σ v_k     (dentro da dimensão)
        w_j = W_d × l_j

    Indicador sem coeficiente válido fica com peso local `None` — não zero. A
    diferença é metodológica: zero afirma "este indicador não importa", e é
    exatamente o que a diretriz proíbe deduzir de uma ausência de resposta.

    Dimensão inteira sem coeficiente válido entra em `dimensions_needing_review`
    e seus indicadores ficam sem peso. Nenhum fallback silencioso.
    """
    por_dimensao: Dict[str, List[IndicatorRelevance]] = {}
    for relevance in relevances:
        por_dimensao.setdefault(relevance.dimension, []).append(relevance)

    pesos: List[IndicatorWeight] = []
    revisar: List[str] = []

    for dimension in methodology.dimensions:
        do_grupo = por_dimensao.get(dimension, [])
        validos = [r for r in do_grupo if r.is_valid]
        total = sum(float(r.coefficient) for r in validos)
        peso_dimensao = dimension_weights.get(dimension)

        if not validos or total <= 0:
            revisar.append(dimension)

        for relevance in do_grupo:
            if relevance.is_valid and total > 0:
                local = float(relevance.coefficient) / total
                global_weight = (
                    peso_dimensao * local if peso_dimensao is not None else None
                )
            else:
                local = None
                global_weight = None

            pesos.append(
                IndicatorWeight(
                    indicator_id=relevance.indicator_id,
                    dimension=dimension,
                    relevance_coefficient=relevance.coefficient,
                    relevance_state=relevance.state,
                    local_weight=local,
                    dimension_weight=peso_dimensao,
                    global_weight=global_weight,
                )
            )

    return WeightSet(
        weights=tuple(pesos),
        relevances=tuple(relevances),
        dimensions_needing_review=tuple(revisar),
    )


def weights_from_answers(
    answers_by_question: Mapping[str, Optional[str]],
    dimension_weights: Mapping[str, float],
    methodology: Methodology,
) -> WeightSet:
    """Atalho: respostas → coeficientes → pesos locais e globais."""
    return compute_weights(
        collect_relevance(answers_by_question, methodology),
        dimension_weights,
        methodology,
    )


def indicators_for(weight_set: WeightSet, methodology: Methodology) -> Tuple[IndicatorConfig, ...]:
    """Indicadores que efetivamente receberam peso global."""
    com_peso = set(weight_set.global_weights())
    return tuple(i for i in methodology.indicators if i.id in com_peso)


__all__ = [
    "RELEVANCE_ANSWERED",
    "RELEVANCE_MISSING",
    "RELEVANCE_UNKNOWN",
    "IndicatorRelevance",
    "IndicatorWeight",
    "WeightSet",
    "collect_relevance",
    "compute_weights",
    "indicators_for",
    "weights_from_answers",
]
