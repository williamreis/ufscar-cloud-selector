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

E a troca é lembrada: quando um provedor recusa por cota informando "tente em
11s", esse prazo passa a valer para as chamadas seguintes, que pulam o elo direto
em vez de repetir a mesma recusa. Uma avaliação faz uma chamada por (provedor ×
dimensão) — sem essa memória, todas batem no primário só para ouvir o mesmo não.

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
import os
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
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

# Mesma duração, quando ela vem sozinha num cabeçalho ("31.5s", "2m59.56s") em
# vez de embutida na frase.
_DURACAO_PATTERN = re.compile(r"(?:(?P<min>[\d.]+)m)?(?P<sec>[\d.]+)s", re.IGNORECASE)

# Cabeçalhos que declaram a reposição, em ordem de precedência. `retry-after` é o
# delta canônico do HTTP; os `x-ratelimit-reset*` variam de formato entre
# provedores e por isso passam todos pelo mesmo tradutor.
_RESET_HEADERS = (
    "retry-after",
    "x-ratelimit-reset-tokens",
    "x-ratelimit-reset-requests",
    "x-ratelimit-reset",
)

# O OpenRouter aninha os cabeçalhos de limite no **corpo** do 429
# (`error.metadata.headers`) e não repete a janela em prosa. Sem ler isto daqui, a
# recusa dele chega sem janela nenhuma e o cliente cai no backoff cego — foi o que
# gastou quatro esperas por chamada contra um limite que só vira no outro dia.
_RESET_BODY_PATTERN = re.compile(
    r"['\"]x-ratelimit-reset['\"]\s*:\s*['\"]?(?P<valor>\d+)", re.IGNORECASE
)

# Marcas de teto por **dia** na recusa.
#
# A distinção entre teto por minuto e teto por dia é o que decide entre esperar e
# desistir. Um teto de tokens por minuto passa em segundos e aguardar recupera a
# extração; um teto diário só vira na virada do dia, e o orçamento de espera do
# cliente é de dezenas de segundos — nenhuma tentativa o alcança, e cada uma só
# soma latência à requisição do gestor.
_MARCAS_LIMITE_DIARIO = ("per day", "per-day", "daily", "(tpd)", "(rpd)")


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


def _janela_de_reset(bruto: Any) -> Optional[float]:
    """
    Traduz um valor de reposição em "segundos a partir de agora".

    Três formatos convivem entre os provedores suportados e só a ordem de grandeza
    os separa: o OpenRouter manda epoch em **milissegundos** (`1787616000000`), há
    quem mande epoch em segundos, e o `retry-after` do HTTP manda o delta já
    pronto. Os cortes são folgados de propósito — nenhum delta plausível chega a
    10^9 segundos (32 anos) e nenhum epoch atual fica abaixo disso.

    Janela já vencida vira 0.0, e não um número negativo: relógio fora de sincronia
    com o provedor é motivo para tentar de novo, não para esperar ao contrário.
    """
    texto = str(bruto).strip()
    if not texto:
        return None

    match = _DURACAO_PATTERN.fullmatch(texto)
    if match:
        return float(match.group("min") or 0) * 60 + float(match.group("sec"))

    try:
        valor = float(texto)
    except ValueError:
        return None

    if valor >= 1e12:  # epoch em milissegundos
        return max(0.0, valor / 1000 - time.time())
    if valor >= 1e9:  # epoch em segundos
        return max(0.0, valor - time.time())
    return max(0.0, valor)  # delta já relativo


def _limite_diario(exc: Exception) -> bool:
    """Teto por dia, e não por minuto: a janela é de horas, não de segundos."""
    texto = str(exc).lower()
    return any(marca in texto for marca in _MARCAS_LIMITE_DIARIO)


