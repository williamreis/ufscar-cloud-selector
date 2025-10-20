from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Dict, Optional
from schemas import QuestionnaireResponse, RecommendationResponse
from llm_utils import llm_extract_weights_and_notes, rag_query_documents
from ahp import compute_ahp_ranking
from providers_data import PROVIDERS

app = FastAPI(title="Cloud Provider Selector API")


@app.post("/api/recommend", response_model=RecommendationResponse)
async def recommend(q: QuestionnaireResponse):
    """
    Recebe respostas do gestor, processa com LLM + RAG e AHP,
    e retorna ranking de provedores com justificativas e evidências.
    """

    # 1) Convert respostas fechadas para escores numéricos (1-5)
    numeric_scores = q.to_numeric_scores()
    # numeric_scores: ex. {"sustainability": 4, "performance": 5, "security": 3, ...}

    # 2) Enviar respostas dissertativas + contexto ao LLM para extrair ajustes/weights
    llm_result = await llm_extract_weights_and_notes(
        numeric_scores=numeric_scores,
        free_texts=q.free_texts_dict()
    )
    # llm_result example:
    # {
    #   "criteria_weights": {"sustainability": 0.25, "performance": 0.5, "security": 0.25},
    #   "subcriteria": {...},
    #   "notes": "O gestor destaca alta prioridade para desempenho..."
    # }

    criteria_weights = llm_result["criteria_weights"]
    notes = llm_result.get("notes", "")

    # 3) Aplicar AHP (usando criteria_weights como entradas)
    # compute_ahp_ranking retorna um dataframe/list com scores por provider
    ranking = compute_ahp_ranking(criteria_weights, PROVIDERS)

    # 4) Fazer RAG para buscar evidências por provider (PS: RAG utiliza vectorstore já indexado)
    evidences = {}
    for provider in [p["id"] for p in PROVIDERS]:
        evidences[provider] = rag_query_documents(
            f"evidence for provider {provider} sustainability/security performance", top_k=3)

    # 5) Montar resposta
    response = {
        "ranking": ranking.to_dict(orient="records"),
        "criteria_weights": criteria_weights,
        "notes": notes,
        "evidences": evidences
    }

    return response
