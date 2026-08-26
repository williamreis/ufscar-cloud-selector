"""Recuperação documental: índice, ingestão e busca (diretriz §13–§16)."""

from rag.index import (
    count_chunks,
    count_chunks_by_provider,
    delete as delete_index,
    invalidate_cache,
    is_ready,
    load,
    save,
)
from rag.ingest import detect_provider_id, ingest_paths, load_and_chunk
from rag.metadata import SCOPE_GLOBAL, evaluation_scope
from rag.queries import query_for_indicator
from rag.retrieval import format_hit, search
from rag.terms import terms_found

__all__ = [
    "SCOPE_GLOBAL",
    "count_chunks",
    "count_chunks_by_provider",
    "delete_index",
    "detect_provider_id",
    "evaluation_scope",
    "format_hit",
    "ingest_paths",
    "invalidate_cache",
    "is_ready",
    "load",
    "load_and_chunk",
    "query_for_indicator",
    "save",
    "search",
    "terms_found",
]
