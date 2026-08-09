"""
Analytic Hierarchy Process (Saaty).

A versão anterior deste módulo fazia apenas uma soma ponderada (Simple Additive
Weighting) e chamava o resultado de AHP: não havia matriz de comparação par a par,
nem derivação de prioridades por autovetor, nem razão de consistência.

Aqui o método está implementado de fato:
  1. as intensidades por critério (escala 1-5, vindas do questionário) viram uma
     matriz de comparação par a par na escala de Saaty (1-9);
  2. as prioridades saem do autovetor principal dessa matriz;
  3. a razão de consistência (CR) é calculada e reportada.

Ressalva honesta sobre o passo 1: no AHP clássico o gestor informa cada comparação
diretamente ("quanto A é mais importante que B, de 1 a 9"). Aqui a matriz é
*derivada* das respostas do questionário, o que é uma adaptação — o questionário
não pede as comparações uma a uma. A derivação é determinística e auditável (a
matriz vai na resposta da API), mas não substitui a elicitação par a par direta.
"""

from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

# Índice Randômico de Saaty, por ordem da matriz (n). Usado no denominador do CR.
SAATY_RANDOM_INDEX = {1: 0.0, 2: 0.0, 3: 0.58, 4: 0.90, 5: 1.12, 6: 1.24, 7: 1.32, 8: 1.41, 9: 1.45, 10: 1.49}

# Acima disso os julgamentos são considerados inconsistentes demais (Saaty: 0.10).
CONSISTENCY_THRESHOLD = 0.10


def normalize_weights(raw_weights: dict) -> dict:
    s = sum(raw_weights.values())
    return {k: (v / s if s > 0 else 1 / len(raw_weights)) for k, v in raw_weights.items()}


def intensities_to_pairwise_matrix(intensities: Dict[str, float]) -> Tuple[List[str], np.ndarray]:
    """
    Converte intensidades (1-5) em matriz de comparação par a par recíproca.

    A diferença de intensidade entre dois critérios vira uma razão na escala ímpar
    de Saaty: diferença 0 → 1 (igual), 1 → 3 (moderada), 2 → 5 (forte),
    3 → 7 (muito forte), 4 → 9 (extrema). O recíproco é atribuído ao par inverso,
    o que garante a_ij = 1/a_ji e diagonal unitária.
    """
    keys = list(intensities.keys())
    n = len(keys)
    matrix = np.ones((n, n), dtype=float)

    for i in range(n):
        for j in range(i + 1, n):
            diff = intensities[keys[i]] - intensities[keys[j]]
            ratio = min(1.0 + 2.0 * abs(diff), 9.0)
            if diff >= 0:
                matrix[i][j] = ratio
                matrix[j][i] = 1.0 / ratio
            else:
                matrix[i][j] = 1.0 / ratio
                matrix[j][i] = ratio

    return keys, matrix


def priority_vector(matrix: np.ndarray, max_iter: int = 500, tol: float = 1e-12):
    """
    Autovetor principal pelo método das potências, normalizado para somar 1,
    mais λmax, o índice (CI) e a razão de consistência (CR).
    """
    n = matrix.shape[0]
    w = np.ones(n, dtype=float) / n

    for _ in range(max_iter):
        w_next = matrix @ w
        total = w_next.sum()
        if total == 0:
            break
        w_next = w_next / total
        if np.allclose(w, w_next, atol=tol):
            w = w_next
            break
        w = w_next

    # λmax = média de (A·w)_i / w_i
    aw = matrix @ w
    with np.errstate(divide="ignore", invalid="ignore"):
        ratios = np.divide(aw, w, out=np.zeros_like(aw), where=w != 0)
    lambda_max = float(ratios[w != 0].mean()) if np.any(w != 0) else float(n)

    consistency_index = (lambda_max - n) / (n - 1) if n > 1 else 0.0
    random_index = SAATY_RANDOM_INDEX.get(n, 1.49)
    consistency_ratio = (consistency_index / random_index) if random_index > 0 else 0.0

    return w, lambda_max, consistency_index, consistency_ratio


def derive_criteria_weights(intensities: Dict[str, float]) -> Dict[str, object]:
    """
    Pipeline completo do AHP para os critérios: intensidades → matriz par a par →
    autovetor → pesos + diagnóstico de consistência.
    """
    keys, matrix = intensities_to_pairwise_matrix(intensities)
    weights, lambda_max, ci, cr = priority_vector(matrix)

    return {
        "weights": {k: float(w) for k, w in zip(keys, weights)},
        "criteria_order": keys,
        "pairwise_matrix": [[round(float(v), 4) for v in row] for row in matrix],
        "intensities": {k: round(float(v), 3) for k, v in intensities.items()},
        "lambda_max": round(float(lambda_max), 4),
        "consistency_index": round(float(ci), 4),
        "consistency_ratio": round(float(cr), 4),
        "is_consistent": bool(cr <= CONSISTENCY_THRESHOLD),
        "consistency_threshold": CONSISTENCY_THRESHOLD,
    }


def compute_ahp_ranking(criteria_weights: dict, providers: list) -> pd.DataFrame:
    """
    Síntese das prioridades das alternativas (modo distributivo do AHP): as notas
    de cada provedor são normalizadas dentro de cada critério (somam 1 por
    critério) antes da agregação ponderada. Por isso as prioridades finais também
    somam 1 entre os provedores — diferente da soma ponderada anterior, cujos
    scores ficavam na faixa 0-1 de cada nota bruta.
    """
    cw = normalize_weights(criteria_weights)

    # Normalização por critério (coluna) entre os provedores
    column_totals = {
        c: sum(p["scores"].get(c, 0.5) for p in providers) for c in cw
    }

    rows = []
    for p in providers:
        priority = 0.0
        for c, w in cw.items():
            value = p["scores"].get(c, 0.5)
            total = column_totals.get(c, 0.0)
            normalized = (value / total) if total > 0 else (1.0 / len(providers))
            priority += w * normalized
        rows.append({"id": p["id"], "name": p["name"], "score": priority})

    df = pd.DataFrame(rows)
    df = df.sort_values(by="score", ascending=False).reset_index(drop=True)
    df["rank"] = df.index + 1
    return df
