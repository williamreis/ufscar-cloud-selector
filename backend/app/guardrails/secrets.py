"""
Detecção de credenciais em texto e documentos (diretriz §23.4).

O gestor pode colar num campo aberto — ou dentro de um PDF enviado — uma chave de
API, um token ou uma chave privada. Isso não pode seguir para o prompt nem para o
registro de auditoria.

A ação é configurável por `GUARDRAIL_SECRET_ACTION` (MASK, REJECT ou WARN). O
default é MASK: preserva o texto útil, remove o segredo e deixa o evento gravado.

As regras são de reconhecimento de *forma*, não de validade. Um falso positivo
mascara um trecho que parecia credencial; o inverso — deixar passar — é o erro
que não dá para desfazer.
"""

import re
from dataclasses import dataclass
from typing import List, Pattern, Tuple

from config import get_settings
from guardrails.events import (
    ACTION_MASK,
    ACTION_REJECT,
    ACTION_WARN,
    GuardrailEvent,
    GuardrailLog,
    GuardrailRejection,
    STAGE_INPUT_TEXT,
)

# (rule_id, descrição, padrão). O grupo capturado, quando existe, é o que é
# substituído — permite manter o rótulo ("senha:") e apagar só o valor.
SECRET_PATTERNS: Tuple[Tuple[str, str, Pattern], ...] = (
    (
        "SECRET_PRIVATE_KEY",
        "bloco de chave privada",
        re.compile(
            r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----.*?"
            r"-----END (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----",
            re.DOTALL | re.IGNORECASE,
        ),
    ),
    ("SECRET_OPENAI_KEY", "chave da OpenAI", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("SECRET_AWS_ACCESS_KEY", "access key da AWS", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("SECRET_GOOGLE_API_KEY", "chave de API do Google", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("SECRET_SLACK_TOKEN", "token do Slack", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    (
        "SECRET_GITHUB_TOKEN",
        "token do GitHub",
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    ),
    (
        "SECRET_JWT",
        "JSON Web Token",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    ),
    (
        "SECRET_BEARER",
        "cabeçalho de autorização",
        re.compile(r"\b[Bb]earer\s+([A-Za-z0-9\-._~+/]{20,}=*)"),
    ),
    (
        "SECRET_ASSIGNMENT",
        "atribuição explícita de credencial",
        re.compile(
            r"(?i)\b(?:senha|password|passwd|secret|client[_-]?secret|api[_-]?key|"
            r"access[_-]?token|token)\b\s*[:=]\s*[\"']?([^\s\"',;]{8,})[\"']?"
        ),
    ),
)

# Substituto do valor removido. Identifica a regra, para o texto continuar legível.
MASK_TEMPLATE = "«credencial removida: {rule_id}»"


@dataclass(frozen=True)
class SecretScan:
    """Resultado da varredura: texto tratado e eventos gerados."""

    text: str
    events: Tuple[GuardrailEvent, ...]

    @property
    def found(self) -> bool:
        return bool(self.events)


def _masked_sample(value: str) -> str:
    """
    Amostra segura do que casou: só o comprimento e as bordas.

    Nunca o valor inteiro — o registro de auditoria não pode virar o lugar onde a
    credencial fica guardada em claro (§27).
    """
    stripped = value.strip()
    if len(stripped) <= 8:
        return f"<{len(stripped)} caracteres>"
    return f"{stripped[:3]}…{stripped[-2:]} (<{len(stripped)} caracteres>)"


def scan(
    text: str,
    target: str | None = None,
    stage: str = STAGE_INPUT_TEXT,
    action: str | None = None,
) -> SecretScan:
    """
    Varre `text` e aplica a ação configurada.

    Com REJECT, levanta `GuardrailRejection` na primeira ocorrência. Com MASK,
    devolve o texto já sem as credenciais. Com WARN, devolve o texto intacto e
    apenas registra — modo pensado para diagnóstico, não para produção.
    """
    action = (action or get_settings().secret_action).upper()
    events: List[GuardrailEvent] = []
    result = text or ""

    for rule_id, description, pattern in SECRET_PATTERNS:
        def _replace(match: re.Match) -> str:
            # Grupo 1 quando a regra isola o valor; senão, o casamento inteiro.
            value = match.group(1) if match.groups() else match.group(0)
            events.append(
                GuardrailEvent(
                    rule_id=rule_id,
                    stage=stage,
                    action=action if action != ACTION_REJECT else ACTION_REJECT,
                    reason=f"Detectado {description} em texto de entrada.",
                    target=target,
                    masked_sample=_masked_sample(value),
                )
            )
            if action != ACTION_MASK:
                return match.group(0)
            whole = match.group(0)
            replacement = MASK_TEMPLATE.format(rule_id=rule_id)
            return whole.replace(value, replacement) if match.groups() else replacement

        result = pattern.sub(_replace, result)

    if events and action == ACTION_REJECT:
        raise GuardrailRejection(events[0])

    return SecretScan(text=result, events=tuple(events))


def scan_into(
    text: str,
    log: GuardrailLog,
    target: str | None = None,
    stage: str = STAGE_INPUT_TEXT,
) -> str:
    """Conveniência: varre, registra no log da avaliação e devolve o texto tratado."""
    result = scan(text, target=target, stage=stage)
    log.extend(list(result.events))
    return result.text


__all__ = [
    "ACTION_MASK",
    "ACTION_REJECT",
    "ACTION_WARN",
    "MASK_TEMPLATE",
    "SECRET_PATTERNS",
    "SecretScan",
    "scan",
    "scan_into",
]
