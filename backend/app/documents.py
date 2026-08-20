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

from starlette.concurrency import run_in_threadpool

import db
import rag
from config import get_settings
from guardrails import GuardrailLog, resolve_within
from rag.metadata import SCOPE_GLOBAL, document_id_for, year_from_name

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
            document_id = document_id_for(path.read_bytes())
            stat = path.stat()
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
    "allowed_extensions",
    "count_documents",
    "global_inventory",
    "global_paths",
    "list_files",
    "pdf_dir",
    "run_ingestion",
    "upload_base_dir",
]
