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
    "openrouter": ("OPENROUTER_MODEL", "meta-llama/llama-4-maverick:free"),
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

    # -- Pontos em aberto na dissertação, configuráveis por decisão (§45) ---
    # TODO ACADÊMICO 04: limiar de atualidade documental. Sem valor definido o
    # alerta fica desligado — a diretriz é explícita em não assumir 24 meses
    # como regra científica antes da confirmação.
    evidence_stale_months: Optional[int]
    # TODO ACADÊMICO 05: deltas da análise de sensibilidade. O default abaixo é
    # técnico e provisório, não uma decisão metodológica.
    sensitivity_deltas: Tuple[float, ...] = field(default=(-0.10, -0.05, 0.05, 0.10))

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


def _build_settings() -> Settings:
    llm_provider = (_env("LLM_PROVIDER", "openai") or "openai").lower()
    if llm_provider not in SUPPORTED_LLM_PROVIDERS:
        raise ValueError(
            f"LLM_PROVIDER inválido: {llm_provider!r}. "
            f"Use um de: {', '.join(SUPPORTED_LLM_PROVIDERS)}."
        )

    legacy_var, legacy_default = _LEGACY_MODEL_VARS[llm_provider]
    llm_model = _env("LLM_MODEL") or _env(legacy_var, legacy_default)

    key_var = _API_KEY_VARS[llm_provider]
    llm_api_key = _env(key_var) if key_var else None

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

    indicators_path = _methodology_path("INDICATORS_PATH", "indicators.json")
    methodology_scales_path = _methodology_path("METHODOLOGY_SCALES_PATH", "scales.json")

    return Settings(
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_api_key=llm_api_key,
        llm_temperature=float(_env("LLM_TEMPERATURE", "0.2")),
        llm_max_tokens=_env_int("LLM_MAX_TOKENS", 1500),
        ollama_base_url=_env("OLLAMA_BASE_URL", "http://localhost:11434"),
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
        audit_db_path=Path(_env("AUDIT_DB_PATH", "../data/audit.db")).resolve(),
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
