import numpy as np
import pandas as pd


def normalize_weights(raw_weights: dict):
    s = sum(raw_weights.values())
    return {k: (v / s if s > 0 else 1 / len(raw_weights)) for k, v in raw_weights.items()}


def compute_ahp_ranking(criteria_weights: dict, providers: list):
    """
    providers: list of dicts, cada dict com 'id' e 'scores' por critério:
    providers = [
        {"id": "aws", "name":"AWS", "scores": {"sustainability":0.7,"performance":0.9,"security":0.85}},
        ...
    ]
    Retorna pandas DataFrame com ranking e score final.
    """
    # normaliza criteria_weights
    cw = normalize_weights(criteria_weights)
    rows = []
    for p in providers:
        # assume provider has 'scores' with 0-1 values; se não, normalizar
        provider_score = 0.0
        for c, w in cw.items():
            val = p["scores"].get(c, 0.5)  # default neutro 0.5
            provider_score += w * val
        rows.append({"id": p["id"], "name": p["name"], "score": provider_score})
    df = pd.DataFrame(rows)
    df = df.sort_values(by="score", ascending=False).reset_index(drop=True)
    df["rank"] = df.index + 1
    return df
