"""
Metadados de documento e de chunk (diretriz §14.3 e §11 do documento anterior).

O que muda em relação ao que era gravado antes: cada trecho passa a ter
**identidade própria e estável** (`chunk_id`), ligação com o documento
(`document_id`) e hash do conteúdo. Sem `chunk_id` não existe a validação de
proveniência da §19 — não há como exigir que a evidência devolvida pela LLM
aponte para um trecho que de fato foi entregue a ela, porque o trecho não tinha
nome.

Os identificadores são **determinísticos**, derivados do conteúdo: reingerir o
mesmo arquivo produz os mesmos ids. Isso permite detectar reingestão e faz com
que uma evidência gravada continue apontando para o mesmo trecho depois de o
índice ser reconstruído.

Campo sem informação disponível fica `None`, nunca preenchido por suposição — é o
que a §14.3 determina em "quando a informação não existir, usar null".
"""

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# Escopos (§14.2). Documento oficial da base × documento anexado numa avaliação.
SCOPE_GLOBAL = "global"


def evaluation_scope(evaluation_id: str) -> str:
    return f"evaluation:{evaluation_id}"


# Ano plausível de publicação de um relatório institucional. A faixa existe para
# não capturar "ISO 27001" nem número de versão como se fosse data.
_YEAR_RE = re.compile(r"(?<!\d)(20[0-3]\d)(?!\d)")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def document_id_for(content: bytes) -> str:
    """Identidade do documento = hash do seu conteúdo."""
    return sha256_bytes(content)[:32]


def chunk_id_for(document_id: str, index: int, text: str) -> str:
    """
    Identidade do trecho.

    Inclui o índice além do texto porque dois trechos idênticos podem coexistir
    num mesmo documento (cabeçalho repetido, tabela replicada), e eles precisam
    ser distinguíveis na hora de citar a fonte.
    """
    return sha256_text(f"{document_id}:{index}:{text}")[:32]


def year_from_name(file_name: str) -> Optional[int]:
    """
    Ano declarado no nome do arquivo, quando houver.

    É leitura, não inferência: `2025-azure-...pdf` carrega o ano no próprio nome.
    Sem correspondência, devolve None — o alerta de atualidade documental (TODO
    ACADÊMICO 04) prefere não ter data a ter uma data adivinhada.
    """
    match = _YEAR_RE.search(file_name or "")
    return int(match.group(1)) if match else None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_document_metadata(
    file_name: str,
    content: bytes,
    scope: str,
    provider_id: Optional[str] = None,
    source_type: Optional[str] = None,
    source_url: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Metadados no nível do documento, comuns a todos os seus chunks."""
    return {
        "document_id": document_id_for(content),
        "source_name": file_name,
        # Tipo do documento (relatório de sustentabilidade, SLA, certificação...).
        # Não é derivável do arquivo com confiança, então fica nulo até que a
        # ingestão o receba explicitamente.
        "source_type": source_type,
        "source_url": source_url,
        "provider_id": provider_id,
        "year": year_from_name(file_name),
        "scope": scope,
        "ingested_at": now_iso(),
        "document_hash": sha256_bytes(content),
        # Mantidos para compatibilidade com o índice FAISS já persistido, cujos
        # filtros de consulta usam estas duas chaves.
        "source": SCOPE_GLOBAL if scope == SCOPE_GLOBAL else "session",
        "session_id": session_id,
        "file_name": file_name,
    }


def build_chunk_metadata(
    document_metadata: Dict[str, Any],
    index: int,
    text: str,
    page: Optional[int] = None,
    page_label: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Metadados de um chunk: os do documento mais a identidade do trecho (§14.3).

    `page` é gravado **exatamente como o loader entrega** — no PyPDFLoader isso
    significa base 0. O campo derivado `page_number` traz a numeração humana. Os
    dois convivem porque o índice FAISS já persistido guarda a forma base 0, e
    reinterpretá-la aqui deslocaria em uma página toda citação de documento
    ingerido antes desta mudança.
    """
    document_id = str(document_metadata.get("document_id"))
    metadata = dict(document_metadata)
    metadata.update(
        {
            "chunk_id": chunk_id_for(document_id, index, text),
            "chunk_index": index,
            "content_hash": sha256_text(text),
            "page": page,
            "page_number": (page + 1) if isinstance(page, int) else None,
            "page_label": page_label,
        }
    )
    return metadata


__all__ = [
    "SCOPE_GLOBAL",
    "build_chunk_metadata",
    "build_document_metadata",
    "chunk_id_for",
    "document_id_for",
    "evaluation_scope",
    "now_iso",
    "sha256_bytes",
    "sha256_text",
    "year_from_name",
]
