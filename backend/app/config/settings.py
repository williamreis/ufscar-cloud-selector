"""
Configuração central da aplicação.

A diretriz (§23.2, §28.3, §35) exige que limites, versões e escolha de provedor
saiam de configuração, não de constante em regra de negócio. Este módulo é o
único ponto que lê `os.environ`; o resto do código pede `get_settings()`.

Duas decisões que explicam o formato:

  - **leitura tardia, não no import.** Os valores são resolvidos na primeira
    chamada de `get_settings()` e ficam em cache. Ler no import congelaria o
    ambiente no momento em que o módulo entra, o que quebraria qualquer teste
    que precise variar um limite (é o que `reload_settings()` desfaz).

  - **compatibilidade com o `.env` que já existe.** Os nomes antigos
    (`OPENAI_MODEL`, `GROQ_MODEL`, ...) continuam valendo; `LLM_MODEL` é um
    override opcional acima deles. Uma instalação existente não precisa
    reescrever o `.env` para continuar subindo.
"""

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

# app/config/settings.py → parents: [0] config · [1] app · [2] backend · [3] repo
#
# `_BACKEND_ROOT` vale nos dois ambientes: é `<repo>/backend` localmente e `/app`
# no container, porque o compose monta `./backend` ali. `_REPO_ROOT` só existe
# localmente — em container ele resolve para `/`, e por isso serve apenas de
# default de desenvolvimento para arquivos que vivem fora de `backend/`.
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[3]

# Modelo de embedding por provedor quando EMBEDDING_MODEL não é informado.
#
# O default da OpenAI é o `text-embedding-ada-002` **de propósito**: é o que o
# LangChain usava implicitamente até aqui, e portanto é o modelo que gerou o
# índice FAISS já persistido. Trocá-lo por um mais novo (`text-embedding-3-*`)
# mudaria a dimensão dos vetores e invalidaria o índice existente sem aviso.
DEFAULT_EMBEDDING_MODELS: Dict[str, str] = {
    "openai": "text-embedding-ada-002",
    "huggingface": "all-MiniLM-L6-v2",
}

# Modelo de LLM por provedor quando LLM_MODEL não é informado. Cada entrada é o
# nome da variável antiga, mantida para não quebrar `.env` já existentes.
_LEGACY_MODEL_VARS: Dict[str, Tuple[str, str]] = {
    "openai": ("OPENAI_MODEL", "gpt-4o-mini"),
    "groq": ("GROQ_MODEL", "openai/gpt-oss-120b"),
    # O `meta-llama/llama-4-maverick:free` saiu do catálogo gratuito (a API
    # responde 404 pedindo a versão paga). O nemotron abaixo foi verificado
    # contra o prompt de extração real: devolve JSON válido em ~8s e aguenta as
    # chamadas seguidas de uma avaliação inteira.
    #
    # O sufixo `:free` é deliberado no *default* — um valor que ninguém escolheu
    # não pode começar a gastar crédito — e tem um preço que precisa estar dito:
    # chamadas `:free` não consomem saldo e por isso saldo não as libera, elas
    # caem num teto de requisições por dia da conta inteira. Quem tem crédito e
    # quer usá-lo tira o `:free` em `OPENROUTER_MODEL`.
    "openrouter": ("OPENROUTER_MODEL", "nvidia/nemotron-3-super-120b-a12b:free"),
    "gemini": ("GEMINI_MODEL", "gemini-2.5-flash"),
    "ollama": ("OLLAMA_MODEL", "llama3.1"),
}

# Variável que carrega a chave de cada provedor. Ollama é local e não usa chave.
_API_KEY_VARS: Dict[str, Optional[str]] = {
    "openai": "OPENAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "ollama": None,
}

SUPPORTED_LLM_PROVIDERS: Tuple[str, ...] = tuple(_LEGACY_MODEL_VARS)
SUPPORTED_EMBEDDING_PROVIDERS: Tuple[str, ...] = ("openai", "huggingface")


@dataclass(frozen=True)
class ProviderProfile:
    """
    O trio que identifica uma LLM utilizável: quem atende, qual modelo, qual chave.

    Existe porque a cadeia de fallback precisa carregar provedores que **não** são
    o configurado em `LLM_PROVIDER` — e cada um tem modelo e credencial próprios.
    Reaproveitar `llm_model` no fallback mandaria o nome de um modelo do Groq para
    o OpenRouter; reaproveitar `llm_api_key` mandaria a chave errada junto.
    """

    provider: str
    model: str
    api_key: Optional[str]

    @property
    def usable(self) -> bool:
        """Ollama roda local e não usa chave; os demais sem chave não atendem."""
        return self.provider == "ollama" or bool(self.api_key)


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_optional_int(name: str) -> Optional[int]:
    """Inteiro que aceita ausência como 'desligado' — não como zero."""
    raw = _env(name)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _env_list(name: str, default: str) -> List[str]:
    raw = _env(name, default) or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


