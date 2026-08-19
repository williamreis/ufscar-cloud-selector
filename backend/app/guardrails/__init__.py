"""
Guardrails multicamada (diretriz §22–§25).

A §22.1 é explícita: guardrail não é uma biblioteca. É a combinação de código
determinístico, schema, prompt controlado, validação pós-LLM, isolamento de dados
e auditoria — e a aplicação **não deve depender de biblioteca externa** para
garantir as regras metodológicas centrais.

O que cada módulo cobre:

    files      — §23.3  extensão, MIME real, tamanho, nome e caminho
    secrets    — §23.4  credenciais em texto e documento (MASK/REJECT/WARN)
    injection  — §23.5  heurísticas de prompt injection (segunda camada)
    text       — §23.2 e §24  limites e encapsulamento como dado não confiável
    events     — §27    registro auditável, sempre com o valor mascarado
"""

from guardrails.events import (
    ACTION_ALLOW,
    ACTION_MASK,
    ACTION_REJECT,
    ACTION_WARN,
    STAGE_DOCUMENT_CONTEXT,
    STAGE_INPUT_TEXT,
    STAGE_LLM_OUTPUT,
    STAGE_UPLOAD_FILE,
    GuardrailEvent,
    GuardrailLog,
    GuardrailRejection,
)
from guardrails.files import (
    ValidatedUpload,
    enforce_document_quota,
    resolve_within,
    safe_filename,
    validate_upload,
)
from guardrails.text import (
    TAG_DOCUMENT_CONTEXT,
    TAG_USER_CONTEXT,
    enforce_length,
    format_qa_pairs,
    neutralize_tags,
    wrap_document_context,
    wrap_user_context,
)

__all__ = [
    "ACTION_ALLOW",
    "ACTION_MASK",
    "ACTION_REJECT",
    "ACTION_WARN",
    "STAGE_DOCUMENT_CONTEXT",
    "STAGE_INPUT_TEXT",
    "STAGE_LLM_OUTPUT",
    "STAGE_UPLOAD_FILE",
    "TAG_DOCUMENT_CONTEXT",
    "TAG_USER_CONTEXT",
    "GuardrailEvent",
    "GuardrailLog",
    "GuardrailRejection",
    "ValidatedUpload",
    "enforce_document_quota",
    "enforce_length",
    "format_qa_pairs",
    "neutralize_tags",
    "resolve_within",
    "safe_filename",
    "validate_upload",
    "wrap_document_context",
    "wrap_user_context",
]
