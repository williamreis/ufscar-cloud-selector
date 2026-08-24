"""
Cliente de LLM: interface única, saída validada por schema e registro de execução.

A diretriz pede três coisas que aqui andam juntas:

  - **§35.1** — um adaptador só, com `structured_generate(messages, schema)`, para
    que a camada de domínio não conheça provedor;
  - **§25** — parse → Pydantic → aceitar ou rejeitar, com *um* retry controlado
    com mensagem de correção e, na segunda falha, `LLM_OUTPUT_INVALID`;
  - **§27** — cada chamada devolve provider, model, prompt_version, latência,
    tokens quando disponíveis, status e hash de entrada/saída.

Um caso de erro é tratado à parte dos demais: o **429 (rate limit)**. As camadas
gratuitas impõem teto de tokens por minuto e respondem "tente de novo em 31s" —
isso é espera, não indisponibilidade, e transformá-lo direto em `LLM_UNAVAILABLE`
perdia a extração inteira de uma dimensão por um limite que passa sozinho. Aqui a
chamada aguarda o tempo que o provedor pede e repete; só depois de esgotar as
esperas configuradas (ou diante de uma espera maior que o teto) é que o status
vira `LLM_UNAVAILABLE`. A espera não vale como tentativa da §25: o retry de lá é
de *formato* — este é de *transporte*, e não consome a chance de correção.

Quando há **cadeia de provedores** configurada (§35.2: `LLM_FALLBACK_PROVIDERS`,
por padrão o OpenRouter atrás do Groq), o desenho muda de ênfase: com outro
provedor ocioso, esperar 30 segundos pelo primário é trocar disponibilidade por
nada. Então o primário não espera — passa a vez. Só o último elo da cadeia, que
não tem para quem passar, usa o orçamento de esperas. O que motiva a troca é o
provedor **não poder atender agora**: 429, saldo/cota esgotados, indisponibilidade
ou má configuração. Chave recusada não entra nessa lista: é erro de instalação e
precisa aparecer, não ser contornado em silêncio.

A troca fica registrada. `LLMRunRecord.provider`/`model` dizem quem de fato
respondeu, e `fallback_from` diz de quem a vez era — sem isso o relatório
afirmaria que uma evidência veio de um modelo que nunca a viu.

Sobre a extração do JSON: a §25 proíbe "regex improvisado para *salvar* conteúdo
inválido e transformá-lo em nota". O que é feito aqui é diferente e anterior a
isso — remover cerca de markdown e recortar o objeto JSON quando o modelo o
embrulha em prosa. O conteúdo recortado ainda passa inteiro pelo Pydantic, e nada
é aproveitado se a validação falhar: não há caminho que transforme resposta
malformada em valor aceito.
"""

import asyncio
import hashlib
import json
import logging
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol, Tuple, Type, TypeVar

from pydantic import BaseModel, ValidationError

from config import ProviderProfile, Settings, get_settings
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


# Groq, OpenAI e OpenRouter dizem no corpo do erro quanto falta esperar
# ("Please try again in 31.567499999s", "try again in 1m2.5s"). Ler esse número é
# melhor que adivinhar: o backoff cego ou espera demais ou volta cedo e queima
# outra requisição do minuto.
_RETRY_AFTER_PATTERN = re.compile(
    r"try again in\s+(?:(?P<min>[\d.]+)m)?(?P<sec>[\d.]+)s", re.IGNORECASE
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_rate_limit(exc: Exception) -> bool:
    """Distingue 'espere' de 'não dá'. Só o primeiro merece nova tentativa."""
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    if status == 429:
        return True
    # Sem status no objeto, sobra o texto. O casamento é por expressão inteira e
    # não por "429" solto: a mensagem do Groq carrega contagens de token, e um
    # "Used 4290" não pode ser lido como limite de taxa.
    texto = str(exc).lower()
    return any(
        marca in texto
        for marca in ("rate_limit", "rate limit", "too many requests", "code: 429", "status 429")
    )


def _suggested_wait(exc: Exception) -> Optional[float]:
    """Tempo de espera que o provedor informou, em segundos, se informou."""
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    for chave in ("retry-after", "Retry-After", "x-ratelimit-reset-tokens"):
        bruto = headers.get(chave) if hasattr(headers, "get") else None
        if bruto:
            try:
                return float(str(bruto).rstrip("s"))
            except ValueError:
                pass

    match = _RETRY_AFTER_PATTERN.search(str(exc))
    if match:
        minutos = float(match.group("min") or 0)
        return minutos * 60 + float(match.group("sec"))
    return None


def _sem_saldo(exc: Exception) -> bool:
    """Saldo, cota ou faturamento — o provedor está de pé, mas não vai atender."""
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if status == 402:
        return True
    texto = str(exc).lower()
    return any(
        marca in texto
        for marca in (
            "insufficient_quota",
            "insufficient quota",
            "insufficient credit",
            "insufficient_credit",
            "exceeded your current quota",
            "quota exceeded",
            "payment required",
            "billing",
            "add credits",
            "no credit",
        )
    )


def _indisponivel(exc: Exception) -> bool:
    """Provedor fora do ar ou inalcançável: 5xx, timeout, conexão recusada."""
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int) and 500 <= status < 600:
        return True
    if isinstance(exc, (asyncio.TimeoutError, ConnectionError, OSError)):
        return True
    texto = str(exc).lower()
    return any(
        marca in texto
        for marca in ("connection error", "connection refused", "timed out", "service unavailable")
    )


