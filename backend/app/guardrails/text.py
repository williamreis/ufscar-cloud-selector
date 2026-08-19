"""
Limites e encapsulamento de texto (diretriz §23.2 e §24).

Esta é a defesa **estrutural** contra injeção, e a que vale mesmo quando as
heurísticas de `guardrails.injection` não disparam: nenhum conteúdo de terceiro é
concatenado como instrução. Ele entra dentro de uma marcação declarada no system
prompt como dado não confiável.

O detalhe que faz a marcação valer alguma coisa é a neutralização da própria
marcação dentro do conteúdo. Sem isso, bastaria o texto conter
`</USER_CONTEXT>` para "sair" do bloco e o resto ser lido como instrução do
sistema — a marcação viraria decoração.
"""

import re
from typing import Any, Dict, Iterable, List, Optional

from config import get_settings
from guardrails.events import (
    ACTION_REJECT,
    GuardrailEvent,
    GuardrailLog,
    GuardrailRejection,
    STAGE_DOCUMENT_CONTEXT,
    STAGE_INPUT_TEXT,
)

TAG_USER_CONTEXT = "USER_CONTEXT"
TAG_DOCUMENT_CONTEXT = "DOCUMENT_CONTEXT"

# Como uma marcação encontrada dentro do conteúdo é neutralizada. Preserva a
# leitura ("o gestor escreveu isso") sem preservar a função de delimitador.
_NEUTRALIZED = "(marcação removida: {tag})"


def neutralize_tags(text: str, *tags: str) -> str:
    """Desarma qualquer ocorrência das marcações de contexto dentro do conteúdo."""
    result = text or ""
    for tag in tags:
        result = re.sub(
            rf"<\s*/?\s*{re.escape(tag)}\b[^>]*>",
            _NEUTRALIZED.format(tag=tag),
            result,
            flags=re.IGNORECASE,
        )
    return result


def enforce_length(
    text: str,
    field_name: str,
    limit: Optional[int] = None,
    log: Optional[GuardrailLog] = None,
) -> str:
    """
    Aplica `MAX_OPEN_TEXT_CHARS`.

    Recusa em vez de truncar: cortar em silêncio faria a avaliação rodar sobre
    metade do que o gestor escreveu, sem que ninguém ficasse sabendo.
    """
    limit = limit if limit is not None else get_settings().max_open_text_chars
    content = text or ""
    if len(content) <= limit:
        return content

    event = GuardrailEvent(
        rule_id="INPUT_TEXT_TOO_LONG",
        stage=STAGE_INPUT_TEXT,
        action=ACTION_REJECT,
        reason=(
            f"Texto de {len(content)} caracteres excede o limite de {limit} "
            f"configurado em MAX_OPEN_TEXT_CHARS."
        ),
        target=field_name,
    )
    if log is not None:
        log.extend([event])
    raise GuardrailRejection(event)


def wrap_user_context(text: str) -> str:
    """Encapsula texto do gestor como dado não confiável."""
    safe = neutralize_tags(text or "", TAG_USER_CONTEXT, TAG_DOCUMENT_CONTEXT)
    return f"<{TAG_USER_CONTEXT}>\n{safe}\n</{TAG_USER_CONTEXT}>"


def format_qa_pairs(pairs: Iterable[Dict[str, str]]) -> str:
    """
    Formata os pares pergunta/resposta que vão para dentro de `<USER_CONTEXT>`.

    O enunciado vem do `questions.json` e a resposta vem do gestor; ambos são
    neutralizados, porque o `questions.json` é um volume editável e portanto
    também não é uma fonte de instrução confiável.
    """
    lines: List[str] = []
    for pair in pairs:
        question = neutralize_tags(str(pair.get("pergunta", "")), TAG_USER_CONTEXT, TAG_DOCUMENT_CONTEXT)
        answer = neutralize_tags(str(pair.get("resposta", "")), TAG_USER_CONTEXT, TAG_DOCUMENT_CONTEXT)
        lines.append(f"- {question}\n  → {answer}")
    return "\n".join(lines)


def wrap_document_context(
    chunks: Iterable[Dict[str, Any]],
    log: Optional[GuardrailLog] = None,
) -> str:
    """
    Encapsula trechos recuperados conforme a §24.

    Cada trecho carrega `source_id` e `chunk_id` na própria marcação: é o que
    permite, depois, exigir que toda evidência devolvida pela LLM aponte para um
    identificador que de fato foi fornecido (§19).
    """
    blocks: List[str] = []
    for chunk in chunks:
        source_id = str(chunk.get("source_id") or chunk.get("document_id") or "desconhecido")
        chunk_id = str(chunk.get("chunk_id") or "desconhecido")
        raw = str(chunk.get("page_content") or chunk.get("text") or "")
        safe = neutralize_tags(raw, TAG_DOCUMENT_CONTEXT, TAG_USER_CONTEXT)
        if safe != raw and log is not None:
            log.record(
                rule_id="DOCUMENT_CONTEXT_TAG_NEUTRALIZED",
                stage=STAGE_DOCUMENT_CONTEXT,
                action="MASK",
                reason="Trecho do documento continha marcação de contexto; delimitador neutralizado.",
                target=chunk_id,
            )
        blocks.append(
            f'<{TAG_DOCUMENT_CONTEXT} source_id="{source_id}" chunk_id="{chunk_id}">\n'
            f"{safe}\n"
            f"</{TAG_DOCUMENT_CONTEXT}>"
        )
    return "\n\n".join(blocks)


__all__ = [
    "TAG_DOCUMENT_CONTEXT",
    "TAG_USER_CONTEXT",
    "enforce_length",
    "format_qa_pairs",
    "neutralize_tags",
    "wrap_document_context",
    "wrap_user_context",
]