def _env_floats(name: str, default: str) -> Tuple[float, ...]:
    values = []
    for item in _env_list(name, default):
        try:
            values.append(float(item))
        except ValueError:
            continue
    return tuple(values)


@dataclass(frozen=True)
class Settings:
    """Fotografia imutável da configuração em vigor."""

    # -- LLM (§35) ----------------------------------------------------------
    llm_provider: str
    llm_model: str
    llm_api_key: Optional[str]
    llm_temperature: float
    llm_max_tokens: int
    ollama_base_url: str

    # -- Limite de taxa do provedor (§26) -----------------------------------
    # As camadas gratuitas (Groq, OpenRouter, Gemini) impõem teto de tokens por
    # minuto. Bater nesse teto é espera, não indisponibilidade: o cliente aguarda
    # o tempo que o provedor pede e repete a chamada, e só desiste depois de
    # `llm_rate_limit_retries` esperas ou quando a espera pedida passa do teto —
    # aí sim vira LLM_UNAVAILABLE, sem virar pontuação presumida.
    llm_rate_limit_retries: int
    llm_rate_limit_max_wait_s: float
    # Teto de uma única chamada, em segundos. Sem ele o SDK do provedor pode
    # segurar a requisição indefinidamente e a tela do gestor fica girando.
    llm_timeout_s: float
    # Teto de quanto tempo um provedor fica de fora depois de recusar por cota.
    #
    # É outro orçamento, e não o mesmo de `llm_rate_limit_max_wait_s`: aquele
    # limita quanto uma requisição pode ficar **parada esperando**, e por isso é
    # curto. Este limita por quanto tempo o provedor é **pulado**, o que não custa
    # espera nenhuma. Confundir os dois truncava a janela de cota diária do Groq
    # (21 minutos) em 60 segundos, e as chamadas voltavam a bater na recusa.
    llm_cooldown_max_s: float
    # Chamadas simultâneas à LLM na extração de evidências. Em camada gratuita
    # com teto de tokens por minuto, 1 é o valor que não desperdiça espera.
    llm_concurrency: int

    # Provedores acionados, em ordem, quando o primário não pode atender — teto
    # de taxa estourado, saldo/cota esgotados ou provedor fora do ar. Não é
    # balanceamento: o primário é sempre tentado primeiro, e a troca fica
    # registrada em cada execução (§27) para que o relatório mostre qual modelo
    # produziu qual evidência.
    llm_fallbacks: Tuple[ProviderProfile, ...]

    # -- Embeddings (§35) ---------------------------------------------------
    # Separados do LLM de propósito: rodar a inferência no Groq e os embeddings
    # na OpenAI é uma combinação legítima, e antes era impossível de expressar.
    # Por isso a chave também é própria: reaproveitar `llm_api_key` mandaria a
    # credencial do Groq para o cliente da OpenAI nessa combinação.
    embedding_provider: str
    embedding_model: str
    embedding_api_key: Optional[str]

    # -- RAG (§14) ----------------------------------------------------------
    vector_db_path: str
    vector_collection_name: str
    chunk_size: int
    chunk_overlap: int
    max_chunks_per_query: int

    # -- Guardrails de entrada (§23.2) --------------------------------------
    max_open_text_chars: int
    max_upload_mb: int
    max_documents_per_evaluation: int
    allowed_upload_extensions: Tuple[str, ...]
    secret_action: str

    # -- Versionamento (§28) ------------------------------------------------
    questionnaire_version: str
    scoring_algorithm_version: str
    questions_json_path: Path

    # -- Configuração metodológica (§39, §45) -------------------------------
    # Indicadores e escalas ficam fora do código para que a decisão acadêmica
    # possa mudar sem alterar regra de negócio. Ambos entram no hash de versão.
    indicators_path: Path
    methodology_scales_path: Path

    # -- Persistência -------------------------------------------------------
    audit_db_path: Path
    # Onde as janelas de cota dos provedores sobrevivem a um reinício. Fica ao
    # lado do banco de auditoria porque é o volume que já é persistente; não é
    # dado de auditoria e por isso não entra no banco. Cache puro: apagar o
    # arquivo só faz a próxima leva sondar de novo.
    llm_cooldown_state_path: Path

    # -- Pontos em aberto na dissertação, configuráveis por decisão (§45) ---
    # TODO ACADÊMICO 04: limiar de atualidade documental. Sem valor definido o
    # alerta fica desligado — a diretriz é explícita em não assumir 24 meses
    # como regra científica antes da confirmação.
    evidence_stale_months: Optional[int]
    # TODO ACADÊMICO 05: deltas da análise de sensibilidade. O default abaixo é
    # técnico e provisório, não uma decisão metodológica.
    sensitivity_deltas: Tuple[float, ...] = field(default=(-0.10, -0.05, 0.05, 0.10))

    @property
    def llm_primary(self) -> ProviderProfile:
        return ProviderProfile(self.llm_provider, self.llm_model, self.llm_api_key)

    @property
    def llm_chain(self) -> Tuple[ProviderProfile, ...]:
        """Primário seguido dos fallbacks — a ordem em que serão tentados."""
        return (self.llm_primary, *self.llm_fallbacks)

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


