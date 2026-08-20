"""
Documentos da base RAG: diretórios, inventário e execução da ingestão.

Extraído de `main.py` porque a área de gestão (`admin.py`) também precisa
executar e inspecionar a ingestão global — e `main` importa `admin`, de modo que
a função compartilhada não podia continuar morando lá sem criar um ciclo de
importação. O pipeline em si continua sendo o de `rag.ingest_paths`: aqui só
ficam a descoberta dos arquivos, a serialização das gravações no índice e o
registro dos documentos no banco de auditoria.

Os diretórios são resolvidos **a cada chamada**, e não uma vez no import: os
testes trocam `PDF_DIR`/`UPLOAD_DIR` por variável de ambiente entre um caso e
outro, e um caminho congelado no import apontaria para o `tmp_path` do primeiro.
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from starlette.concurrency import run_in_threadpool

import db
import rag
from config import get_settings
from guardrails import GuardrailLog, resolve_within
from rag.metadata import SCOPE_GLOBAL, document_id_for, now_iso, year_from_name

logger = logging.getLogger("uvicorn.error")

# Serializa gravações no índice FAISS: a ingestão roda em threadpool (não
# bloqueia o event loop), mas duas ingestões em paralelo colidiriam no save_local.
_ingest_lock = asyncio.Lock()


def allowed_extensions() -> tuple:
    return get_settings().allowed_upload_extensions


def pdf_dir() -> Path:
    """data/pdf: documentos do administrador, consultados em todas as buscas RAG."""
    directory = Path(os.getenv("PDF_DIR", "../data/pdf")).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def upload_base_dir() -> Path:
    """data/upload: base dos uploads por sessão (data/upload/<session_id>)."""
    directory = Path(os.getenv("UPLOAD_DIR", "../data/upload")).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def list_files(directory: Path) -> List[Path]:
    """Arquivos de formato aceito no diretório, em ordem estável."""
    if not directory.is_dir():
        return []
    return sorted(
        p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in allowed_extensions()
    )


def count_documents(directory: Path) -> int:
    return len(list_files(directory))


def global_paths(file_names: Optional[List[str]] = None) -> List[str]:
    """
    Caminhos a ingerir em data/pdf.

    Sem seleção, todos os arquivos do diretório. Com seleção, apenas os nomes
    indicados — resolvidos por `resolve_within`, para que um nome vindo da
    requisição não consiga apontar para fora de data/pdf.
    """
    directory = pdf_dir()
    if not file_names:
        return [str(p) for p in list_files(directory)]

    paths: List[str] = []
    for name in file_names:
        candidate = resolve_within(directory, name)
        if not candidate.is_file() or candidate.suffix.lower() not in allowed_extensions():
            raise FileNotFoundError(f"Arquivo não encontrado em data/pdf: {Path(name).name}")
        paths.append(str(candidate))
    return paths


async def run_ingestion(
    paths: List[str], scope: str, session_id: Optional[str]
) -> Dict[str, Any]:
    """Ingestão + registro dos documentos, compartilhado entre global e sessão."""
    guardrail_log = GuardrailLog()
    async with _ingest_lock:
        result = await run_in_threadpool(
            rag.ingest_paths, paths, scope, session_id, None, guardrail_log
        )

    details = [{**detail, "session_id": session_id} for detail in result.get("details", [])]
    try:
        await run_in_threadpool(db.save_documents, details)
    except Exception:
        # O índice já foi gravado; perder o registro do documento não invalida a
        # ingestão, mas precisa aparecer no log.
        logger.exception("Falha ao registrar os documentos ingeridos")

    result["guardrail_events"] = guardrail_log.as_dicts()
    return result


# ---------------------------------------------------------------------------
# Ingestão global como tarefa acompanhável
#
# Indexar a base inteira leva minutos: o nginx do frontend corta a requisição em
# 300s (`proxy_read_timeout`) e a tela reportava falha para um trabalho que
# continuava rodando no backend. Então a rota **inicia** a ingestão e devolve na
# hora; o painel acompanha por polling.
#
# O estado vive em memória, num único processo: reiniciar o backend perde o
# acompanhamento (a parte já indexada permanece no índice, porque cada arquivo é
# gravado ao terminar). Fila persistente seria desproporcional para uma operação
# que um administrador dispara manualmente.
# ---------------------------------------------------------------------------

STATE_IDLE = "idle"
STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_ERROR = "error"

_job: Dict[str, Any] = {
    "state": STATE_IDLE,
    "job_id": None,
    "files": [],
    "started_at": None,
    "finished_at": None,
    "progress": {"done": 0, "total": 0, "current": None},
    "result": None,
    "error": None,
}
# O asyncio só guarda referência fraca para a task: sem isto ela pode ser
# coletada no meio da execução.
_job_task: Optional[Any] = None


class IngestionInProgress(RuntimeError):
    """Já existe uma ingestão global em curso."""


def current_job() -> Dict[str, Any]:
    """Cópia do estado da ingestão global, para o painel acompanhar."""
    return {**_job, "progress": dict(_job["progress"])}


def _empty_result() -> Dict[str, Any]:
    return {
        "chunks": 0,
        "files_processed": 0,
        "files_failed": 0,
        "details": [],
        "unassigned_files": [],
        "documents": [],
        "errors": [],
        "guardrail_events": [],
    }


def _merge_result(accumulated: Dict[str, Any], part: Dict[str, Any]) -> None:
    for key in ("chunks", "files_processed", "files_failed"):
        accumulated[key] += part.get(key, 0)
    for key in ("details", "unassigned_files", "documents", "errors", "guardrail_events"):
        accumulated[key].extend(part.get(key, []))


async def _run_global_job(paths: List[str]) -> None:
    """
    Executa a ingestão **um arquivo por vez**.

    Arquivo a arquivo, e não o lote inteiro de uma vez, por dois motivos: o
    progresso passa a ser real (e não uma barra inventada), e o que já foi
    indexado fica gravado no índice mesmo que o processo caia no meio.
    """
    total = len(paths)
    accumulated = _empty_result()
    try:
        for position, path in enumerate(paths):
            _job["progress"] = {"done": position, "total": total, "current": Path(path).name}
            part = await run_ingestion([path], SCOPE_GLOBAL, None)
            _merge_result(accumulated, part)
            # Resultado parcial visível durante a execução: um erro no terceiro
            # arquivo aparece antes de o décimo segundo terminar.
            _job["result"] = {**accumulated}
        _job["progress"] = {"done": total, "total": total, "current": None}
        _job["state"] = STATE_DONE
    except Exception as exc:
        logger.exception("Falha na ingestão global")
        _job["state"] = STATE_ERROR
        _job["error"] = f"{type(exc).__name__}: {exc}"
        _job["result"] = {**accumulated}
    finally:
        _job["finished_at"] = now_iso()


async def start_global_ingestion(file_names: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Inicia a ingestão global e devolve o estado inicial do job.

    A descoberta e a validação dos caminhos acontecem aqui, de forma síncrona:
    nome inválido ou diretório vazio precisa virar resposta de erro imediata, e
    não um job que falha logo depois.
    """
    global _job_task

    if _job["state"] == STATE_RUNNING:
        raise IngestionInProgress("Já existe uma ingestão em andamento.")

    paths = global_paths(file_names)
    if not paths:
        return {**current_job(), "paths": []}

    _job.update(
        {
            "state": STATE_RUNNING,
            "job_id": uuid4().hex,
            "files": [Path(p).name for p in paths],
            "started_at": now_iso(),
            "finished_at": None,
            "progress": {"done": 0, "total": len(paths), "current": None},
            "result": None,
            "error": None,
        }
    )
    _job_task = asyncio.create_task(_run_global_job(paths))
    return {**current_job(), "paths": paths}


