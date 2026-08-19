"""
Desempenho dos provedores: da evidência ao valor comparável (§9, §10 e §11).

Três regras da diretriz moram aqui, e todas as três são sobre **não inventar**:

  - **§9.3 — divisão indefinida não vira número.** Quando a fórmula fica
    matematicamente indefinida (todo mundo com o mesmo valor a zero, máximo
    zero), o indicador é marcado `non_discriminative` ou `invalid_for_comparison`
    e sai da comparação. Não há valor de recurso.

  - **§11 — ausência de evidência não é desempenho zero.** `NOT_FOUND` retira o
    indicador do conjunto comparável; não zera a nota de ninguém. Zero afirmaria
    "o provedor não tem", que é justamente o que não se sabe.

  - **§11.1 — o conjunto comparável é comum a todos.** Um indicador só entra se
    houver valor comparável para **todas** as alternativas da avaliação.
    Renormalizar por provedor faria cada um ser avaliado numa régua diferente, e
    a §11.1 proíbe isso em letra maiúscula.

A conversão de evidência qualitativa em número é da rubrica (§10.1) — regra
determinística sobre uma categoria que a LLM apenas classificou dentro de uma
allowlist. A LLM não atribui nota em nenhum ponto deste arquivo.
"""

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from domain.methodology import (
    DIRECTION_BENEFIT,
    DIRECTION_MINIMIZE,
    IndicatorConfig,
    Methodology,
)

# Estados de evidência (§11).
STATUS_FOUND = "FOUND"
STATUS_PARTIAL = "PARTIAL"
STATUS_NOT_FOUND = "NOT_FOUND"
STATUS_INVALID = "INVALID"

# Motivos pelos quais um indicador fica fora do conjunto comparável.
EXCLUDED_NO_EVIDENCE = "no_evidence"
EXCLUDED_NON_DISCRIMINATIVE = "non_discriminative"
EXCLUDED_INVALID_FOR_COMPARISON = "invalid_for_comparison"
EXCLUDED_MISSING_FOR_SOME = "missing_for_some_providers"
EXCLUDED_NO_WEIGHT = "no_weight"


@dataclass(frozen=True)
class PerformanceInput:
    """
    Desempenho bruto de um provedor num indicador, antes da normalização.

    `value` é o valor extraído (quantitativo) ou já convertido pela rubrica
    (qualitativo). `None` com status `NOT_FOUND` é o caso normal de ausência —
    e continua sendo `None`, nunca `0.0`.
    """

    provider_id: str
    indicator_id: str
    status: str
    value: Optional[float] = None
    raw_value: Optional[str] = None
    unit: Optional[str] = None
    qualitative_category: Optional[str] = None

    @property
    def is_usable(self) -> bool:
        return self.status == STATUS_FOUND and self.value is not None


@dataclass(frozen=True)
class NormalizedPerformance:
    """Valor adimensional de um provedor num indicador."""

    provider_id: str
    indicator_id: str
    original_value: Optional[float]
    normalized_value: Optional[float]
    status: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "indicator_id": self.indicator_id,
            "original_value": self.original_value,
            "normalized_value": self.normalized_value,
            "status": self.status,
        }


@dataclass(frozen=True)
class ComparabilitySet:
    """
    Conjunto `V` de indicadores válidos e comparáveis (§11.1), mais o que ficou
    de fora e por quê — a exclusão é informação de relatório, não um detalhe.
    """

    valid: Tuple[str, ...]
    excluded: Mapping[str, str]
    normalized: Tuple[NormalizedPerformance, ...]

    @property
    def comparability_rate(self) -> Optional[float]:
        total = len(self.valid) + len(self.excluded)
        return (len(self.valid) / total) if total else None

    def normalized_by(self) -> Dict[Tuple[str, str], float]:
        return {
            (n.provider_id, n.indicator_id): n.normalized_value
            for n in self.normalized
            if n.normalized_value is not None
        }

    def as_dict(self) -> Dict[str, Any]:
        return {
            "valid_indicators": list(self.valid),
            "excluded_indicators": dict(self.excluded),
            "comparability_rate": self.comparability_rate,
            "normalized": [n.as_dict() for n in self.normalized],
        }


# ---------------------------------------------------------------------------
# Rubrica: categoria qualitativa → valor
# ---------------------------------------------------------------------------


def value_from_category(
    indicator: IndicatorConfig,
    category: Optional[str],
) -> Tuple[Optional[float], str]:
    """
    Converte uma categoria qualitativa em número pela rubrica do indicador (§10.1).

    Categoria fora da allowlist devolve `INVALID`, não uma nota aproximada: a §19
    exige que a categoria pertença à lista permitida, e "quase" não é um valor.
    """
    if indicator.rubric is None:
        return None, STATUS_INVALID
    if category is None:
        return None, STATUS_NOT_FOUND
    value = indicator.rubric.value_for(category)
    if value is None:
        return None, STATUS_INVALID
    return value, STATUS_FOUND


# ---------------------------------------------------------------------------
# Normalização quantitativa (§9)
# ---------------------------------------------------------------------------


