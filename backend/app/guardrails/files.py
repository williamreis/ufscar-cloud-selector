"""
Validação de arquivos enviados (diretriz §23.3).

A checagem anterior olhava só a extensão, o que é o mesmo que não checar: renomear
`payload.exe` para `payload.pdf` passava. Aqui a extensão é apenas o primeiro
filtro; o que decide é a **assinatura real do conteúdo**.

Cobre os seis itens da §23.3: extensão permitida, MIME real, tamanho, capacidade
de extração textual, nome seguro e caminho controlado.

A detecção é por assinatura própria, sem `python-magic`, para não introduzir
dependência de biblioteca de sistema (`libmagic`) num container que hoje sobe sem
ela — o conjunto de formatos aceitos é pequeno e fechado (`.pdf`, `.txt`).
"""

import re
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from config import get_settings
from guardrails.events import (
    ACTION_REJECT,
    GuardrailEvent,
    GuardrailLog,
    GuardrailRejection,
    STAGE_UPLOAD_FILE,
)

# Assinaturas de binário executável ou empacotado. Nenhuma delas pode chegar ao
# disco, mesmo que a extensão diga outra coisa.
_FORBIDDEN_SIGNATURES: Tuple[Tuple[bytes, str], ...] = (
    (b"MZ", "executável DOS/Windows (PE)"),
    (b"\x7fELF", "executável ELF (Linux)"),
    (b"\xcf\xfa\xed\xfe", "executável Mach-O (macOS)"),
    (b"\xfe\xed\xfa\xce", "executável Mach-O (macOS)"),
    (b"\xca\xfe\xba\xbe", "bytecode Java / binário universal"),
    (b"PK\x03\x04", "arquivo compactado ZIP"),
    (b"\x1f\x8b", "arquivo compactado GZIP"),
    (b"Rar!\x1a\x07", "arquivo compactado RAR"),
    (b"\xfd7zXZ", "arquivo compactado XZ"),
)

_PDF_SIGNATURE = b"%PDF-"

# Nome de arquivo seguro: só o que é inequívoco em qualquer sistema de arquivos.
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_NAME_LENGTH = 120


@dataclass(frozen=True)
class ValidatedUpload:
    """Arquivo aprovado, com o nome já saneado para gravação."""

    original_name: str
    stored_name: str
    extension: str
    size: int
    detected_type: str


def safe_filename(original: str) -> str:
    """
    Nome de gravação: prefixo aleatório + nome saneado.

    O prefixo evita colisão e impede que o nome escolhido pelo remetente decida o
    caminho final; o saneamento remove acento, espaço, separador de diretório e
    qualquer coisa que possa ser interpretada pelo sistema de arquivos.
    """
    base = Path(original or "").name
    normalized = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode("ascii")
    cleaned = _UNSAFE_CHARS.sub("_", normalized).strip("._-")
    if not cleaned:
        cleaned = "documento"
    if len(cleaned) > _MAX_NAME_LENGTH:
        stem, _, suffix = cleaned.rpartition(".")
        cleaned = f"{(stem or cleaned)[: _MAX_NAME_LENGTH - len(suffix) - 1]}.{suffix}" if suffix else cleaned[:_MAX_NAME_LENGTH]
    return f"{uuid.uuid4().hex}_{cleaned}"


def _reject(
    rule_id: str, reason: str, target: str, log: Optional[GuardrailLog]
) -> GuardrailRejection:
    event = GuardrailEvent(
        rule_id=rule_id,
        stage=STAGE_UPLOAD_FILE,
        action=ACTION_REJECT,
        reason=reason,
        target=target,
    )
    if log is not None:
        log.extend([event])
    return GuardrailRejection(event)


def _detect_type(content: bytes) -> Optional[str]:
    """
    Tipo real pelo conteúdo: 'pdf', 'text' ou None quando não é nenhum dos dois.

    Para texto não há assinatura — a evidência é negativa: decodifica em UTF-8 (ou
    Latin-1) e não contém byte nulo, que é o que separa texto de binário na
    prática.
    """
    if content[:1024].find(_PDF_SIGNATURE) != -1:
        return "pdf"

    if b"\x00" in content[:8192]:
        return None

    sample = content[:8192]
    for encoding in ("utf-8", "latin-1"):
        try:
            sample.decode(encoding)
        except UnicodeDecodeError:
            continue
        return "text"
    return None