def _suggested_wait(exc: Exception) -> Optional[float]:
    """
    Tempo de espera que o provedor informou, em segundos, se informou.

    A ordem das fontes é a da confiabilidade. Cabeçalho vem antes do texto porque
    é contrato; entre os textos, a frase "try again in 31.5s" vem antes do epoch
    de reposição porque é um delta e não depende de os dois relógios concordarem.
    """
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    # Normaliza a caixa: `httpx.Headers` já é insensível, mas um dicionário simples
    # (o duplo dos testes, um SDK que exponha o corpo cru) não é.
    if hasattr(headers, "items"):
        procura = {str(chave).lower(): valor for chave, valor in headers.items()}
        for chave in _RESET_HEADERS:
            janela = _janela_de_reset(procura.get(chave, ""))
            if janela is not None:
                return janela

    texto = str(exc)
    match = _RETRY_AFTER_PATTERN.search(texto)
    if match:
        minutos = float(match.group("min") or 0)
        return minutos * 60 + float(match.group("sec"))

    match = _RESET_BODY_PATTERN.search(texto)
    if match:
        return _janela_de_reset(match.group("valor"))
    return None


# Provedores em compasso de espera: nome → instante (relógio monotônico) a partir
# do qual vale tentar de novo.
#
# Sem isto, **toda** chamada da avaliação bate no provedor primário só para tomar
# o mesmo 429 e cair no fallback: com 9 extrações são 9 ida-e-voltas jogados fora,
# e o gestor paga a latência de todas. Quando a Groq diz "tente em 11s", essa
# informação vale para as chamadas seguintes, não só para aquela.
#
# É estado de processo e deliberadamente frouxo: expira sozinho, nunca impede a
# última alternativa da cadeia de ser tentada, e no pior caso (reinício, vários
# workers) só se perde a economia — nunca a correção.
_provider_cooldown: Dict[str, float] = {}

# Provedores que já responderam neste processo, e as sondagens em curso.
#
# Isto resolve a **primeira leva**, que o cooldown sozinho não alcança: ele só é
# gravado quando a recusa *volta*, e com `LLM_CONCURRENCY=3` as três chamadas já
# saíram antes disso — cada uma colhendo o mesmo não do mesmo provedor estourado.
#
# A regra é: enquanto o provedor não tiver respondido nenhuma vez neste processo,
# **uma** chamada sonda e as demais aguardam o veredito dela em vez de saírem
# junto. Aguardar, e não pular direto para o fallback, é deliberado: se o provedor
# estiver de pé, todas seguem por ele e a avaliação inteira sai de um modelo só —
# que é o que a §27 precisa poder afirmar no relatório. Assim que a sonda responde,
# o provedor entra em `_provider_healthy` e a concorrência volta a correr solta;
# se ela for recusada por cota, o cooldown já está gravado quando as outras acordam
# e todas passam direto ao fallback, sem uma segunda chamada sequer.
#
# "Healthy" aqui é só "atende": saída que não valida no schema conta, porque o
# provedor respondeu — aquilo é veredito de conteúdo da §25, não de disponibilidade.
#
# Não há lock porque não há concorrência real: as corrotinas rodam no mesmo laço de
# eventos, e entre consultar `_provider_probe` e reivindicá-lo não existe await.
_provider_healthy: set = set()
_provider_probe: Dict[str, asyncio.Event] = {}

# Estado de cota já lido do disco neste processo (ver `_carregar_cooldowns`).
_cooldown_carregado = False


def reset_provider_cooldowns() -> None:
    """Zera as esperas registradas. Existe para os testes não vazarem estado."""
    global _cooldown_carregado
    _provider_cooldown.clear()
    _provider_healthy.clear()
    _provider_probe.clear()
    _cooldown_carregado = False


# --- Cota que sobrevive ao reinício -----------------------------------------
#
# O cooldown acima é estado de processo, e reiniciar o backend o apaga. O efeito
# aparece no log: sobe o servidor, e a primeira avaliação volta a gastar chamadas
# num provedor que já estava em cota diária havia 40 minutos — informação que o
# processo anterior tinha e jogou fora ao morrer.
#
# Por isso a janela é gravada em disco, no mesmo volume persistente do banco de
# auditoria. O que se grava é o **instante absoluto** de liberação, não o que
# falta: o relógio monotônico não atravessa reinício, e um prazo relativo
# gravado viraria uma janela nova a cada subida.
#
# É cache, não fonte de verdade: qualquer erro de leitura ou escrita é engolido e
# o mecanismo volta a ser só de memória. Perder a economia é aceitável; deixar o
# produto de pé não é negociável.


