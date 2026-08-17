import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from typing import List, Dict, Optional

from schemas import CRITERIA, QuestionnaireResponse, RecommendationResponse
from llm_utils import (
    llm_explain_preferences,
    rag_query_documents,
    ingest_documents_from_paths,
    count_chunks_by_provider,
    CRITERIA_QUERIES,
    METADATA_SOURCE_GLOBAL,
    METADATA_SOURCE_SESSION,
)
from ahp import compute_ahp_ranking, derive_criteria_weights
from providers_data import PROVIDERS, PROVIDER_SCORES_PROVENANCE
import auth
import db
from admin import router as admin_router

logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Cria o banco de auditoria se ainda não existir (idempotente)."""
    db.init_db()
    if not auth.is_configured():
        logger.warning(
            "ADMIN_PASSWORD não definido: a área de gestão (/api/admin) fica indisponível."
        )
    yield


app = FastAPI(title="Cloud Provider Selector API", lifespan=lifespan)
app.include_router(admin_router)

# Em produção o nginx do frontend faz proxy same-origin para /api (CORS nem entra
# em jogo). Isso aqui existe só para permitir `npm run dev` (Vite) local direto
# contra o backend durante desenvolvimento.
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:8501").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

# data/pdf: documentos do administrador, sempre usados na ingestão e em todas as consultas RAG
PDF_DIR = Path(os.getenv("PDF_DIR", "../data/pdf")).resolve()
PDF_DIR.mkdir(parents=True, exist_ok=True)

# data/upload: base para uploads por sessão; cada sessão em data/upload/<session_id> (uso só na sessão ativa)
UPLOAD_BASE_DIR = Path(os.getenv("UPLOAD_DIR", "../data/upload")).resolve()
UPLOAD_BASE_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {".pdf", ".txt"}

# Serializa gravações no índice FAISS: ingestão roda em threadpool (não bloqueia
# o event loop), mas duas ingestões em paralelo poderiam colidir no save_local.
_ingest_lock = asyncio.Lock()


@app.post("/api/recommend", response_model=RecommendationResponse)
async def recommend(q: QuestionnaireResponse):
    """
    Recebe respostas do gestor, processa com LLM + RAG e AHP,
    e retorna ranking de provedores com justificativas e evidências.
    """

    # 1) Comparações par a par (seção D) → matriz de julgamentos do gestor.
    #    É a única fonte dos pesos entre dimensões. O gestor informa a dimensão
    #    prioritária e a intensidade verbal; a razão de Saaty correspondente é
    #    derivada aqui no servidor (pairwise.py), nunca aceita pronta do cliente.
    judgments = q.pairwise_judgments()

    # 2) Perguntas de relevância (A, B, C) → média 1-5 por dimensão.
    #    Mede a relevância dos indicadores dentro de cada dimensão; não é
    #    convertida para a escala de Saaty (são conceitos distintos), então entra
    #    na memória de cálculo e no contexto do LLM, não nos pesos.
    relevance = q.relevance_by_criterion()

    # 3) AHP: matriz de comparação par a par → autovetor → pesos + consistência
    ahp_result = derive_criteria_weights(judgments, CRITERIA)
    criteria_weights = ahp_result["weights"]

    # 4) LLM redige a justificativa a partir das respostas dissertativas e dos
    #    pesos já calculados. Ele não altera nenhum número: o cálculo permanece
    #    determinístico e reprodutível a partir das respostas fechadas.
    llm_result = await llm_explain_preferences(
        qa_pairs=q.qa_for_llm(),
        relevance=relevance,
        criteria_weights=criteria_weights,
    )
    notes = llm_result.get("notes", "")

    # 5) Só entram no ranking os provedores com base documental indexada.
    #    Sem documentos não há como sustentar a avaliação com evidência, então o
    #    provedor é excluído do relatório em vez de aparecer com nota sem lastro.
    chunk_counts = await run_in_threadpool(count_chunks_by_provider)
    evaluated = [p for p in PROVIDERS if chunk_counts.get(p["id"], 0) > 0]
    excluded = [
        {"id": p["id"], "name": p["name"]}
        for p in PROVIDERS
        if chunk_counts.get(p["id"], 0) == 0
    ]

    if not evaluated:
        raise HTTPException(
            status_code=409,
            detail=(
                "Nenhum provedor possui documentos indexados. Faça a ingestão em "
                "data/pdf (painel /control) antes de gerar uma recomendação."
            ),
        )

    # 6) Síntese das prioridades das alternativas, com a memória de cálculo
    #    célula a célula (nota → normalizada → contribuição → score).
    ranking, synthesis = compute_ahp_ranking(criteria_weights, evaluated)

    # 7) Matriz de scores por provedor (para dashboard: tabela e gráficos)
    providers_by_id = {p["id"]: p for p in evaluated}
    criteria_keys = list(criteria_weights.keys())
    provider_scores = []
    for _, row in ranking.iterrows():
        pid, name, total = row["id"], row["name"], float(row["score"])
        scores = providers_by_id.get(pid, {}).get("scores", {})
        provider_scores.append({
            "id": pid,
            "name": name,
            "rank": int(row["rank"]),
            "score": round(total, 4),
            **{c: round(float(scores.get(c, 0.5)), 4) for c in criteria_keys},
        })

    # 8) RAG para evidências, uma busca por (provedor × critério), de modo que cada
    #    trecho recuperado fique atrelado ao indicador que ele sustenta.
    session_id = getattr(q, "session_id", None) or None
    evidences: Dict[str, list] = {}
    for provider in evaluated:
        pid, pname = provider["id"], provider["name"]
        items = []
        for criterion in criteria_keys:
            terms = CRITERIA_QUERIES.get(criterion, criterion)
            hits = await run_in_threadpool(
                rag_query_documents,
                f"{pname}: {terms}",
                top_k=2,
                session_id=session_id,
                # Restringe aos documentos deste provedor: sem isso, um provedor sem
                # documentos indexados receberia trechos de outro provedor como
                # "evidência" (a similaridade responde aos termos, não ao nome).
                provider_id=pid,
            )
            for h in hits:
                items.append({**h, "criterion": criterion})
        evidences[pid] = items

    # 9) Montar resposta
    response = {
        "ranking": ranking.to_dict(orient="records"),
        "criteria_weights": criteria_weights,
        "provider_scores": provider_scores,
        "notes": notes,
        "evidences": evidences,
        # Memória de cálculo do AHP, para o relatório poder ser auditado
        "ahp": {
            **ahp_result,
            # Perfil de relevância dos indicadores (1-5) por dimensão: contexto do
            # relatório, fora do cálculo dos pesos (ver relevance_by_criterion).
            "relevance_by_criterion": relevance,
        },
        # Memória de cálculo da síntese: como cada score final foi obtido
        "synthesis": synthesis,
        # Respostas fora do cálculo numérico (ver docstring de unscored_answers)
        "unscored_answers": q.unscored_answers(),
        # Cobertura documental: quem foi avaliado e quem ficou de fora, e por quê
        "coverage": {
            "evaluated": [
                {"id": p["id"], "name": p["name"], "chunks": chunk_counts.get(p["id"], 0)}
                for p in evaluated
            ],
            "excluded_no_documents": excluded,
            "scores_provenance": PROVIDER_SCORES_PROVENANCE,
        },
    }

    # 10) Persistir para auditoria. Uma falha aqui não descarta o resultado que o
    #     gestor acabou de gerar — o relatório é devolvido com submission_id nulo,
    #     e a UI avisa que aquele envio não entrou no registro.
    try:
        response["submission_id"] = await run_in_threadpool(
            db.save_submission, q.audit_payload(), response
        )
    except Exception:
        logger.exception("Falha ao gravar o envio no banco de auditoria")
        response["submission_id"] = None

    return response


# ========== Upload e ingestão RAG ==========


def _upload_dir_for_session(session_id: str) -> Path:
    """Diretório de upload da sessão: data/upload/<session_id>."""
    if not session_id or not session_id.strip():
        raise HTTPException(status_code=400, detail="session_id é obrigatório para upload.")
    d = UPLOAD_BASE_DIR / session_id.strip()
    d.mkdir(parents=True, exist_ok=True)
    return d


@app.post("/api/documents/upload")
async def upload_documents(
    files: List[UploadFile] = File(...),
    session_id: Optional[str] = None,
):
    """
    Recebe um ou mais arquivos (PDF ou TXT) e session_id (query), salva em data/upload/<session_id>.
    Esses arquivos são usados somente na sessão ativa; ingerir via POST /api/documents/ingest?session_id=...
    """
    if not session_id:
        raise HTTPException(status_code=400, detail="Query 'session_id' é obrigatória para upload.")
    upload_dir = _upload_dir_for_session(session_id)
    if not files:
        raise HTTPException(status_code=400, detail="Nenhum arquivo enviado.")
    saved = []
    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Formato não permitido: {f.filename}. Use: {', '.join(ALLOWED_EXTENSIONS)}",
            )
        safe_name = f"{uuid.uuid4().hex}_{Path(f.filename).name}"
        path = upload_dir / safe_name
        content = await f.read()
        path.write_bytes(content)
        saved.append({"original_name": f.filename, "stored_name": safe_name, "path": str(path)})
    return {"uploaded": saved, "message": f"{len(saved)} arquivo(s) salvo(s) para a sessão. Realize a ingestão da sessão para indexar no RAG."}


@app.get("/api/documents/file")
async def get_document_file(
    name: str,
    scope: str = METADATA_SOURCE_GLOBAL,
    session_id: Optional[str] = None,
):
    """
    Serve um documento indexado para que a evidência do relatório possa linkar
    direto para o PDF de origem (ex.: .../file?name=x.pdf&scope=global#page=12).
    """
    if scope == METADATA_SOURCE_SESSION:
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id é obrigatório para scope=session.")
        base_dir = (UPLOAD_BASE_DIR / session_id.strip()).resolve()
    else:
        base_dir = PDF_DIR

    # Aceita apenas o nome do arquivo e confirma que o caminho resolvido continua
    # dentro do diretório permitido — bloqueia travessia via "../" ou path absoluto.
    candidate = (base_dir / Path(name).name).resolve()
    if not candidate.is_file() or base_dir not in candidate.parents:
        raise HTTPException(status_code=404, detail="Documento não encontrado.")
    if candidate.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Formato não permitido.")

    media_type = "application/pdf" if candidate.suffix.lower() == ".pdf" else "text/plain"
    # inline: o navegador abre no viewer (necessário para o #page=N funcionar)
    return FileResponse(
        candidate,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{candidate.name}"'},
    )


@app.get("/api/documents/uploaded")
async def list_uploaded_documents(session_id: Optional[str] = None):
    """Lista arquivos em data/upload/<session_id>. session_id obrigatório."""
    if not session_id:
        return {"files": [], "message": "Informe session_id na query."}
    upload_dir = UPLOAD_BASE_DIR / session_id.strip()
    if not upload_dir.is_dir():
        return {"files": []}
    files = []
    for p in upload_dir.iterdir():
        if p.is_file() and p.suffix.lower() in ALLOWED_EXTENSIONS:
            files.append({"name": p.name, "size": p.stat().st_size})
    return {"files": files}


@app.post("/api/documents/ingest-global", dependencies=[Depends(auth.require_admin)])
async def run_ingest_global():
    """
    Ingestão dos documentos em data/pdf (incluídos pelo administrador).
    São indexados com source=global e consultados em todas as buscas RAG.
    """
    paths = [str(p) for p in PDF_DIR.iterdir() if p.is_file() and p.suffix.lower() in ALLOWED_EXTENSIONS]
    if not paths:
        return {
            "chunks": 0,
            "files_processed": 0,
            "message": "Nenhum arquivo em data/pdf. Coloque PDFs ou TXTs em data/pdf e chame novamente.",
            "details": [],
            "errors": [],
        }
    async with _ingest_lock:
        result = await run_in_threadpool(
            ingest_documents_from_paths, paths, doc_metadata={"source": METADATA_SOURCE_GLOBAL}
        )
    return result


@app.post("/api/documents/ingest")
async def run_ingest_session(session_id: Optional[str] = None):
    """
    Ingestão dos documentos da sessão em data/upload/<session_id>.
    São indexados com source=session e session_id; consultados apenas quando a sessão está ativa no recommend.
    """
    if not session_id:
        return {
            "chunks": 0,
            "files_processed": 0,
            "message": "Informe session_id na query para ingestão da sessão.",
            "details": [],
            "errors": [],
        }
    upload_dir = UPLOAD_BASE_DIR / session_id.strip()
    if not upload_dir.is_dir():
        return {"chunks": 0, "files_processed": 0, "message": "Nenhum arquivo para esta sessão.", "details": [], "errors": []}
    paths = [str(p) for p in upload_dir.iterdir() if p.is_file() and p.suffix.lower() in ALLOWED_EXTENSIONS]
    if not paths:
        return {"chunks": 0, "files_processed": 0, "message": "Nenhum arquivo no diretório da sessão.", "details": [], "errors": []}
    async with _ingest_lock:
        result = await run_in_threadpool(
            ingest_documents_from_paths,
            paths,
            doc_metadata={"source": METADATA_SOURCE_SESSION, "session_id": session_id.strip()},
        )
    return result
