"""
Índice vetorial FAISS: carga, cache e escrita.

Extraído de `llm_utils` sem mudança de comportamento. O cache continua invalidado
pelo mtime do arquivo — a ingestão regrava o índice, e sem isso cada consulta
relia o arquivo inteiro do disco e reconstruía o objeto.
"""

import os
from pathlib import Path
from typing import Any, Dict, Optional

from config import get_settings
from llm.embeddings import get_embedding_function


def index_path() -> str:
    return get_settings().vector_db_path


def is_ready(path: Optional[str] = None) -> bool:
    p = Path(path or index_path())
    return (p / "index.faiss").is_file() and (p / "index.pkl").is_file()


_cache: Dict[str, Any] = {"mtime": None, "index": None, "path": None}


def load(use_cache: bool = True) -> Optional[Any]:
    """Índice persistido, ou None quando ainda não houve ingestão."""
    path = index_path()
    if not is_ready(path):
        return None

    from langchain_community.vectorstores import FAISS

    mtime = os.path.getmtime(os.path.join(path, "index.faiss"))
    if (
        use_cache
        and _cache["index"] is not None
        and _cache["mtime"] == mtime
        and _cache["path"] == path
    ):
        return _cache["index"]

    # allow_dangerous_deserialization: o índice é gerado e lido apenas por nós
    # mesmos (ingestão local), não é arquivo de origem externa/não confiável.
    index = FAISS.load_local(path, get_embedding_function(), allow_dangerous_deserialization=True)
    if use_cache:
        _cache.update({"mtime": mtime, "index": index, "path": path})
    return index


def invalidate_cache() -> None:
    _cache.update({"mtime": None, "index": None, "path": None})


def save(index: Any) -> None:
    path = index_path()
    Path(path).mkdir(parents=True, exist_ok=True)
    index.save_local(path)
    invalidate_cache()


def count_chunks_by_provider() -> Dict[str, int]:
    """
    Quantos trechos indexados existem por provedor. Usado para excluir do ranking
    provedores sem nenhuma base documental.
    """
    index = load()
    if index is None:
        return {}

    counts: Dict[str, int] = {}
    for doc in index.docstore._dict.values():
        metadata = doc.metadata or {}
        # `provider_id` é a chave nova; `provider` é a do índice já persistido.
        provider = metadata.get("provider_id") or metadata.get("provider")
        if provider:
            counts[provider] = counts.get(provider, 0) + 1
    return counts


__all__ = [
    "count_chunks_by_provider",
    "index_path",
    "invalidate_cache",
    "is_ready",
    "load",
    "save",
]
