"""
Justificativa textual das preferências declaradas.

Camada fina entre o questionário e a LLM. Existe para que `main.py` não precise
conhecer prompt, schema nem cliente — e para concentrar o tratamento do texto do
gestor antes de ele chegar ao modelo:

    limite de tamanho → varredura de credenciais → heurística de injeção →
    encapsulamento em <USER_CONTEXT> → prompt versionado → saída validada

A ordem importa. A varredura de credenciais roda **antes** do encapsulamento
porque o que ela remove não pode chegar ao prompt; a heurística de injeção roda
depois dela e só registra, porque quem contém a injeção é o encapsulamento, não
a detecção (§23.5).
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from guardrails import GuardrailLog, enforce_length, format_qa_pairs, wrap_user_context
from guardrails import injection, secrets
from guardrails.events import STAGE_INPUT_TEXT
from llm import STATUS_OK, get_llm_client
from llm.client import LLMRunRecord
from llm.prompts import get as get_prompt
from llm.schemas import PreferenceNotes

logger = logging.getLogger("uvicorn.error")

PROMPT_ID = "PROMPT_PREFERENCE_NOTES_V1"


def sanitize_qa_pairs(
    qa_pairs: List[Dict[str, str]],
    log: GuardrailLog,
) -> List[Dict[str, str]]:
    """
    Aplica os guardrails de entrada a cada resposta do gestor.

    Levanta `GuardrailRejection` quando uma resposta estoura o limite de tamanho
    ou quando a política de credenciais está em REJECT — nos dois casos a
    requisição para com motivo explícito, em vez de seguir com texto adulterado
    em silêncio.
    """
    clean: List[Dict[str, str]] = []
    for pair in qa_pairs:
        question = str(pair.get("pergunta", ""))
        answer = str(pair.get("resposta", ""))

        answer = enforce_length(answer, field_name=question[:80], log=log)
        answer = secrets.scan_into(answer, log, target=question[:80], stage=STAGE_INPUT_TEXT)
        injection.scan_into(answer, log, target=question[:80], stage=STAGE_INPUT_TEXT)

        clean.append({"pergunta": question, "resposta": answer})
    return clean


async def explain_preferences(
    qa_pairs: List[Dict[str, str]],
    relevance: Dict[str, float],
    criteria_weights: Dict[str, float],
    guardrail_log: Optional[GuardrailLog] = None,
) -> Tuple[str, LLMRunRecord]:
    """
    Pede à LLM apenas a justificativa textual.

    Os pesos saem do AHP e não passam pelo modelo — o cálculo continua
    determinístico, reprodutível e auditável (§2.1).

    Devolve `(texto, registro da execução)`. Quando a saída não valida, o texto
    volta vazio e o motivo fica no registro: a alternativa seria aproveitar a
    resposta malformada, que é exatamente o que a §25 proíbe.
    """
    log = guardrail_log if guardrail_log is not None else GuardrailLog()
    safe_pairs = sanitize_qa_pairs(qa_pairs, log)

    prompt = get_prompt(PROMPT_ID).render(
        criteria_weights=str({k: round(float(v), 4) for k, v in criteria_weights.items()}),
        relevance=str({k: round(float(v), 2) for k, v in relevance.items()}),
        qa_pairs=wrap_user_context(format_qa_pairs(safe_pairs)),
    )

    result = await get_llm_client().structured_generate(prompt, PreferenceNotes)

    if result.ok and isinstance(result.data, PreferenceNotes):
        return result.data.notes, result.run

    logger.warning(
        "Justificativa não gerada (%s): %s", result.run.status, result.run.error
    )
    return "", result.run


__all__ = ["PROMPT_ID", "explain_preferences", "sanitize_qa_pairs"]