def _build_settings() -> Settings:
    # Groq é o padrão por ser a camada gratuita mais rápida entre as suportadas.
    # Um `.env` que já define LLM_PROVIDER continua mandando.
    llm_provider = (_env("LLM_PROVIDER", "groq") or "groq").lower()
    if llm_provider not in SUPPORTED_LLM_PROVIDERS:
        raise ValueError(
            f"LLM_PROVIDER inválido: {llm_provider!r}. "
            f"Use um de: {', '.join(SUPPORTED_LLM_PROVIDERS)}."
        )

    legacy_var, legacy_default = _LEGACY_MODEL_VARS[llm_provider]
    llm_model = _env("LLM_MODEL") or _env(legacy_var, legacy_default)

    key_var = _API_KEY_VARS[llm_provider]
    llm_api_key = _env(key_var) if key_var else None

    # Cadeia de fallback. `LLM_MODEL` de propósito **não** entra aqui: aquele
    # override diz qual modelo usar no provedor primário, e aplicá-lo ao fallback
    # pediria ao OpenRouter um nome de modelo que só existe no Groq.
    fallbacks: List[ProviderProfile] = []
    for nome in _env_list("LLM_FALLBACK_PROVIDERS", "openrouter"):
        nome = nome.lower()
        if nome == llm_provider or nome not in SUPPORTED_LLM_PROVIDERS:
            continue
        var, padrao = _LEGACY_MODEL_VARS[nome]
        chave_var = _API_KEY_VARS[nome]
        perfil = ProviderProfile(
            provider=nome,
            model=_env(var, padrao),
            api_key=_env(chave_var) if chave_var else None,
        )
        # Fallback sem credencial não é fallback: manter na cadeia só trocaria um
        # erro de cota por um erro de configuração, mais tarde e menos claro.
        if perfil.usable and perfil not in fallbacks:
            fallbacks.append(perfil)

    # Sem EMBEDDING_PROVIDER explícito, reproduz a regra anterior: OpenAI usava
    # OpenAIEmbeddings, qualquer outro provedor caía no sentence-transformers
    # local. Assim um .env antigo continua gerando o mesmo índice.
    embedding_provider = (
        _env("EMBEDDING_PROVIDER") or ("openai" if llm_provider == "openai" else "huggingface")
    ).lower()
    if embedding_provider not in SUPPORTED_EMBEDDING_PROVIDERS:
        raise ValueError(
            f"EMBEDDING_PROVIDER inválido: {embedding_provider!r}. "
            f"Use um de: {', '.join(SUPPORTED_EMBEDDING_PROVIDERS)}."
        )
    embedding_model = _env("EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODELS[embedding_provider]

    # O sentence-transformers roda local e não usa chave; a OpenAI usa a dela,
    # independentemente de qual provedor atende o LLM.
    embedding_api_key = (
        _env("EMBEDDING_API_KEY") or _env("OPENAI_API_KEY")
        if embedding_provider == "openai"
        else None
    )

    secret_action = (_env("GUARDRAIL_SECRET_ACTION", "MASK") or "MASK").upper()
    if secret_action not in ("MASK", "REJECT", "WARN"):
        raise ValueError(
            f"GUARDRAIL_SECRET_ACTION inválido: {secret_action!r}. Use MASK, REJECT ou WARN."
        )

    extensions = tuple(
        ext if ext.startswith(".") else f".{ext}"
        for ext in (e.lower() for e in _env_list("ALLOWED_UPLOAD_EXTENSIONS", ".pdf,.txt"))
    )

    questions_path = _env("QUESTIONS_JSON_PATH")
    questions_json_path = (
        Path(questions_path).expanduser()
        if questions_path
        else _REPO_ROOT / "frontend" / "public" / "questions.json"
    )

    def _methodology_path(var: str, filename: str) -> Path:
        explicit = _env(var)
        if explicit:
            return Path(explicit).expanduser()
        return _BACKEND_ROOT / "methodology" / filename

    audit_db_path = Path(_env("AUDIT_DB_PATH", "../data/audit.db")).resolve()

    indicators_path = _methodology_path("INDICATORS_PATH", "indicators.json")
    methodology_scales_path = _methodology_path("METHODOLOGY_SCALES_PATH", "scales.json")

    return Settings(
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_api_key=llm_api_key,
        # Zero por padrão. As três chamadas do produto — extração de evidência,
        # refinamento de consulta e notas de preferência — passam todas por
        # `structured_generate`: são classificação e leitura de documento em
        # JSON, e não há nada nelas que a amostragem melhore. Com 0.2, duas
        # execuções do mesmo questionário sobre o mesmo índice liam valores
        # diferentes do mesmo relatório (90% num envio, 100% no seguinte) e
        # mudavam quais indicadores sobreviviam ao conjunto comparável — num
        # relatório que se apresenta como auditável, isso é o defeito. Continua
        # sobrescritível por LLM_TEMPERATURE para experimentação.
        llm_temperature=float(_env("LLM_TEMPERATURE", "0.0")),
        # 1500 truncava a extração: uma dimensão devolve um `finding` por
        # indicador (5, no questionário atual) com resumo em texto, e os modelos
        # de raciocínio ainda gastam parte do teto pensando antes de escrever o
        # JSON. Resposta cortada não fecha o objeto e a dimensão inteira se perde.
        llm_max_tokens=_env_int("LLM_MAX_TOKENS", 4000),
        ollama_base_url=_env("OLLAMA_BASE_URL", "http://localhost:11434"),
        llm_rate_limit_retries=_env_int("LLM_RATE_LIMIT_RETRIES", 4),
        llm_rate_limit_max_wait_s=_env_float("LLM_RATE_LIMIT_MAX_WAIT_S", 60.0),
        llm_timeout_s=_env_float("LLM_TIMEOUT_S", 60.0),
        # 30 minutos cobre a janela de cota diária das camadas gratuitas. Acima
        # disso vale re-testar de vez em quando: uma chamada perdida a cada meia
        # hora é barato, e aceitar qualquer prazo declarado deixaria o provedor
        # de fora por horas com base num número que não controlamos.
        llm_cooldown_max_s=_env_float("LLM_COOLDOWN_MAX_S", 1800.0),
        llm_concurrency=max(1, _env_int("LLM_CONCURRENCY", 3)),
        llm_fallbacks=tuple(fallbacks),
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        embedding_api_key=embedding_api_key,
        vector_db_path=_env("VECTOR_DB_PATH", "../data/faiss_index"),
        vector_collection_name=_env("VECTOR_COLLECTION_NAME", "providers_docs"),
        chunk_size=_env_int("CHUNK_SIZE", 1000),
        chunk_overlap=_env_int("CHUNK_OVERLAP", 200),
        max_chunks_per_query=_env_int("MAX_CHUNKS_PER_QUERY", 5),
        max_open_text_chars=_env_int("MAX_OPEN_TEXT_CHARS", 4000),
        max_upload_mb=_env_int("MAX_UPLOAD_MB", 20),
        max_documents_per_evaluation=_env_int("MAX_DOCUMENTS_PER_EVALUATION", 20),
        allowed_upload_extensions=extensions,
        secret_action=secret_action,
        questionnaire_version=_env("QUESTIONNAIRE_VERSION", "1"),
        scoring_algorithm_version=_env("SCORING_ALGORITHM_VERSION", "1"),
        questions_json_path=questions_json_path,
        indicators_path=indicators_path,
        methodology_scales_path=methodology_scales_path,
        audit_db_path=audit_db_path,
        llm_cooldown_state_path=audit_db_path.parent / "llm_cooldown.json",
        evidence_stale_months=_env_optional_int("EVIDENCE_STALE_MONTHS"),
        sensitivity_deltas=_env_floats("SENSITIVITY_DELTAS", "-0.10,-0.05,0.05,0.10"),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return _build_settings()


def reload_settings() -> Settings:
    """Relê o ambiente. Existe para os testes variarem um limite sem subprocesso."""
    get_settings.cache_clear()
    return get_settings()
