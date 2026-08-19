"""Recuperação documental: índice, ingestão e busca (diretriz §13–§16)."""

from rag.index import count_chunks_by_provider, invalidate_cache, is_ready, load, save
from rag.ingest import detect_provider_id, ingest_paths, load_and_chunk
from rag.metadata import SCOPE_GLOBAL, evaluation_scope
from rag.queries import DIMENSION_QUERIES, query_for
from rag.retrieval import format_hit, search

__all__ = [
    "DIMENSION_QUERIES",
    "SCOPE_GLOBAL",
    "count_chunks_by_provider",
    "detect_provider_id",
    "evaluation_scope",
    "format_hit",
    "ingest_paths",
    "invalidate_cache",
    "is_ready",
    "load",
    "load_and_chunk",
    "query_for",
    "save",
    "search",
]