def normalize_values(
    values: Mapping[str, float],
    direction: str,
) -> Tuple[Dict[str, Optional[float]], Optional[str]]:
    """
    Normaliza os valores de **um** indicador entre os provedores.

        benefício    r_ij = x_ij / max_i(x_ij)
        minimização  r_ij = min_i(x_ij) / x_ij

    Devolve `(normalizados, motivo_de_exclusão)`. Quando a fórmula fica
    indefinida, os normalizados voltam vazios e o motivo diz qual caso ocorreu —
    em nenhuma hipótese há divisão por zero nem valor substituto.
    """
    if not values:
        return {}, EXCLUDED_NO_EVIDENCE

    numbers = list(values.values())

    if direction == DIRECTION_BENEFIT:
        maximum = max(numbers)
        if maximum == 0:
            # Todos zerados: a razão x/max é 0/0. Não há como discriminar.
            return {}, EXCLUDED_NON_DISCRIMINATIVE
        if maximum < 0:
            return {}, EXCLUDED_INVALID_FOR_COMPARISON
        return {pid: value / maximum for pid, value in values.items()}, None

    if direction == DIRECTION_MINIMIZE:
        if any(value <= 0 for value in numbers):
            # min/x com algum x ≤ 0 é indefinido ou muda de sinal: fora.
            return {}, EXCLUDED_INVALID_FOR_COMPARISON
        minimum = min(numbers)
        return {pid: minimum / value for pid, value in values.items()}, None

    return {}, EXCLUDED_INVALID_FOR_COMPARISON


# ---------------------------------------------------------------------------
# Conjunto comparável (§11.1) e renormalização (§11.2)
# ---------------------------------------------------------------------------


def build_comparability_set(
    performances: Sequence[PerformanceInput],
    provider_ids: Sequence[str],
    weighted_indicator_ids: Iterable[str],
    methodology: Methodology,
) -> ComparabilitySet:
    """
    Monta o conjunto `V`: indicadores com valor utilizável para **todos** os
    provedores da avaliação, já normalizados.

    Indicador com peso mas sem evidência suficiente sai — e o motivo fica
    registrado. Indicador com evidência mas sem peso também sai: peso nulo
    significa que o gestor não deu base para ele, e usá-lo assim mesmo seria
    reintroduzir o julgamento que a §5.1 recusa fabricar.
    """
    com_peso = set(weighted_indicator_ids)
    provedores = list(provider_ids)

    por_indicador: Dict[str, Dict[str, PerformanceInput]] = {}
    for entry in performances:
        por_indicador.setdefault(entry.indicator_id, {})[entry.provider_id] = entry

    valid: List[str] = []
    excluded: Dict[str, str] = {}
    normalized: List[NormalizedPerformance] = []

    for indicator in methodology.indicators:
        entradas = por_indicador.get(indicator.id, {})

        if indicator.id not in com_peso:
            if entradas:
                excluded[indicator.id] = EXCLUDED_NO_WEIGHT
            continue

        # O status de cada provedor é preservado mesmo quando o indicador é
        # descartado: a cobertura documental (§29) conta FOUND/PARTIAL/NOT_FOUND
        # independentemente de o indicador entrar no ranking.
        usaveis = {
            pid: float(entrada.value)
            for pid, entrada in entradas.items()
            if entrada.is_usable
        }

        faltando = [pid for pid in provedores if pid not in usaveis]
        if faltando:
            motivo = (
                EXCLUDED_NO_EVIDENCE if not usaveis else EXCLUDED_MISSING_FOR_SOME
            )
            excluded[indicator.id] = motivo
            for pid in provedores:
                entrada = entradas.get(pid)
                normalized.append(
                    NormalizedPerformance(
                        provider_id=pid,
                        indicator_id=indicator.id,
                        original_value=entrada.value if entrada else None,
                        normalized_value=None,
                        status=entrada.status if entrada else STATUS_NOT_FOUND,
                    )
                )
            continue

        valores_normalizados, motivo = normalize_values(
            {pid: usaveis[pid] for pid in provedores}, indicator.direction or DIRECTION_BENEFIT
        )
        if motivo is not None:
            excluded[indicator.id] = motivo
            for pid in provedores:
                normalized.append(
                    NormalizedPerformance(
                        provider_id=pid,
                        indicator_id=indicator.id,
                        original_value=usaveis.get(pid),
                        normalized_value=None,
                        status=STATUS_INVALID,
                    )
                )
            continue

        valid.append(indicator.id)
        for pid in provedores:
            normalized.append(
                NormalizedPerformance(
                    provider_id=pid,
                    indicator_id=indicator.id,
                    original_value=usaveis[pid],
                    normalized_value=valores_normalizados[pid],
                    status=STATUS_FOUND,
                )
            )

    return ComparabilitySet(
        valid=tuple(valid), excluded=dict(excluded), normalized=tuple(normalized)
    )


def renormalize_weights(
    global_weights: Mapping[str, float],
    valid_indicator_ids: Sequence[str],
) -> Dict[str, float]:
    """
    Renormaliza os pesos dos indicadores válidos (§11.2):

        w'_j = w_j / Σ_{k∈V}(w_k)        com Σ_{j∈V} w'_j = 1

    O mesmo conjunto `V` vale para todos os provedores — por isso a função recebe
    uma lista de indicadores, e não um mapa por provedor.
    """
    selecionados = {
        indicator_id: float(global_weights[indicator_id])
        for indicator_id in valid_indicator_ids
        if indicator_id in global_weights
    }
    total = sum(selecionados.values())
    if total <= 0:
        return {}
    return {indicator_id: peso / total for indicator_id, peso in selecionados.items()}


__all__ = [
    "EXCLUDED_INVALID_FOR_COMPARISON",
    "EXCLUDED_MISSING_FOR_SOME",
    "EXCLUDED_NON_DISCRIMINATIVE",
    "EXCLUDED_NO_EVIDENCE",
    "EXCLUDED_NO_WEIGHT",
    "STATUS_FOUND",
    "STATUS_INVALID",
    "STATUS_NOT_FOUND",
    "STATUS_PARTIAL",
    "ComparabilitySet",
    "NormalizedPerformance",
    "PerformanceInput",
    "build_comparability_set",
    "normalize_values",
    "renormalize_weights",
    "value_from_category",
]
