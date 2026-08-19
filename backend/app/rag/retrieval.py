"""
Recuperação de trechos (diretriz §13).

O RAG **recupera evidência, não responde à pergunta** — a interpretação é da
etapa seguinte. Este módulo, portanto, devolve trechos com sua procedência e nada
mais.

O filtro por provedor é essencial e continua valendo: a similaridade vetorial é
dominada pelos termos temáticos (energia, data center, segurança), então uma
busca por "Oracle Cloud: eficiência energética" retorna alegremente trechos da
AWS se não houver documento da Oracle indexado. Sem o filtro, o relatório citaria
o documento de um provedor como evidência de outro.
"""

from typing import Any, Dict, List, Optional

from config import get_settings
from rag import index as index_module
from rag.metadata import SCOPE_GLOBAL

# fetch_k alto: o filtro é aplicado após a busca dos vizinhos mais próximos, então
# provedores com poucos documentos precisam de um pool maior de candidatos.
FETCH_K = 500

# Tamanho do trecho devolvido ao frontend. O chunk inteiro fica no índice; isto é
# só o recorte exibido/enviado ao prompt.
EXCERPT_CHARS = 800


def format_hit(doc: Any, score: float) -> Dict[str, Any]:
    """
    Normaliza um resultado do FAISS.

    Devolve `chunk_id`/`document_id` quando existem: é o que a §19 exige para que
    uma evidência possa ser conferida contra o conjunto realmente recuperado.
    Trechos indexados antes desta mudança não os têm, e vêm nulos — a lacuna fica
    visível em vez de ser preenchida com um id inventado.
    """
    md = doc.metadata or {}
    page_index = md.get("page")
    return {
        "page_content": doc.page_content[:EXCERPT_CHARS],
        "score": float(score),
        "chunk_id": md.get("chunk_id"),
        "document_id": md.get("document_id"),
        "source_id": md.get("document_id"),
        "content_hash": md.get("content_hash"),
        "file_name": md.get("source_name") or md.get("file_name"),
        "source_type": md.get("source_type"),
        "source_url": md.get("source_url"),
        "year": md.get("year"),
        # page do PyPDFLoader é 0-indexed; page_label é o rótulo impresso no PDF
        "page": md.get("page_number") or ((page_index + 1) if isinstance(page_index, int) else None),
        "page_label": md.get("page_label"),
        "total_pages": md.get("total_pages"),
        "scope": md.get("scope") or md.get("source"),
        "session_id": md.get("session_id"),
        "provider": md.get("provider_id") or md.get("provider"),
        "ingested_at": md.get("ingested_at"),
    }


def search(
    query: str,
    top_k: Optional[int] = None,
    session_id: Optional[str] = None,
    provider_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Busca por similaridade.

    - Sempre consulta documentos globais (source=global, de `data/pdf`).
    - Com `session_id`, também consulta os documentos daquela sessão.
    - Com `provider_id`, restringe aos documentos daquele provedor.

    O isolamento da §14.2 vale aqui: documento de uma avaliação não vaza para
    outra, porque o filtro de sessão exige o `session_id` exato.
    """
    index = index_module.load()
    if index is None:
        return []

    top_k = top_k or get_settings().max_chunks_per_query
    results: List[Dict[str, Any]] = []

    scopes: List[Dict[str, Any]] = [{"source": SCOPE_GLOBAL}]
    if session_id:
        scopes.append({"source": "session", "session_id": session_id})

    for scope_filter in scopes:
        if provider_id:
            scope_filter = {**scope_filter, "provider": provider_id}
        try:
            hits = index.similarity_search_with_score(
                query, k=top_k, filter=scope_filter, fetch_k=FETCH_K
            )
        except Exception:
            # Um escopo sem nenhum documento correspondente não é erro: segue
            # para o próximo em vez de derrubar a consulta inteira.
            continue
        results.extend(format_hit(doc, score) for doc, score in hits)

    # Ordenar por score (menor distância = mais similar) e cortar em top_k
    results.sort(key=lambda item: item["score"])
    return results[:top_k]


__all__ = ["EXCERPT_CHARS", "format_hit", "search"]
