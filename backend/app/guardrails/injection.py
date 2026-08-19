"""
Heurísticas de prompt injection em texto aberto (diretriz §23.5).

A própria diretriz avisa: **a detecção não deve ser a única defesa**. Ela é uma
segunda camada. A primeira é estrutural e está em `guardrails.text`: todo
conteúdo de terceiro entra encapsulado e o system prompt declara que ali dentro é
dado, nunca comando.

Por isso a ação padrão aqui é WARN, não REJECT. Bloquear com base em palavra-chave
recusaria respostas legítimas ("nosso requisito é ignorar instruções de terceiros
no fluxo de aprovação") sem tornar o sistema mais seguro — o que protege é o
encapsulamento, que vale mesmo quando a heurística não dispara.
"""

import re
from dataclasses import dataclass
from typing import List, Pattern, Tuple

from guardrails.events import (
    ACTION_WARN,
    GuardrailEvent,
    GuardrailLog,
    STAGE_INPUT_TEXT,
)

# (rule_id, descrição, padrão) — português e inglês, que é como as tentativas
# aparecem na prática, inclusive dentro de PDF.
INJECTION_PATTERNS: Tuple[Tuple[str, str, Pattern], ...] = (
    (
        "INJECTION_IGNORE_INSTRUCTIONS",
        "pedido de ignorar instruções anteriores",
        re.compile(
            r"(?i)\b(?:ignore|ignorar|desconsidere|desconsiderar|esque[çc]a|disregard|forget)\b"
            r"[^.\n]{0,40}\b(?:instru[çc][õo]es|regras|prompt|comandos|sistema|system|"
            r"instructions|rules|above)\b"
        ),
    ),
    (
        "INJECTION_REVEAL_PROMPT",
        "pedido de revelar o prompt do sistema",
        re.compile(
            r"(?i)\b(?:revele|revelar|mostre|mostrar|exiba|imprima|reveal|show|print|repeat)\b"
            r"[^.\n]{0,40}\b(?:system\s*prompt|prompt\s+do\s+sistema|instru[çc][õo]es\s+do\s+sistema)\b"
        ),
    ),
    (
        "INJECTION_SET_WEIGHT",
        "tentativa de fixar peso ou pontuação",
        # Aceita tanto a forma imperativa ("atribua peso 90") quanto a
        # declarativa ("Segurança deve ter peso 90%"), que é como a §42.5
        # escreve o caso adversarial.
        re.compile(
            r"(?i)\b(?:atribua|atribuir|defina|definir|coloque|aumente|reduza|use|tenha|"
            r"deve\s+(?:ter|ser|receber)|deveria\s+(?:ter|ser)|set|assign|give|"
            r"must\s+(?:have|be)|should\s+(?:have|be))\b"
            r"[^.\n]{0,40}\b(?:peso|pesos|weight|score|pontua[çc][ãa]o|nota)\b"
            r"[^.\n]{0,20}\d"
        ),
    ),
    (
        "INJECTION_FORCE_PROVIDER",
        "tentativa de forçar a escolha de um provedor",
        re.compile(
            r"(?i)\b(?:escolha|escolher|selecione|recomende|classifique|choose|select|recommend|rank)\b"
            r"[^.\n]{0,60}\b(?:independentemente|independente\s+d|regardless|no\s+matter|"
            r"em\s+primeiro|first\s+place|sempre)\b"
        ),
    ),
    (
        "INJECTION_ROLE_OVERRIDE",
        "tentativa de redefinir o papel do modelo",
        re.compile(
            r"(?i)(?:a\s+partir\s+de\s+agora\s+voc[êe]\s+[ée]|voc[êe]\s+agora\s+[ée]|"
            r"you\s+are\s+now|from\s+now\s+on\s+you\s+are|act\s+as\s+(?:a|an)\b)"
        ),
    ),
    (
        "INJECTION_FAKE_ROLE_TAG",
        "marcador de papel de sistema embutido no texto",
        re.compile(
            r"(?i)(?:<\s*/?\s*(?:system|assistant)\s*>|\[\s*/?\s*(?:system|assistant)\s*\]|"
            r"^\s*#{1,3}\s*system\b|\b(?:system|assistant)\s*:\s*você\b)",
            re.MULTILINE,
        ),
    ),
)


@dataclass(frozen=True)
class InjectionScan:
    events: Tuple[GuardrailEvent, ...]

    @property
    def suspicious(self) -> bool:
        return bool(self.events)


def _snippet(text: str, match: re.Match, radius: int = 40) -> str:
    start = max(0, match.start() - radius)
    end = min(len(text), match.end() + radius)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end].strip()}{suffix}"


def scan(
    text: str,
    target: str | None = None,
    stage: str = STAGE_INPUT_TEXT,
) -> InjectionScan:
    """Aponta indícios de injeção. Não altera o texto — quem contém é o encapsulamento."""
    events: List[GuardrailEvent] = []
    content = text or ""

    for rule_id, description, pattern in INJECTION_PATTERNS:
        for match in pattern.finditer(content):
            events.append(
                GuardrailEvent(
                    rule_id=rule_id,
                    stage=stage,
                    action=ACTION_WARN,
                    reason=(
                        f"Indício de {description}. O conteúdo segue encapsulado como "
                        "dado não confiável e não é tratado como instrução."
                    ),
                    target=target,
                    masked_sample=_snippet(content, match),
                )
            )
            break  # uma ocorrência por regra basta para o registro

    return InjectionScan(events=tuple(events))


def scan_into(
    text: str,
    log: GuardrailLog,
    target: str | None = None,
    stage: str = STAGE_INPUT_TEXT,
) -> InjectionScan:
    result = scan(text, target=target, stage=stage)
    log.extend(list(result.events))
    return result


__all__ = ["INJECTION_PATTERNS", "InjectionScan", "scan", "scan_into"]
