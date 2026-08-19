"""
Ingestão documental (diretriz §14.1).

    Documento → validação → extração textual → chunking → embeddings → FAISS →
    metadados persistidos

Duas diferenças em relação à ingestão anterior:

  - **cada chunk sai identificado.** `chunk_id`, `document_id` e `content_hash`
    passam a existir, que é o que torna possível a validação de proveniência da
    §19 e a citação estável exigida pela §15.

  - **o arquivo é validado antes de ser lido.** A checagem de assinatura real
    (`guardrails.files`) roda sobre os bytes; antes bastava a extensão estar
    certa, e um binário renomeado para `.pdf` chegava ao loader.
"""

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from config import get_settings
from guardrails.events import GuardrailLog, GuardrailRejection
from guardrails.files import validate_upload
from llm.embeddings import get_embedding_function
from rag import index as index_module
from rag.metadata import SCOPE_GLOBAL, build_chunk_metadata, build_document_metadata

logger = logging.getLogger("uvicorn.error")


def detect_provider_id(file_name: str) -> Optional[str]:
    """
    Descobre a qual provedor um documento pertence pelo nome do arquivo, usando os
    doc_keywords de providers_data. Retorna None quando nenhum termo casa — nesse
    caso o documento é indexado, mas não é atribuído a nenhum provedor (e portanto
    não vira evidência de ninguém), o que é preferível a atribuí-lo ao errado.
    """
    from providers_data import PROVIDERS  # import local evita ciclo de importação

    name = (file_name or "").lower()
    for p in PROVIDERS:
        for kw in p.get("doc_keywords", []):
            if kw in name:
                return p["id"]
    return None


def _load_document(path: str) -> List[Any]:
    """Extração textual conforme o formato. Devolve documentos do LangChain."""
    from langchain_community.document_loaders import PyPDFLoader, TextLoader

    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return PyPDFLoader(path).load()
    if ext in (".txt", ".text"):
        return TextLoader(path, encoding="utf-8").load()
    raise ValueError(f"Formato não suportado: {ext or '(nenhum)'}")


def load_and_chunk(
    file_paths: List[str],
    scope: str,
    session_id: Optional[str] = None,
    source_type: Optional[str] = None,
    guardrail_log: Optional[GuardrailLog] = None,
) -> Tuple[List[Any], List[Dict[str, Any]], List[str]]:
    """
    Lê, valida, fatia e anota os arquivos.

    Devolve (chunks, documentos processados, erros). Um arquivo rejeitado por
    guardrail entra em `erros` com o motivo e o processo segue com os demais —
    uma ingestão em lote não deve parar inteira por causa de um arquivo ruim.
    """
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    settings = get_settings()
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap
    )

    chunks_total: List[Any] = []
    processed: List[Dict[str, Any]] = []
    errors: List[str] = []

    for path in file_paths:
        file_name = os.path.basename(path)
        if not os.path.isfile(path):
            errors.append(f"Arquivo não encontrado: {path}")
            continue

        try:
            with open(path, "rb") as handle:
                content = handle.read()
        except OSError as exc:
            errors.append(f"{file_name}: falha ao ler o arquivo ({exc}).")
            continue

        # Revalida no momento da ingestão, e não só no upload: os documentos
        # globais chegam por `data/pdf`, copiados à mão, sem passar pelo upload.
        try:
            validate_upload(file_name, content, guardrail_log)
        except GuardrailRejection as exc:
            errors.append(f"{file_name}: {exc.event.reason}")
            continue

        try:
            documents = _load_document(path)
        except Exception as exc:
            # DOCUMENT_PARSE_ERROR da §26: fica registrado, não vira chunk vazio.
            errors.append(f"{file_name}: falha na extração textual ({exc}).")
            continue

        provider_id = detect_provider_id(file_name)
        document_metadata = build_document_metadata(
            file_name=file_name,
            content=content,
            scope=scope,
            provider_id=provider_id,
            source_type=source_type,
            session_id=session_id,
        )

        pieces = splitter.split_documents(documents)
        for position, piece in enumerate(pieces):
            loader_metadata = piece.metadata or {}
            piece.metadata = build_chunk_metadata(
                document_metadata,
                index=position,
                text=piece.page_content,
                page=loader_metadata.get("page"),
                page_label=loader_metadata.get("page_label"),
            )
            # `provider` (chave antiga) permanece para que os filtros de consulta
            # continuem encontrando tanto o que já estava indexado quanto o novo.
            if provider_id:
                piece.metadata["provider"] = provider_id
            piece.metadata["total_pages"] = loader_metadata.get("total_pages")

        chunks_total.extend(pieces)
        processed.append(
            {
                "file": path,
                "file_name": file_name,
                "document_id": document_metadata["document_id"],
                "chunks": len(pieces),
                "provider": provider_id,
                "year": document_metadata["year"],
                "scope": scope,
            }
        )

    return chunks_total, processed, errors


def ingest_paths(
    file_paths: List[str],
    scope: str = SCOPE_GLOBAL,
    session_id: Optional[str] = None,
    source_type: Optional[str] = None,
    guardrail_log: Optional[GuardrailLog] = None,
) -> Dict[str, Any]:
    """Pipeline completo da §14.1 para um conjunto de caminhos."""
    from langchain_community.vectorstores import FAISS

    chunks, processed, errors = load_and_chunk(
        file_paths,
        scope=scope,
        session_id=session_id,
        source_type=source_type,
        guardrail_log=guardrail_log,
    )

    result: Dict[str, Any] = {
        "chunks": len(chunks),
        "files_processed": len(processed),
        "files_failed": len(errors),
        "details": processed,
        # Arquivos sem provedor identificável no nome: são indexados, mas não
        # aparecem como evidência de nenhum provedor. Sinalizado para o admin corrigir.
        "unassigned_files": [d["file_name"] for d in processed if not d["provider"]],
        "documents": [
            {"document_id": d["document_id"], "file_name": d["file_name"], "year": d["year"]}
            for d in processed
        ],
        "errors": errors,
    }

    if not chunks:
        return result

    embeddings = get_embedding_function()
    # use_cache=False: mutamos o índice aqui (add_documents), então trabalhamos
    # sobre uma instância própria em vez do objeto compartilhado com as consultas.
    existing = index_module.load(use_cache=False)
    if existing is None:
        existing = FAISS.from_documents(chunks, embeddings)
    else:
        existing.add_documents(chunks)

    index_module.save(existing)
    return result


__all__ = ["detect_provider_id", "ingest_paths", "load_and_chunk"]
