"""
Analytic Hierarchy Process (Saaty).

O método está implementado por completo:
  1. a matriz de comparação par a par vem direto da seção D do questionário, em
     que o gestor informa qual dimensão prioriza e com que intensidade verbal —
     elicitação clássica, convertida para a razão de Saaty em `pairwise.py`;
  2. as prioridades saem do autovetor principal dessa matriz;
  3. a razão de consistência (CR) é calculada e reportada — e aqui ela mede a
     coerência real dos julgamentos do gestor.

As perguntas de relevância (escala 1-5, seções A/B/C) NÃO entram nesta matriz.
Relevância individual de um indicador e preferência relativa entre duas dimensões
são conceitos distintos; convertê-los um no outro produziria julgamentos que o
gestor nunca deu. Aquelas respostas são reportadas à parte, como perfil de
prioridade dos indicadores dentro de cada dimensão.
"""

from typing import Any, Dict, List, Sequence, Tuple

import numpy as np
import pandas as pd

# Índice Randômico de Saaty, por ordem da matriz (n). Usado no denominador do CR.
SAATY_RANDOM_INDEX = {1: 0.0, 2: 0.0, 3: 0.58, 4: 0.90, 5: 1.12, 6: 1.24, 7: 1.32, 8: 1.41, 9: 1.45, 10: 1.49}

# Acima disso os julgamentos são considerados inconsistentes demais (Saaty: 0.10).
CONSISTENCY_THRESHOLD = 0.10


def normalize_weights(raw_weights: dict) -> dict:
    s = sum(raw_weights.values())
    return {k: (v / s if s > 0 else 1 / len(raw_weights)) for k, v in raw_weights.items()}


def judgments_to_pairwise_matrix(
    judgments: Dict[str, Dict[str, Any]],
    criteria: Sequence[str],
) -> Tuple[List[str], np.ndarray, List[str]]:
    """
    Monta a matriz recíproca a partir dos julgamentos diretos da seção D.

    `judgments` é chaveado por "<critério A>|<critério B>" e traz a razão a_ij na
    escala de Saaty (1, 3, 5, 7, 9 e recíprocos). Cada julgamento preenche a_ij e
    a_ji = 1/a_ij, o que garante reciprocidade e diagonal unitária.

    Pares sem julgamento ficam em 1 (indiferença) e são devolvidos na lista de
    faltantes, para que a resposta da API deixe explícito que aquela célula não
    veio do gestor.
    """
    keys = list(criteria)
    n = len(keys)
    matrix = np.ones((n, n), dtype=float)
    missing: List[str] = []

    for i in range(n):
        for j in range(i + 1, n):
            direct = judgments.get(f"{keys[i]}|{keys[j]}")
            inverse = judgments.get(f"{keys[j]}|{keys[i]}")
            if direct is not None:
                ratio = float(direct["ratio"])
            elif inverse is not None:
                ratio = 1.0 / float(inverse["ratio"])
            else:
                missing.append(f"{keys[i]}|{keys[j]}")
                continue

            ratio = min(max(ratio, 1.0 / 9.0), 9.0)
            matrix[i][j] = ratio
            matrix[j][i] = 1.0 / ratio

    return keys, matrix, missing


