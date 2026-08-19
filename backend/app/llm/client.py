"""
Cliente de LLM: interface única, saída validada por schema e registro de execução.

A diretriz pede três coisas que aqui andam juntas:

  - **§35.1** — um adaptador só, com `structured_generate(messages, schema)`, para
    que a camada de domínio não conheça provedor;
  - **§25** — parse → Pydantic → aceitar ou rejeitar, com *um* retry controlado
    com mensagem de correção e, na segunda falha, `LLM_OUTPUT_INVALID`;
  - **§27** — cada chamada devolve provider, model, prompt_version, latência,
    tokens quando disponíveis, status e hash de entrada/saída.

Sobre a extração do JSON: a §25 proíbe "regex improvisado para *salvar* conteúdo
inválido e transformá-lo em nota". O que é feito aqui é diferente e anterior a
isso — remover cerca de markdown e recortar o objeto JSON quando o modelo o
embrulha em prosa. O conteúdo recortado ainda passa inteiro pelo Pydantic, e nada
é aproveitado se a validação falhar: não há caminho que transforme resposta
malformada em valor aceito.
"""

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol, Type, TypeVar

from pydantic import BaseModel, ValidationError

from config import Settings, get_settings
from llm.prompts import RenderedPrompt
from llm.providers import LLMUnavailable, build_chat_model

logger = logging.getLogger("uvicorn.error")

T = TypeVar("T", bound=BaseModel)

# Status de execução (§26).
STATUS_OK = "OK"
STATUS_OUTPUT_INVALID = "LLM_OUTPUT_INVALID"
STATUS_UNAVAILABLE = "LLM_UNAVAILABLE"

# Instrução de correção do retry único. Deliberadamente seca: repete o schema e
# não reabre espaço para o modelo "explicar" — a tentativa é de formato, não de
# conteúdo novo.
_CORRECTION_TEMPLATE = (
    "Sua resposta anterior não pôde ser validada: {error}\n"
    "Responda novamente APENAS com um JSON válido, sem texto fora do JSON, "
    "obedecendo exatamente a este schema:\n{schema}"
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LLMRunRecord:
    """O que fica registrado de uma chamada à LLM (§27)."""

    run_id: str
    prompt_id: str
    prompt_version: str
    provider: str
    model: str
    status: str
    latency_ms: int
    attempts: int
    input_hash: str
    output_hash: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    error: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "prompt_id": self.prompt_id,
            "prompt_version": self.prompt_version,
            "provider": self.provider,
            "model": self.model,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "attempts": self.attempts,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "error": self.error,
        }


@dataclass(frozen=True)
class StructuredResult:
    """
    Resultado de uma geração estruturada.

    `data` só vem preenchido quando a validação passou. `raw_text` acompanha para
    que o chamador possa registrar o que veio — nunca para reaproveitá-lo como se
    fosse válido.
    """

    run: LLMRunRecord
    data: Optional[BaseModel] = None
    raw_text: str = ""
    attempts_detail: tuple = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.data is not None and self.run.status == STATUS_OK


