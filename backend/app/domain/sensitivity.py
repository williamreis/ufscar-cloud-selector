"""
Análise de sensibilidade do ranking aos pesos das dimensões.

**O que responde.** Quanto o peso de uma dimensão precisaria mudar, mantidas as
respostas do gestor em tudo o mais, para que o primeiro colocado deixasse de ser
o primeiro. É a pergunta que separa "a recomendação decorre das prioridades
declaradas" de "a recomendação sobreviveria a qualquer prioridade".

**Por que faz falta.** Com a rubrica do Quadro 23 concentrada num nível só, os
indicadores qualitativos contribuem o mesmo para todos os provedores e se anulam.
Sobram poucos quantitativos para separar, e as pontuações saem a 0,002 umas das
outras. Um relatório que apresenta essa ordem sem dizer o quanto ela é frágil
transfere ao gestor uma confiança que o cálculo não tem.

**O que NÃO faz.** Não recomenda peso, não sugere revisão de julgamento e não
altera o ranking. Devolve uma medida sobre o resultado que já existe — a decisão
continua inteira com quem respondeu o questionário.

**Método.** Para cada dimensão `d`, varia-se o peso `w_d` dentro de [0,1]. Os
pesos das outras dimensões são reescalados proporcionalmente, de modo que a soma
continue 1 — é a perturbação padrão em AHP, e a única que preserva a relação
entre as dimensões que não estão sendo testadas. A cada peso novo os pesos
globais dos indicadores são recalculados, renormalizados sobre o mesmo conjunto
comparável, e as pontuações refeitas. Procura-se o menor deslocamento, para cima
ou para baixo, que troque o primeiro colocado.

A varredura é determinística e a passagem é fina (`PASSO`), em vez de busca
binária: a função objetivo não é monotônica — trocar a liderança pode acontecer,
desfazer-se e acontecer de novo —, e uma binária assumiria monotonicidade que não
existe.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

# Resolução da varredura, em pontos de peso. 0,005 = meio ponto percentual:
# fino o bastante para distinguir "vira com quase nada" de "não vira", e grosso
# o bastante para a varredura das três dimensões custar milissegundos.
PASSO = 0.005

# Abaixo disto o deslocamento é indistinguível de zero e a leitura correta é
# "qualquer mudança vira", não "vira com 0,001".
DESLOCAMENTO_MINIMO = 1e-9


@dataclass(frozen=True)
class DimensionSensitivity:
    """Quanto o peso de uma dimensão precisa mudar para trocar o líder."""

    dimension: str
    weight: float
    #: Menor deslocamento (em pontos de peso) que troca o primeiro colocado.
    #: `None` quando nenhum peso possível em [0,1] troca.
    flip_delta: Optional[float]
    #: Peso da dimensão no ponto de virada.
    flip_weight: Optional[float]
    #: Quem passa a liderar naquele ponto.
    flip_leader: Optional[str]

    @property
    def flips(self) -> bool:
        return self.flip_delta is not None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "dimension": self.dimension,
            "weight": round(self.weight, 6),
            "flips": self.flips,
            "flip_delta": None if self.flip_delta is None else round(self.flip_delta, 4),
            "flip_weight": None if self.flip_weight is None else round(self.flip_weight, 4),
            "flip_leader": self.flip_leader,
        }


@dataclass(frozen=True)
class SensitivityResult:
    """Robustez do primeiro lugar, dimensão a dimensão."""

    leader: str
    #: Distância entre o 1º e o 2º colocados, na escala da pontuação.
    margin: float
    #: `margin` comparada à margem de indiferença do `scales.json`.
    margin_within_tolerance: bool
    tie_break_tolerance: float
    dimensions: Tuple[DimensionSensitivity, ...]

    @property
    def most_fragile(self) -> Optional[DimensionSensitivity]:
        """A dimensão cujo peso precisa mudar menos para trocar o líder."""
        candidatas = [d for d in self.dimensions if d.flips]
        return min(candidatas, key=lambda d: d.flip_delta or 0.0) if candidatas else None

    @property
    def robust(self) -> bool:
        """Nenhum peso de dimensão, sozinho, troca o primeiro colocado."""
        return not any(d.flips for d in self.dimensions)

    def as_dict(self) -> Dict[str, Any]:
        frágil = self.most_fragile
        return {
            "leader": self.leader,
            "margin": round(self.margin, 6),
            "margin_within_tolerance": self.margin_within_tolerance,
            "tie_break_tolerance": self.tie_break_tolerance,
            "robust": self.robust,
            "most_fragile_dimension": frágil.dimension if frágil else None,
            "most_fragile_delta": (
                None if frágil is None or frágil.flip_delta is None
                else round(frágil.flip_delta, 4)
            ),
            "dimensions": [d.as_dict() for d in self.dimensions],
        }


def _reescala(
    pesos: Mapping[str, float], dimensao: str, novo_peso: float
) -> Dict[str, float]:
    """
    Novo vetor de pesos com `dimensao` em `novo_peso`, somando 1.

    As demais dimensões são reescaladas proporcionalmente entre si: o que muda é
    o quanto a dimensão testada pesa, não a preferência relativa entre as outras.
    Quando as outras somam zero, o resto é distribuído igualmente — não há
    proporção a preservar.
    """
    outras = {d: w for d, w in pesos.items() if d != dimensao}
    resto = 1.0 - novo_peso
    total_outras = sum(outras.values())
    if total_outras <= 0:
        parcela = resto / len(outras) if outras else 0.0
        novos = {d: parcela for d in outras}
    else:
        novos = {d: w * resto / total_outras for d, w in outras.items()}
    novos[dimensao] = novo_peso
    return novos


def _lider(
    dimension_weights: Mapping[str, float],
    local_weights: Mapping[str, float],
    dimension_by_indicator: Mapping[str, str],
    normalized: Mapping[Tuple[str, str], float],
    provider_ids: Sequence[str],
    valid_indicators: Sequence[str],
) -> Optional[str]:
    """
    Primeiro colocado com este vetor de pesos de dimensão.

    Refaz o caminho inteiro — peso global, renormalização sobre o conjunto
    comparável, soma ponderada — em vez de aproximar por derivada: o conjunto
    comparável não muda com o peso, mas a renormalização sim, e uma aproximação
    linear erraria justamente onde as pontuações estão próximas.
    """
    globais = {
        iid: local_weights[iid] * dimension_weights.get(dimension_by_indicator[iid], 0.0)
        for iid in valid_indicators
        if iid in local_weights
    }
    total = sum(globais.values())
    if total <= 0:
        return None
    efetivos = {iid: w / total for iid, w in globais.items()}

    melhor_id, melhor_score = None, None
    for pid in provider_ids:
        score = sum(
            peso * normalized[(pid, iid)]
            for iid, peso in efetivos.items()
            if (pid, iid) in normalized
        )
        if melhor_score is None or score > melhor_score:
            melhor_id, melhor_score = pid, score
    return melhor_id


def analyze(
    *,
    ranking: Sequence[Mapping[str, Any]],
    dimension_weights: Mapping[str, float],
    local_weights: Mapping[str, float],
    dimension_by_indicator: Mapping[str, str],
    normalized: Mapping[Tuple[str, str], float],
    valid_indicators: Sequence[str],
    tie_break_tolerance: float,
) -> Optional[SensitivityResult]:
    """
    Mede a robustez do primeiro colocado.

    `ranking` vem já ordenado, como o `compute_scores` devolve. Devolve `None`
    quando não há o que analisar — menos de dois provedores ou nenhum indicador
    no conjunto comparável —, porque nesses casos a pergunta não se coloca.
    """
    if len(ranking) < 2 or not valid_indicators:
        return None

    lider_atual = str(ranking[0]["id"])
    margem = float(ranking[0]["score"]) - float(ranking[1]["score"])
    provider_ids = [str(r["id"]) for r in ranking]

    resultados: List[DimensionSensitivity] = []
    for dimensao, peso in dimension_weights.items():
        melhor_delta: Optional[float] = None
        melhor_peso: Optional[float] = None
        melhor_lider: Optional[str] = None

        passos = int(round(1.0 / PASSO))
        for i in range(passos + 1):
            candidato = i * PASSO
            delta = candidato - peso
            if abs(delta) < DESLOCAMENTO_MINIMO:
                continue
            novo = _lider(
                _reescala(dimension_weights, dimensao, candidato),
                local_weights,
                dimension_by_indicator,
                normalized,
                provider_ids,
                valid_indicators,
            )
            if novo is not None and novo != lider_atual:
                if melhor_delta is None or abs(delta) < abs(melhor_delta):
                    melhor_delta, melhor_peso, melhor_lider = delta, candidato, novo

        resultados.append(
            DimensionSensitivity(
                dimension=dimensao,
                weight=float(peso),
                flip_delta=melhor_delta,
                flip_weight=melhor_peso,
                flip_leader=melhor_lider,
            )
        )

    return SensitivityResult(
        leader=lider_atual,
        margin=margem,
        margin_within_tolerance=margem <= tie_break_tolerance,
        tie_break_tolerance=float(tie_break_tolerance),
        dimensions=tuple(resultados),
    )


__all__ = ["DimensionSensitivity", "SensitivityResult", "analyze"]
