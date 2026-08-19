"""
Endpoints da área de gestão (/api/admin/*).

Todas as rotas de leitura exigem o token emitido em /api/admin/login. Só o
próprio login fica aberto — e ainda assim com trava por IP.
"""

import csv
import io
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

import auth
import db

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