def _carregar_cooldowns(caminho: Optional[Path]) -> None:
    """Relê, uma vez por processo, as janelas de cota que a execução anterior deixou."""
    global _cooldown_carregado
    if _cooldown_carregado:
        return
    _cooldown_carregado = True
    if caminho is None:
        return

    try:
        gravado = json.loads(Path(caminho).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(gravado, dict):
        return

    agora_epoch = time.time()
    for provider, ate_epoch in gravado.items():
        try:
            restante = float(ate_epoch) - agora_epoch
        except (TypeError, ValueError):
            continue
        if restante > 0:
            _provider_cooldown[str(provider)] = time.monotonic() + restante
            logger.info(
                "Provedor %s continua em espera de cota por mais %s (registro anterior).",
                provider,
                _duracao(restante),
            )


def _gravar_cooldowns(caminho: Optional[Path]) -> None:
    """Persiste as janelas ainda vigentes, em instante absoluto."""
    if caminho is None:
        return
    agora_mono, agora_epoch = time.monotonic(), time.time()
    vigentes = {
        provider: agora_epoch + (ate - agora_mono)
        for provider, ate in _provider_cooldown.items()
        if ate > agora_mono
    }
    destino = Path(caminho)
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        # Escreve ao lado e renomeia: rename é atômico no mesmo sistema de
        # arquivos, então nenhum outro worker lê um JSON pela metade.
        provisorio = destino.parent / f"{destino.name}.{os.getpid()}.tmp"
        provisorio.write_text(json.dumps(vigentes), encoding="utf-8")
        provisorio.replace(destino)
    except OSError as exc:  # volume só-leitura, disco cheio: segue de memória
        logger.debug("Não foi possível persistir a espera de cota em %s: %s", destino, exc)


def _duracao(segundos: float) -> str:
    """'21min' em vez de '1280s' — o operador lê a janela, não o número bruto."""
    if segundos >= 90:
        return f"{segundos / 60:.0f}min"
    return f"{segundos:.0f}s"


def _cooldown_restante(provider: str) -> float:
    """Segundos que ainda faltam para valer a pena tentar este provedor."""
    ate = _provider_cooldown.get(provider)
    if ate is None:
        return 0.0
    restante = ate - time.monotonic()
    if restante <= 0:
        _provider_cooldown.pop(provider, None)
        return 0.0
    return restante


def _registrar_cooldown(
    provider: str, exc: Exception, teto: float, caminho: Optional[Path] = None
) -> None:
    """
    Anota até quando pular o provedor, usando a janela que ele mesmo informou.

    A janela é respeitada por inteiro até `teto` — e `teto` aqui é o de *pular*,
    não o de *esperar*. Quando o Groq estoura a cota diária ele pede 21 minutos;
    encurtar isso para um minuto só faz as chamadas seguintes voltarem a colher a
    mesma recusa.
    """
    janela = _suggested_wait(exc)
    if janela is None:
        # Sem janela declarada não se inventa uma longa: um respiro curto já evita
        # a saraivada de chamadas idênticas. A exceção é a recusa que se declara
        # diária — aí o respiro curto é que estaria errado, porque garante que as
        # chamadas seguintes colham a mesma recusa, e o teto de pular é o palpite
        # honesto na falta de um prazo do provedor.
        janela = teto if _limite_diario(exc) else 5.0
    _provider_cooldown[provider] = time.monotonic() + min(janela, teto)
    # Quem acabou de recusar por cota deixa de ser "atende": a próxima leva volta
    # a sondar com uma chamada só quando a janela expirar.
    _provider_healthy.discard(provider)
    _gravar_cooldowns(caminho)


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
    if _limite_diario(exc):
        # Cota diária: a janela é de horas e nenhuma das esperas configuradas a
        # alcança. Insistir aqui não recupera a extração — só entrega ao gestor a
        # soma de todos os backoffs antes do mesmo LLM_UNAVAILABLE.
        return None

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


def _finish_reason(message: Any) -> Optional[str]:
    metadata = getattr(message, "response_metadata", None) or {}
    if not isinstance(metadata, dict):
        return None
    razao = metadata.get("finish_reason") or metadata.get("stop_reason")
    return str(razao) if razao else None


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
        estado = self._settings.llm_cooldown_state_path
        _carregar_cooldowns(estado)
        detalhe: list = []
        resultado: Optional[StructuredResult] = None

        for indice, profile in enumerate(cadeia):
            tem_alternativa = indice + 1 < len(cadeia)

            if tem_alternativa:
                # Enquanto o provedor não tiver respondido neste processo, uma
                # chamada sonda e as outras aguardam o veredito dela aqui. É o que
                # impede a primeira leva de sair junta e colher, cada uma, a mesma
                # recusa — o laço trata a sondagem que começa *enquanto* se espera.
                while profile.provider not in _provider_healthy:
                    sonda = _provider_probe.get(profile.provider)
                    if sonda is None:
                        break
                    await sonda.wait()

            # Provedor em cota é pulado enquanto a janela que ele pediu não passa —
            # desde que haja para quem passar. O último elo é sempre tentado: pular
            # todos devolveria falha sem ter chamado ninguém.
            restante = _cooldown_restante(profile.provider) if tem_alternativa else 0.0
            if restante > 0:
                motivo = f"em espera de cota por mais {_duracao(restante)}"
                logger.info("Pulando %s em %s: %s.", profile.provider, prompt.prompt_id, motivo)
                detalhe.append(
                    {"attempt": 0, "provider": profile.provider, "error": f"pulado: {motivo}"}
                )
                continue

            # Reivindica a sondagem. Entre o `break` acima e esta linha não há
            # await, então duas corrotinas não se reivindicam sondas ao mesmo tempo:
            # a segunda a acordar já encontra o evento novo e volta a esperar.
            sondando = tem_alternativa and profile.provider not in _provider_healthy
            if sondando:
                _provider_probe[profile.provider] = asyncio.Event()
            try:
                resultado, trocar = await self._generate_with(
                    profile,
                    prompt,
                    schema,
                    max_attempts,
                    detalhe,
                    tem_alternativa=tem_alternativa,
                    fallback_from=cadeia[0].provider if indice else None,
                    estado=estado,
                )
            finally:
                if sondando:
                    evento = _provider_probe.pop(profile.provider, None)
                    if evento is not None:
                        evento.set()

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
        # é a que o operador precisa ver. Não pode ser None: o último elo nunca é
        # pulado por cooldown, então o laço chamou alguém pelo menos uma vez.
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
        estado: Optional[Path] = None,
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
                if _is_rate_limit(exc) or _sem_saldo(exc):
                    _registrar_cooldown(
                        profile.provider, exc, float(self._settings.llm_cooldown_max_s), estado
                    )
                return falhou(
                    STATUS_UNAVAILABLE, attempt, str(exc), tem_alternativa and _pode_trocar(exc)
                )

            # O provedor respondeu: para efeito de disponibilidade ele atende, e a
            # próxima leva pode sair em paralelo sem sondagem. Se a resposta não
            # validar no schema, isso é veredito de conteúdo da §25 logo abaixo —
            # outra coisa.
            _provider_healthy.add(profile.provider)

            raw_text = _message_text(message)
            usage = _token_usage(message)

            payload = extract_json_object(raw_text)
            if payload is None:
                # Resposta cortada no teto de tokens é diagnóstico diferente de
                # resposta malformada, e a diferença é acionável: uma pede outro
                # prompt, a outra pede LLM_MAX_TOKENS maior. Modelos de raciocínio
                # gastam o teto pensando antes de escrever o JSON, e é o caso em
                # que isso mais aparece.
                if _finish_reason(message) in ("length", "max_tokens"):
                    last_error = (
                        "a resposta foi truncada no teto de tokens "
                        f"(LLM_MAX_TOKENS={self._settings.llm_max_tokens}) antes de fechar o JSON"
                    )
                else:
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
