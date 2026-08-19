"""
Registro de eventos de guardrail (diretriz §27, bloco "Guardrails").

Cada evento guarda `rule_id`, etapa, ação, motivo e timestamp — e **nunca o
segredo puro**. Quando o gatilho foi um dado sensível, o que fica é a amostra já
mascarada; é a única forma de o registro ser auditável sem ele próprio virar um
vazamento.
"""

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Ações possíveis (§23.4).
ACTION_ALLOW = "ALLOW"
ACTION_WARN = "WARN"
ACTION_MASK = "MASK"
ACTION_REJECT = "REJECT"

# Etapas do pipeline em que um guardrail pode disparar.
STAGE_INPUT_TEXT = "input_text"
STAGE_UPLOAD_FILE = "upload_file"
STAGE_DOCUMENT_CONTEXT = "document_context"
STAGE_LLM_OUTPUT = "llm_output"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class GuardrailEvent:
    rule_id: str
    stage: str
    action: str
    reason: str
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=_now_iso)
    # Identificação do alvo (question_id, nome do arquivo, chunk_id...).
    target: Optional[str] = None
    # Amostra já mascarada do que disparou a regra. Nunca o valor original.
    masked_sample: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


class GuardrailLog:
    """
    Acumula os eventos de uma avaliação.

    Existe para que a decisão de persistir seja do chamador (uma requisição =
    um lote), em vez de cada verificação escrever no banco por conta própria.
    """

    def __init__(self) -> None:
        self._events: List[GuardrailEvent] = []

    def record(
        self,
        rule_id: str,
        stage: str,
        action: str,
        reason: str,
        target: Optional[str] = None,
        masked_sample: Optional[str] = None,
    ) -> GuardrailEvent:
        event = GuardrailEvent(
            rule_id=rule_id,
            stage=stage,
            action=action,
            reason=reason,
            target=target,
            masked_sample=masked_sample,
        )
        self._events.append(event)
        return event

    def extend(self, events: List[GuardrailEvent]) -> None:
        self._events.extend(events)

    @property
    def events(self) -> List[GuardrailEvent]:
        return list(self._events)

    def as_dicts(self) -> List[Dict[str, Any]]:
        return [e.as_dict() for e in self._events]

    def has_action(self, action: str) -> bool:
        return any(e.action == action for e in self._events)

    def __len__(self) -> int:
        return len(self._events)


class GuardrailRejection(ValueError):
    """
    Entrada recusada por um guardrail.

    Carrega o evento para que a camada de API possa devolver o motivo e ainda
    assim registrar o que aconteceu.
    """

    def __init__(self, event: GuardrailEvent):
        super().__init__(event.reason)
        self.event = event