def extract_json_object(text: str) -> Optional[Any]:
    """
    Recorta o objeto JSON de uma resposta, tolerando cerca de markdown e prosa.

    Devolve None quando não há objeto balanceado — sem tentar consertar o
    conteúdo. A validação de fato é do Pydantic, no chamador.
    """
    if not text:
        return None

    cleaned = text.strip()
    if cleaned.startswith("```"):
        # ```json ... ``` — descarta a primeira linha (a cerca) e a última.
        parts = cleaned.split("```")
        if len(parts) >= 2:
            body = parts[1]
            if body.lstrip().lower().startswith("json"):
                body = body.lstrip()[4:]
            cleaned = body.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Recorta do primeiro "{" até a chave que o fecha, respeitando aninhamento e
    # strings (um "}" dentro de texto não pode encerrar o objeto).
    start = cleaned.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(cleaned)):
        char = cleaned[i]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(cleaned[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _token_usage(message: Any) -> Dict[str, Optional[int]]:
    """Tokens quando o provedor os informa; nulos quando não (§27 diz 'quando disponível')."""
    usage = getattr(message, "usage_metadata", None) or {}
    if not usage:
        metadata = getattr(message, "response_metadata", None) or {}
        usage = metadata.get("token_usage") or metadata.get("usage") or {}
    if not isinstance(usage, dict):
        return {"input_tokens": None, "output_tokens": None, "total_tokens": None}
    return {
        "input_tokens": usage.get("input_tokens") or usage.get("prompt_tokens"),
        "output_tokens": usage.get("output_tokens") or usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
    }


def _message_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    # Alguns provedores devolvem lista de blocos ({"type": "text", "text": ...}).
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content
        )
    return str(content)


class LLMClient(Protocol):
    """Contrato que a camada de domínio enxerga (§35.1)."""

    async def structured_generate(
        self, prompt: RenderedPrompt, schema: Type[T], max_attempts: int = 2
    ) -> StructuredResult: ...


class LangChainLLMClient:
    """Implementação sobre os chat models do LangChain."""

    def __init__(self, settings: Optional[Settings] = None, model: Any = None):
        self._settings = settings or get_settings()
        # `model` injetável para teste: a suíte não deve depender de rede.
        self._model = model

    @property
    def provider(self) -> str:
        return self._settings.llm_provider

    @property
    def model_name(self) -> str:
        return self._settings.llm_model

    def _chat_model(self) -> Any:
        if self._model is None:
            self._model = build_chat_model(self._settings)
        return self._model

    async def structured_generate(
        self, prompt: RenderedPrompt, schema: Type[T], max_attempts: int = 2
    ) -> StructuredResult:
        input_hash = _sha256(f"{prompt.system}\n\n{prompt.user}")
        started = time.perf_counter()
        messages = list(prompt.as_messages())
        attempts_detail = []
        raw_text = ""
        usage: Dict[str, Optional[int]] = {
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
        }

        def record(status: str, attempts: int, error: Optional[str]) -> LLMRunRecord:
            return LLMRunRecord(
                run_id=uuid.uuid4().hex,
                prompt_id=prompt.prompt_id,
                prompt_version=prompt.prompt_version,
                provider=self.provider,
                model=self.model_name,
                status=status,
                latency_ms=int((time.perf_counter() - started) * 1000),
                attempts=attempts,
                input_hash=input_hash,
                output_hash=_sha256(raw_text) if raw_text else None,
                error=error,
                **usage,
            )

        try:
            chat_model = self._chat_model()
        except LLMUnavailable as exc:
            logger.warning("LLM indisponível para %s: %s", prompt.prompt_id, exc)
            return StructuredResult(run=record(STATUS_UNAVAILABLE, 0, str(exc)))

        last_error = "resposta não validada"
        for attempt in range(1, max(1, max_attempts) + 1):
            try:
                message = await chat_model.ainvoke(messages)
            except Exception as exc:
                logger.warning("Falha na chamada à LLM (%s): %s", prompt.prompt_id, exc)
                attempts_detail.append({"attempt": attempt, "error": str(exc)})
                return StructuredResult(
                    run=record(STATUS_UNAVAILABLE, attempt, str(exc)),
                    raw_text=raw_text,
                    attempts_detail=tuple(attempts_detail),
                )

            raw_text = _message_text(message)
            usage = _token_usage(message)

            payload = extract_json_object(raw_text)
            if payload is None:
                last_error = "a resposta não contém um objeto JSON válido"
            else:
                try:
                    data = schema.model_validate(payload)
                except ValidationError as exc:
                    last_error = exc.errors(include_url=False).__str__()
                else:
                    attempts_detail.append({"attempt": attempt, "error": None})
                    return StructuredResult(
                        run=record(STATUS_OK, attempt, None),
                        data=data,
                        raw_text=raw_text,
                        attempts_detail=tuple(attempts_detail),
                    )

            attempts_detail.append({"attempt": attempt, "error": last_error})

            # Retry controlado: reapresenta o schema e pede só o formato de volta.
            if attempt < max_attempts:
                messages = list(prompt.as_messages()) + [
                    ("ai", raw_text),
                    (
                        "human",
                        _CORRECTION_TEMPLATE.format(
                            error=last_error,
                            schema=json.dumps(schema.model_json_schema(), ensure_ascii=False),
                        ),
                    ),
                ]

        logger.warning(
            "Saída da LLM inválida após %d tentativa(s) em %s: %s",
            max_attempts,
            prompt.prompt_id,
            last_error,
        )
        return StructuredResult(
            run=record(STATUS_OUTPUT_INVALID, max_attempts, last_error),
            raw_text=raw_text,
            attempts_detail=tuple(attempts_detail),
        )


def get_llm_client(settings: Optional[Settings] = None) -> LLMClient:
    return LangChainLLMClient(settings=settings)