def normalized_matrix(matrix: np.ndarray) -> np.ndarray:
    """
    Matriz normalizada pela soma de cada coluna (§6.3):

        N_ij = A_ij / Σ_i(A_ij)

    Coluna de soma zero não acontece numa matriz recíproca válida (todos os
    elementos são positivos), mas a guarda existe para que uma matriz malformada
    produza zeros em vez de NaN silencioso.
    """
    totals = matrix.sum(axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.divide(
            matrix, totals, out=np.zeros_like(matrix, dtype=float), where=totals != 0
        )


def _weights_by_column_mean(matrix: np.ndarray) -> np.ndarray:
    """
    Procedimento da dissertação (§6.3): normalizar por coluna e tirar a média
    aritmética de cada linha da matriz normalizada.
    """
    return normalized_matrix(matrix).mean(axis=1)


def _weights_by_power_method(matrix: np.ndarray, max_iter: int = 500, tol: float = 1e-12) -> np.ndarray:
    """Autovetor principal pelo método das potências, normalizado para somar 1."""
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
    return w


def priority_vector(
    matrix: np.ndarray,
    method: str = "column_mean",
    random_index_table: Dict[int, float] | None = None,
):
    """
    Vetor de prioridades, mais λmax, o índice (CI) e a razão de consistência (CR).

    **`column_mean` é o padrão porque é o procedimento descrito na §6.3** — somar
    cada coluna, dividir cada elemento pela soma da sua coluna e tirar a média
    aritmética de cada linha. É também o método que reproduz os valores esperados
    no teste obrigatório da §6.5.

    `eigenvector` (método das potências) continua disponível e era o que o código
    usava antes da Fase 1. Para a matriz de referência da §6.5 os dois divergem em
    ~0,007 no primeiro peso: pouco, mas acima de erro de ponto flutuante — não é
    uma escolha indiferente, e por isso ela é explícita e configurável.

    O cálculo de consistência é idêntico nos dois casos e sempre usa a matriz
    original: λmax = média de (A·w)_i / w_i.
    """
    n = matrix.shape[0]

    if method == "eigenvector":
        w = _weights_by_power_method(matrix)
    elif method == "column_mean":
        w = _weights_by_column_mean(matrix)
    else:
        raise ValueError(f"Método de ponderação AHP desconhecido: {method!r}.")

    total = w.sum()
    if total > 0:
        w = w / total

    # λmax = média de (A·w)_i / w_i
    aw = matrix @ w
    with np.errstate(divide="ignore", invalid="ignore"):
        ratios = np.divide(aw, w, out=np.zeros_like(aw), where=w != 0)
    lambda_max = float(ratios[w != 0].mean()) if np.any(w != 0) else float(n)

    consistency_index = (lambda_max - n) / (n - 1) if n > 1 else 0.0
    table = random_index_table or SAATY_RANDOM_INDEX
    random_index = table.get(n, 1.49)
    consistency_ratio = (consistency_index / random_index) if random_index > 0 else 0.0

    return w, lambda_max, consistency_index, consistency_ratio


def derive_criteria_weights(
    judgments: Dict[str, Dict[str, Any]],
    criteria: Sequence[str],
    method: str | None = None,
    consistency_threshold: float | None = None,
    random_index_table: Dict[int, float] | None = None,
) -> Dict[str, object]:
    """
    Pipeline completo do AHP para as dimensões: julgamentos par a par → matriz →
    vetor de prioridades → pesos + diagnóstico de consistência.

    O método e o limite de consistência vêm da configuração metodológica quando
    não são informados — são decisão da dissertação, não do código.
    """
    if method is None or consistency_threshold is None or random_index_table is None:
        # Import local: `ahp` é domínio puro e não deve exigir a configuração
        # carregada para ser usado (os testes chamam com os parâmetros diretos).
        from domain.methodology import get_methodology

        methodology = get_methodology()
        method = method or methodology.ahp_weight_method
        if consistency_threshold is None:
            consistency_threshold = methodology.ahp_consistency_threshold
        random_index_table = random_index_table or dict(methodology.ahp_random_index)

    keys, matrix, missing = judgments_to_pairwise_matrix(judgments, criteria)
    weights, lambda_max, ci, cr = priority_vector(
        matrix, method=method, random_index_table=random_index_table
    )
    normalized = normalized_matrix(matrix)

    return {
        "weights": {k: float(w) for k, w in zip(keys, weights)},
        "criteria_order": keys,
        "weight_method": method,
        "pairwise_matrix": [[round(float(v), 4) for v in row] for row in matrix],
        # §32.2 exige a matriz normalizada persistida junto da original: é o passo
        # intermediário que permite refazer a conta dos pesos à mão.
        "normalized_matrix": [[round(float(v), 6) for v in row] for row in normalized],
        # Além da razão, o julgamento como o gestor o informou (dimensão
        # priorizada + intensidade verbal) e a frase legível equivalente: a
        # memória de cálculo mostra a resposta, não só o número derivado dela.
        "judgments": {
            k: {
                "ratio": round(float(v["ratio"]), 4),
                "choice": v.get("choice"),
                "question_id": v.get("question_id"),
                "preference": v.get("preference"),
                "intensity": v.get("intensity"),
                "saaty_intensity": v.get("saaty_intensity"),
            }
            for k, v in judgments.items()
        },
        "missing_judgments": missing,
        "lambda_max": round(float(lambda_max), 4),
        "consistency_index": round(float(ci), 4),
        "random_index": random_index_table.get(len(keys)),
        "consistency_ratio": round(float(cr), 4),
        "is_consistent": bool(cr <= consistency_threshold),
        "consistency_threshold": consistency_threshold,
    }


def compute_ahp_ranking(criteria_weights: dict, providers: list) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Síntese das prioridades das alternativas (modo distributivo do AHP): as notas
    de cada provedor são normalizadas dentro de cada critério (somam 1 por
    critério) antes da agregação ponderada. Por isso as prioridades finais também
    somam 1 entre os provedores — diferente de uma soma ponderada simples, cujos
    scores ficariam na faixa 0-1 de cada nota bruta.

    Devolve o ranking e a memória de cálculo da síntese, célula a célula, para que
    o score final possa ser reconstruído à mão a partir do relatório:

        normalizada = nota / soma das notas do critério
        contribuição = peso do critério × normalizada
        score = Σ contribuições
    """
    cw = normalize_weights(criteria_weights)

    # Normalização por critério (coluna) entre os provedores
    column_totals = {
        c: sum(p["scores"].get(c, 0.5) for p in providers) for c in cw
    }

    rows = []
    audit_providers = []
    for p in providers:
        priority = 0.0
        cells = {}
        for c, w in cw.items():
            value = p["scores"].get(c, 0.5)
            total = column_totals.get(c, 0.0)
            normalized = (value / total) if total > 0 else (1.0 / len(providers))
            contribution = w * normalized
            priority += contribution
            cells[c] = {
                "raw": round(float(value), 4),
                "normalized": round(float(normalized), 6),
                "weight": round(float(w), 6),
                "contribution": round(float(contribution), 6),
            }
        rows.append({"id": p["id"], "name": p["name"], "score": priority})
        audit_providers.append(
            {"id": p["id"], "name": p["name"], "cells": cells, "score": round(float(priority), 6)}
        )

    df = pd.DataFrame(rows)
    df = df.sort_values(by="score", ascending=False).reset_index(drop=True)
    df["rank"] = df.index + 1

    order = {pid: i for i, pid in enumerate(df["id"])}
    audit_providers.sort(key=lambda a: order.get(a["id"], len(order)))

    synthesis = {
        "mode": "distributive",
        "criteria_order": list(cw.keys()),
        "weights": {c: round(float(w), 6) for c, w in cw.items()},
        "column_totals": {c: round(float(t), 4) for c, t in column_totals.items()},
        "providers": audit_providers,
        # Deve ser 1 (a menos de arredondamento): serve de verificação no relatório.
        "score_total": round(float(sum(r["score"] for r in rows)), 6),
    }
    return df, synthesis
