"""
Registro de prompts versionados (diretriz §28.2).

Todo prompt tem identificador e versão, e o par (`prompt_id`, `prompt_version`)
é gravado em cada execução da LLM. Sem isso não há como saber, meses depois, com
que instrução um resultado antigo foi produzido.

**Interpolação por `{{variável}}`, não por `str.format`.** A substituição é
literal, sobre marcadores de chave dupla. É uma escolha de segurança: parte do
que entra no prompt é texto do gestor ou trecho de documento, e um `{` solto
nesse conteúdo quebraria um `format()` — ou, pior, faria o conteúdo ser lido como
marcador. Aqui o conteúdo nunca é reinterpretado como template.
"""

import re
from dataclasses import dataclass
from typing import Dict, Tuple

# Marcador aceito no corpo do template: {{nome_da_variavel}}
_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


class PromptRenderError(ValueError):
    """Variável obrigatória ausente ou desconhecida na renderização do prompt."""


@dataclass(frozen=True)
class RenderedPrompt:
    """Prompt pronto para a chamada, com a identificação que vai para a auditoria."""

    prompt_id: str
    prompt_version: str
    system: str
    user: str

    def as_messages(self) -> list:
        return [("system", self.system), ("human", self.user)]


@dataclass(frozen=True)
class Prompt:
    """Definição versionada de um prompt."""

    id: str
    version: str
    system: str
    user_template: str

    @property
    def variables(self) -> Tuple[str, ...]:
        return tuple(sorted(set(_PLACEHOLDER_RE.findall(self.user_template))))

    def render(self, **values: str) -> RenderedPrompt:
        expected = set(self.variables)
        received = set(values)

        missing = expected - received
        if missing:
            raise PromptRenderError(
                f"{self.id}: variáveis ausentes na renderização: {', '.join(sorted(missing))}."
            )
        unknown = received - expected
        if unknown:
            raise PromptRenderError(
                f"{self.id}: variáveis desconhecidas: {', '.join(sorted(unknown))}."
            )

        user = self.user_template
        for name, value in values.items():
            user = user.replace(f"{{{{{name}}}}}", str(value))

        return RenderedPrompt(
            prompt_id=self.id,
            prompt_version=self.version,
            system=self.system,
            user=user,
        )


_REGISTRY: Dict[str, Prompt] = {}


def register(prompt: Prompt) -> Prompt:
    if prompt.id in _REGISTRY:
        raise ValueError(f"Prompt duplicado no registro: {prompt.id}.")
    _REGISTRY[prompt.id] = prompt
    return prompt


def get(prompt_id: str) -> Prompt:
    try:
        return _REGISTRY[prompt_id]
    except KeyError:
        raise KeyError(f"Prompt não registrado: {prompt_id}.") from None


def registered_versions() -> Dict[str, str]:
    """Mapa `prompt_id → versão` de tudo que está registrado, para o bloco de auditoria."""
    return {pid: p.version for pid, p in sorted(_REGISTRY.items())}


# O import abaixo popula o registro; fica no fim para não haver ciclo com `register`.
from llm.prompts import preference_notes  # noqa: E402,F401

__all__ = [
    "Prompt",
    "PromptRenderError",
    "RenderedPrompt",
    "get",
    "register",
    "registered_versions",
]
