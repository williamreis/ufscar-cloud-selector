"""
Pontuação global, ranking e explicação por contribuição (§12 e §13).

    S_i           = Σ_{j∈V} (w'_j × r_ij)
    contribution  = w'_j × r_ij
    por dimensão  = Σ das contribuições dos seus indicadores

Tudo aqui é determinístico e reconstruível à mão a partir do que é devolvido: a
§13 exige que a explicação numérica seja do código, e que a LLM apenas redija uma
síntese usando valores já prontos.

Sobre empate: a §12 proíbe desempate oculto. A política vem da configuração
(TODO ACADÊMICO 03) e o padrão `show_tie` dá a mesma posição a pontuações iguais,
deixando o empate visível no relatório em vez de resolvê-lo por ordem de lista —
que é o desempate acidental mais comum, e o mais difícil de perceber.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from domain.methodology import Methodology
from domain.normalization import ComparabilitySet


@dataclass(frozen=True)
class Contribution:
    """Contribuição de um indicador para a pontuação de um provedor."""

    indicator_id: str
    dimension: str
    effective_weight: float
    normalized_value: float
    contribution: float

    def as_dict(self) -> Dict[str, Any]:
        return {
            "indicator_id": self.indicator_id,
            "dimension": self.dimension,
            "effective_weight": round(self.effective_weight, 6),
            "normalized_value": round(self.normalized_value, 6),
            "contribution": round(self.contribution, 6),
        }


@dataclass(frozen=True)
class ProviderScore:
    """Pontuação de um provedor, com a memória de cálculo que a produziu."""

    provider_id: str
    provider_name: str
    score: float
    rank: int
    tied: bool
    contributions: Tuple[Contribution, ...]
    dimension_contributions: Mapping[str, float]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.provider_id,
            "name": self.provider_name,
            "rank": self.rank,
            "score": round(self.score, 6),
            "tied": self.tied,
            "dimension_contributions": {
                k: round(v, 6) for k, v in self.dimension_contributions.items()
            },
            "contributions": [c.as_dict() for c in self.contributions],
        }


@dataclass(frozen=True)
class ScoringResult:
    """Ranking completo, com o conjunto válido e os pesos efetivamente usados."""

    scores: Tuple[ProviderScore, ...]
    effective_weights: Mapping[str, float]
    valid_indicators: Tuple[str, ...]
    excluded_indicators: Mapping[str, str]
    tie_break_policy: str

    @property
    def has_ties(self) -> bool:
        return any(s.tied for s in self.scores)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ranking": [s.as_dict() for s in self.scores],
            "effective_weights": {k: round(v, 6) for k, v in self.effective_weights.items()},
            "valid_indicators": list(self.valid_indicators),
            "excluded_indicators": dict(self.excluded_indicators),
            "tie_break_policy": self.tie_break_policy,
            "has_ties": self.has_ties,
            # Deve fechar em 1 quando há conjunto válido: verificação do relatório.
            "effective_weight_sum": round(sum(self.effective_weights.values()), 6),
        }


def compute_scores(
    providers: Sequence[Mapping[str, str]],
    comparability: ComparabilitySet,
    effective_weights: Mapping[str, float],
    methodology: Methodology,
) -> ScoringResult:
    """
    Agrega pontuação e monta o ranking.

    `effective_weights` são os pesos **já renormalizados** sobre o conjunto
    válido (§11.2). Passá-los prontos é deliberado: quem decide o conjunto é a
    camada de comparabilidade, e reconstruí-lo aqui abriria espaço para as duas
    camadas discordarem.

    Provedor sem nenhum indicador válido pontua 0 — e isso não é "desempenho
    zero", é ausência de base comparável. O caso é registrado em
    `excluded_indicators` e cabe ao relatório dizê-lo; a alternativa (omitir o
    provedor) esconderia que ele foi avaliado.
    """
    normalizados = comparability.normalized_by()
    dimensao_por_indicador = {i.id: i.dimension for i in methodology.indicators}

    linhas: List[Tuple[float, str, str, List[Contribution], Dict[str, float]]] = []

    for provider in providers:
        provider_id = provider["id"]
        contribuicoes: List[Contribution] = []
        por_dimensao: Dict[str, float] = {d: 0.0 for d in methodology.dimensions}
        total = 0.0

        for indicator_id in comparability.valid:
            peso = effective_weights.get(indicator_id)
            valor = normalizados.get((provider_id, indicator_id))
            if peso is None or valor is None:
                continue

            contribuicao = peso * valor
            total += contribuicao
            dimensao = dimensao_por_indicador.get(indicator_id, "")
            por_dimensao[dimensao] = por_dimensao.get(dimensao, 0.0) + contribuicao
            contribuicoes.append(
                Contribution(
                    indicator_id=indicator_id,
                    dimension=dimensao,
                    effective_weight=peso,
                    normalized_value=valor,
                    contribution=contribuicao,
                )
            )

        linhas.append(
            (total, provider_id, provider.get("name", provider_id), contribuicoes, por_dimensao)
        )

    # Ordem decrescente de pontuação. O desempate por nome é apenas para tornar a
    # saída estável entre execuções — a posição em si é tratada logo abaixo.
    linhas.sort(key=lambda linha: (-linha[0], linha[2]))

    tolerancia = methodology.tie_break_tolerance
    scores: List[ProviderScore] = []
    posicao_anterior = 0
    valor_anterior: Optional[float] = None

    for indice, (total, provider_id, nome, contribuicoes, por_dimensao) in enumerate(linhas):
        empatado_com_anterior = (
            valor_anterior is not None and abs(total - valor_anterior) <= tolerancia
        )
        if empatado_com_anterior and methodology.tie_break_policy == "show_tie":
            posicao = posicao_anterior
        else:
            posicao = indice + 1

        scores.append(
            ProviderScore(
                provider_id=provider_id,
                provider_name=nome,
                score=total,
                rank=posicao,
                tied=empatado_com_anterior,
                contributions=tuple(contribuicoes),
                dimension_contributions=por_dimensao,
            )
        )
        posicao_anterior = posicao
        valor_anterior = total

    # `tied` marca também o primeiro elemento de um empate, não só o segundo:
    # um relatório que destaca "1º lugar" precisa saber que há outro ali.
    for i, atual in enumerate(scores[:-1]):
        seguinte = scores[i + 1]
        if seguinte.tied and not atual.tied:
            scores[i] = ProviderScore(
                provider_id=atual.provider_id,
                provider_name=atual.provider_name,
                score=atual.score,
                rank=atual.rank,
                tied=True,
                contributions=atual.contributions,
                dimension_contributions=atual.dimension_contributions,
            )

    return ScoringResult(
        scores=tuple(scores),
        effective_weights=dict(effective_weights),
        valid_indicators=comparability.valid,
        excluded_indicators=comparability.excluded,
        tie_break_policy=methodology.tie_break_policy,
    )


__all__ = ["Contribution", "ProviderScore", "ScoringResult", "compute_scores"]
