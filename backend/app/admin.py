"""
Endpoints da área de gestão (/api/admin/*).

Todas as rotas exigem o token emitido em /api/admin/login — inclusive as que
escrevem (exclusão de envio e ingestão documental). Só o próprio login fica
aberto, e ainda assim com trava por IP.
"""

import csv
import io
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

import auth
import db
import documents
from guardrails import GuardrailRejection
from rag.metadata import SCOPE_GLOBAL

router = APIRouter(prefix="/api/admin", tags=["admin"])


class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    token: str
    expires_at: int


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request):
    if not auth.is_configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "Área de gestão não configurada: defina ADMIN_PASSWORD no backend/.env "
                "e reinicie o backend."
            ),
        )
    auth.check_lockout(request)

    if not auth.password_matches(body.password):
        auth.register_failure(request)
        raise HTTPException(status_code=401, detail="Senha incorreta.")

    auth.clear_failures(request)
    token, expires_at = auth.issue_token()
    return {"token": token, "expires_at": expires_at}


@router.get("/session", dependencies=[Depends(auth.require_admin)])
async def session_ok():
    """Ping para o frontend descobrir se o token guardado ainda vale."""
    return {"ok": True}


@router.get("/stats", dependencies=[Depends(auth.require_admin)])
async def stats():
    return await run_in_threadpool(db.dashboard_stats)


@router.get("/submissions", dependencies=[Depends(auth.require_admin)])
async def submissions(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    search: Optional[str] = None,
):
    rows, total = await run_in_threadpool(db.list_submissions, limit, offset, search)
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/submissions/{submission_id}", dependencies=[Depends(auth.require_admin)])
async def submission_detail(submission_id: str):
    record = await run_in_threadpool(db.get_submission, submission_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Envio não encontrado.")
    return record


@router.delete("/submissions/{submission_id}", dependencies=[Depends(auth.require_admin)])
async def delete_submission(submission_id: str):
    """
    Exclui um envio definitivamente, junto com respostas, julgamentos e ranking.

    Não há lixeira: o registro sai do banco. A confirmação é responsabilidade da
    interface, que mostra de quem é o envio antes de chamar aqui.
    """
    deleted = await run_in_threadpool(db.delete_submission, submission_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Envio não encontrado.")
    return {"deleted": submission_id}


# ---------------------------------------------------------------------------
# Base documental do RAG
#
# O mesmo pipeline de `POST /api/documents/ingest-global`, exposto aqui com duas
# diferenças que só fazem sentido para quem administra: o **inventário** (o que
# está em data/pdf, o que já foi indexado, quem ficaria fora do ranking por falta
# de documento) e a possibilidade de reingerir **apenas os arquivos escolhidos**,
# em vez do diretório inteiro.
# ---------------------------------------------------------------------------


class RagIngestRequest(BaseModel):
    # Nomes de arquivo em data/pdf. Vazio ou ausente = ingerir o diretório todo.
    files: Optional[List[str]] = None


@router.get("/rag/status", dependencies=[Depends(auth.require_admin)])
async def rag_status():
    """Inventário da base global: arquivos em data/pdf, índice e cobertura por provedor."""
    return await run_in_threadpool(documents.global_inventory)


@router.post("/rag/ingest", dependencies=[Depends(auth.require_admin)])
async def rag_ingest(body: Optional[RagIngestRequest] = None):
    """
    Executa a ingestão dos documentos de data/pdf no índice RAG.

    O registro no banco é idempotente por `document_id` (hash do conteúdo), mas o
    índice FAISS **não**: `rag.ingest_paths` acrescenta os chunks ao índice
    existente, sem remover os da ingestão anterior do mesmo documento. Reingerir
    tudo duplica vetores, e é por isso que a seleção por arquivo existe.
    """
    selected = body.files if body else None
    try:
        paths = await run_in_threadpool(documents.global_paths, selected)
    except GuardrailRejection as exc:
        raise HTTPException(status_code=400, detail=exc.event.reason) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not paths:
        return {
            "chunks": 0,
            "files_processed": 0,
            "message": (
                "Nenhum arquivo em data/pdf. Coloque os PDF/TXT no servidor e execute "
                "a ingestão novamente."
            ),
            "details": [],
            "errors": [],
        }

    return await documents.run_ingestion(paths, SCOPE_GLOBAL, None)


@router.get("/export.csv", dependencies=[Depends(auth.require_admin)])
async def export_csv():
    rows = await run_in_threadpool(db.export_rows)
    if not rows:
        raise HTTPException(status_code=404, detail="Nenhum envio registrado ainda.")

    # União das chaves: envios de versões diferentes do questionário têm colunas
    # diferentes, e nenhuma pode ser descartada silenciosamente na exportação.
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    buffer.seek(0)

    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="questionarios.csv"'},
    )