def _pode_trocar(exc: Exception) -> bool:
    """
    Vale acionar o próximo provedor da cadeia?

    Sim quando o provedor da vez não pode atender agora — teto de taxa, saldo ou
    indisponibilidade. Não quando o erro é da instalação (chave recusada, modelo
    inexistente, requisição malformada): trocar aí esconderia o defeito e faria
    a avaliação inteira rodar num provedor que ninguém escolheu.
    """
    return _is_rate_limit(exc) or _sem_saldo(exc) or _indisponivel(exc)


def _backoff_delay(exc: Exception, tentativa: int, teto: float) -> Optional[float]:
    """
    Quanto esperar antes de repetir — ou None quando não vale a pena esperar.

    O jitter existe porque a extração dispara chamadas em paralelo: sem ele, as
    que tomaram 429 juntas voltariam juntas e tomariam 429 de novo.
    """
    sugerido = _suggested_wait(exc)
    if sugerido is None:
        sugerido = min(2.0 ** tentativa, teto)
    elif sugerido > teto:
        # Janela maior que o teto configurado: segurar a requisição aqui só
        # empurraria o timeout para o usuário. Falha declarada, e não presumida.
        return None
    return sugerido + random.uniform(0, 1.0)


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
    # Provedor de quem era a vez, quando esta execução foi para um fallback.
    # Nulo na esmagadora maioria dos casos — e é essa a informação: não houve
    # troca. Preenchido, diz que o primário não pôde atender.
    fallback_from: Optional[str] = None

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
            "fallback_from": self.fallback_from,
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
    """
    Implementação sobre os chat models do LangChain, com cadeia de provedores.

    A cadeia é `settings.llm_chain`: o provedor configurado primeiro, os de
    `LLM_FALLBACK_PROVIDERS` depois. A troca acontece só quando o provedor da vez
    **não pode atender** — teto de taxa, saldo/cota, provedor fora do ar. Saída
    que não valida no schema não troca de provedor: aquilo é veredito de conteúdo
    da §25, e sair procurando um modelo que devolva JSON válido transformaria uma
    rejeição registrada numa busca por resposta conveniente.
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        model: Any = None,
        models: Optional[Dict[str, Any]] = None,
    ):
        self._settings = settings or get_settings()
        # Modelos injetáveis para teste: a suíte não deve depender de rede.
        # `model` preenche o primário; `models` (provedor → duplo) permite
        # exercitar a cadeia inteira sem chave nenhuma.
        self._models: Dict[str, Any] = dict(models or {})
        if model is not None:
            self._models.setdefault(self._settings.llm_provider, model)

    @property
    def provider(self) -> str:
        return self._settings.llm_provider

    @property
    def model_name(self) -> str:
        return self._settings.llm_model

    def _chat_model(self, profile: ProviderProfile) -> Any:
        modelo = self._models.get(profile.provider)
        if modelo is None:
            modelo = build_chat_model(self._settings, profile)
            self._models[profile.provider] = modelo
        return modelo

    async def _invoke(
        self, chat_model: Any, messages: Any, prompt_id: str, max_waits: int
    ) -> Tuple[Any, int]:
        """
        Chama o modelo, esperando e repetindo enquanto a recusa for 429.

        Devolve a mensagem e quantas esperas foram necessárias. Qualquer outro
        erro sobe intacto: só o limite de taxa é transitório por definição.

        `max_waits` é zero quando ainda há provedor alternativo na cadeia —
        segurar a chamada por 30 segundos enquanto outro provedor está ocioso
        seria trocar disponibilidade por espera sem ganho nenhum.
        """
        teto = float(self._settings.llm_rate_limit_max_wait_s)
        esperas = 0

        while True:
            try:
                return await chat_model.ainvoke(messages), esperas
            except Exception as exc:
                if not _is_rate_limit(exc) or esperas >= max_waits:
                    raise
                atraso = _backoff_delay(exc, esperas, teto)
                if atraso is None:
                    raise
                esperas += 1
                logger.info(
                    "Limite de taxa em %s; aguardando %.1fs antes da tentativa %d/%d.",
                    prompt_id,
                    atraso,
                    esperas,
                    max_waits,
                )
                await asyncio.sleep(atraso)

    async def structured_generate(
        self, prompt: RenderedPrompt, schema: Type[T], max_attempts: int = 2
    ) -> StructuredResult:
        cadeia = self._settings.llm_chain
        detalhe: list = []
        resultado: Optional[StructuredResult] = None

        for indice, profile in enumerate(cadeia):
            tem_alternativa = indice + 1 < len(cadeia)
            resultado, trocar = await self._generate_with(
                profile,
                prompt,
                schema,
                max_attempts,
                detalhe,
                tem_alternativa=tem_alternativa,
                fallback_from=cadeia[0].provider if indice else None,
            )
            if not trocar:
                return resultado
            logger.warning(
                "Provedor %s não pôde atender %s (%s); passando para %s.",
                profile.provider,
                prompt.prompt_id,
                resultado.run.error or "sem detalhe",
                cadeia[indice + 1].provider,
            )

        # Cadeia inteira esgotada. `resultado` carrega a falha do último elo, que
        # é a que o operador precisa ver. Não pode ser None: `llm_chain` sempre
        # tem ao menos o provedor primário, então o laço rodou pelo menos uma vez.
        if resultado is None:  # pragma: no cover — invariante da cadeia
            raise RuntimeError("cadeia de provedores vazia")
        return resultado

    async def _generate_with(
        self,
        profile: ProviderProfile,
        prompt: RenderedPrompt,
        schema: Type[T],
        max_attempts: int,
        attempts_detail: list,
        tem_alternativa: bool,
        fallback_from: Optional[str],
    ) -> Tuple[StructuredResult, bool]:
        """
        Uma passada completa (§25) num único provedor.

        Devolve o resultado e se vale tentar o próximo elo da cadeia.
        """
        input_hash = _sha256(f"{prompt.system}\n\n{prompt.user}")
        started = time.perf_counter()
        messages = list(prompt.as_messages())
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
                provider=profile.provider,
                model=profile.model,
                status=status,
                latency_ms=int((time.perf_counter() - started) * 1000),
                attempts=attempts,
                input_hash=input_hash,
                output_hash=_sha256(raw_text) if raw_text else None,
                error=error,
                fallback_from=fallback_from,
                **usage,
            )

        def falhou(status: str, attempts: int, error: str, trocar: bool):
            attempts_detail.append(
                {"attempt": attempts, "provider": profile.provider, "error": error}
            )
            return (
                StructuredResult(
                    run=record(status, attempts, error),
                    raw_text=raw_text,
                    attempts_detail=tuple(attempts_detail),
                ),
                trocar,
            )

        try:
            chat_model = self._chat_model(profile)
        except LLMUnavailable as exc:
            # Provedor mal configurado é motivo para tentar o próximo: manter a
            # cadeia parada num elo quebrado não ajuda ninguém.
            logger.warning("LLM indisponível em %s: %s", profile.provider, exc)
            return falhou(STATUS_UNAVAILABLE, 0, str(exc), tem_alternativa)

        # Com alternativa na cadeia, não se espera: passa a vez. Sem alternativa,
        # vale o orçamento configurado — é a única chance que resta.
        max_waits = 0 if tem_alternativa else max(0, self._settings.llm_rate_limit_retries)

        last_error = "resposta não validada"
        for attempt in range(1, max(1, max_attempts) + 1):
            try:
                message, esperas = await self._invoke(
                    chat_model, messages, prompt.prompt_id, max_waits
                )
            except Exception as exc:
                logger.warning(
                    "Falha na chamada à LLM (%s em %s): %s",
                    prompt.prompt_id,
                    profile.provider,
                    exc,
                )
                return falhou(
                    STATUS_UNAVAILABLE, attempt, str(exc), tem_alternativa and _pode_trocar(exc)
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
                    attempts_detail.append(
                        {
                            "attempt": attempt,
                            "provider": profile.provider,
                            "error": None,
                            "rate_limit_waits": esperas,
                        }
                    )
                    return (
                        StructuredResult(
                            run=record(STATUS_OK, attempt, None),
                            data=data,
                            raw_text=raw_text,
                            attempts_detail=tuple(attempts_detail),
                        ),
                        False,
                    )

            attempts_detail.append(
                {"attempt": attempt, "provider": profile.provider, "error": last_error}
            )

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
            "Saída da LLM inválida após %d tentativa(s) em %s (%s): %s",
            max_attempts,
            prompt.prompt_id,
            profile.provider,
            last_error,
        )
        # Sem troca de provedor: ver a docstring da classe.
        return (
            StructuredResult(
                run=record(STATUS_OUTPUT_INVALID, max_attempts, last_error),
                raw_text=raw_text,
                attempts_detail=tuple(attempts_detail),
            ),
            False,
        )


def get_llm_client(settings: Optional[Settings] = None) -> LLMClient:
    return LangChainLLMClient(settings=settings)