def validate_upload(
    filename: str,
    content: bytes,
    log: Optional[GuardrailLog] = None,
) -> ValidatedUpload:
    """
    Valida um arquivo recebido. Levanta `GuardrailRejection` no primeiro problema.

    A ordem das checagens é a que expõe menos: tamanho antes de olhar o conteúdo,
    assinatura proibida antes de tentar interpretar o formato.
    """
    settings = get_settings()
    target = Path(filename or "").name or "(sem nome)"

    extension = Path(filename or "").suffix.lower()
    if extension not in settings.allowed_upload_extensions:
        raise _reject(
            "FILE_EXTENSION_NOT_ALLOWED",
            (
                f"Extensão não permitida: {extension or '(nenhuma)'}. "
                f"Permitidas: {', '.join(settings.allowed_upload_extensions)}."
            ),
            target,
            log,
        )

    size = len(content)
    if size == 0:
        raise _reject("FILE_EMPTY", "Arquivo vazio.", target, log)
    if size > settings.max_upload_bytes:
        raise _reject(
            "FILE_TOO_LARGE",
            (
                f"Arquivo de {size / (1024 * 1024):.1f} MB excede o limite de "
                f"{settings.max_upload_mb} MB (MAX_UPLOAD_MB)."
            ),
            target,
            log,
        )

    for signature, description in _FORBIDDEN_SIGNATURES:
        if content.startswith(signature):
            raise _reject(
                "FILE_EXECUTABLE_CONTENT",
                f"Conteúdo identificado como {description}, incompatível com documento textual.",
                target,
                log,
            )

    if content.lstrip()[:2] == b"#!":
        raise _reject(
            "FILE_EXECUTABLE_CONTENT",
            "Conteúdo começa com shebang (#!), característico de script executável.",
            target,
            log,
        )

    detected = _detect_type(content)
    if detected is None:
        raise _reject(
            "FILE_TYPE_UNRECOGNIZED",
            "Não foi possível reconhecer o conteúdo como PDF nem como texto extraível.",
            target,
            log,
        )

    expected = {".pdf": "pdf", ".txt": "text", ".text": "text"}.get(extension)
    if expected and detected != expected:
        raise _reject(
            "FILE_MIME_MISMATCH",
            (
                f"O conteúdo real ({detected}) não corresponde à extensão {extension}. "
                "Renomear um arquivo não muda o que ele é."
            ),
            target,
            log,
        )

    return ValidatedUpload(
        original_name=target,
        stored_name=safe_filename(filename),
        extension=extension,
        size=size,
        detected_type=detected,
    )


def enforce_document_quota(
    current_count: int,
    incoming: int,
    target: str,
    log: Optional[GuardrailLog] = None,
) -> None:
    """Aplica `MAX_DOCUMENTS_PER_EVALUATION` (§23.2)."""
    limit = get_settings().max_documents_per_evaluation
    total = current_count + incoming
    if total > limit:
        raise _reject(
            "DOCUMENT_QUOTA_EXCEEDED",
            (
                f"A sessão ficaria com {total} documentos e o limite é {limit} "
                f"(MAX_DOCUMENTS_PER_EVALUATION)."
            ),
            target,
            log,
        )


def resolve_within(base_dir: Path, name: str) -> Path:
    """
    Resolve `name` dentro de `base_dir`, recusando qualquer escape.

    Aceita apenas o nome final do arquivo e confere o caminho já resolvido —
    bloqueia tanto `../` quanto caminho absoluto quanto symlink que aponte para
    fora do diretório permitido.
    """
    base = base_dir.resolve()
    candidate = (base / Path(name).name).resolve()
    if base != candidate.parent:
        raise _reject(
            "PATH_TRAVERSAL_BLOCKED",
            "Caminho de arquivo fora do diretório permitido.",
            str(name),
            None,
        )
    return candidate


__all__ = [
    "ValidatedUpload",
    "enforce_document_quota",
    "resolve_within",
    "safe_filename",
    "validate_upload",
]