# Hash do conteúdo por (caminho, mtime, tamanho). O inventário é consultado a
# cada abertura do painel e a cada acompanhamento de ingestão; sem cache, cada
# consulta releria a base inteira do disco só para recalcular ids que não mudaram.
_document_id_cache: Dict[str, Any] = {}


def _document_id_cached(path: Path, stat: os.stat_result) -> str:
    key = (str(path), stat.st_mtime, stat.st_size)
    cached = _document_id_cache.get(str(path))
    if cached and cached[0] == key:
        return cached[1]
    document_id = document_id_for(path.read_bytes())
    _document_id_cache[str(path)] = (key, document_id)
    return document_id


def global_inventory() -> Dict[str, Any]:
    """
    Estado da base documental global, para a área de gestão.

    Cruza três fontes que podem discordar entre si, e é justamente a discordância
    que interessa ao administrador:

      - o **diretório** data/pdf (o que existe no servidor);
      - a tabela `documents` (o que já foi ingerido alguma vez);
      - o **índice FAISS** (o que de fato responde às buscas).

    Um arquivo novo no diretório aparece como pendente; um índice apagado com
    documentos registrados aparece em `index_ready: false`. Nenhum dos dois é
    inferido do outro.
    """
    from providers_data import PROVIDERS  # import local evita ciclo de importação

    settings = get_settings()
    registered = {
        record["document_id"]: record
        for record in db.list_documents()
        if record.get("scope") == SCOPE_GLOBAL
    }

    files: List[Dict[str, Any]] = []
    for path in list_files(pdf_dir()):
        try:
            stat = path.stat()
            document_id = _document_id_cached(path, stat)
        except OSError as exc:
            logger.warning("Não foi possível ler %s em data/pdf: %s", path.name, exc)
            continue

        # O document_id é o hash do conteúdo: editar o arquivo muda o id e o
        # documento volta a constar como pendente — que é o comportamento certo,
        # porque o conteúdo indexado deixou de ser o do arquivo em disco.
        record = registered.get(document_id)
        provider_id = rag.detect_provider_id(path.name)
        files.append(
            {
                "name": path.name,
                "size": stat.st_size,
                "modified_at": stat.st_mtime,
                "document_id": document_id,
                "provider_id": provider_id,
                "year": year_from_name(path.name),
                "indexed": record is not None,
                "chunks": record.get("chunk_count") if record else None,
                "ingested_at": record.get("ingested_at") if record else None,
            }
        )

    chunk_counts = rag.count_chunks_by_provider()
    return {
        "pdf_dir": str(pdf_dir()),
        "allowed_extensions": list(allowed_extensions()),
        "index_ready": rag.is_ready(),
        "job": current_job(),
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
        "files": files,
        "pending_files": sum(1 for f in files if not f["indexed"]),
        # Sem provedor identificável no nome: são indexados, mas não viram
        # evidência de ninguém (ver `doc_keywords` em providers_data).
        "unassigned_files": [f["name"] for f in files if not f["provider_id"]],
        "documents_indexed": len(registered),
        "chunks_total": rag.count_chunks(),
        # Provedor sem nenhum trecho indexado fica fora do ranking (§ cobertura
        # documental em /api/recommend), então a lista traz todos, inclusive os zerados.
        "providers": [
            {"id": p["id"], "name": p["name"], "chunks": chunk_counts.get(p["id"], 0)}
            for p in PROVIDERS
        ],
    }


__all__ = [
    "IngestionInProgress",
    "allowed_extensions",
    "current_job",
    "count_documents",
    "global_inventory",
    "global_paths",
    "list_files",
    "pdf_dir",
    "run_ingestion",
    "start_global_ingestion",
    "upload_base_dir",
]
