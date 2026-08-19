"""
Função de embedding, separada da escolha do LLM (diretriz §35).

Antes o embedding era um efeito colateral do `LLM_PROVIDER`: OpenAI usava
`OpenAIEmbeddings`, qualquer outro provedor caía no sentence-transformers local.
Isso tornava impossível expressar combinações legítimas — rodar a inferência num
provedor comercial e os embeddings localmente, por exemplo — e deixava o modelo
de embedding fora do registro de auditoria exigido pela §27.

Agora `EMBEDDING_PROVIDER` e `EMBEDDING_MODEL` são explícitos, e o default
reproduz exatamente a regra anterior para não invalidar índices já gerados.
"""

from functools import lru_cache
from typing import Any

from config import Settings, get_settings
from llm.providers import LLMUnavailable


@lru_cache(maxsize=2)
def _build(provider: str, model: str, api_key: str | None) -> Any:
    """
    Instancia a função de embedding.

    Em cache por (provedor, modelo, chave): o `HuggingFaceEmbeddings` carrega o
    modelo do disco, o que custava segundos a cada consulta quando era recriado.
    """
    try:
        if provider == "openai":
            from langchain_openai import OpenAIEmbeddings

            return OpenAIEmbeddings(openai_api_key=api_key, model=model)

        from langchain_huggingface import HuggingFaceEmbeddings

        return HuggingFaceEmbeddings(model_name=model)
    except ImportError as exc:
        raise LLMUnavailable(
            f"Integração de embeddings do provedor {provider!r} não instalada: {exc}."
        ) from exc


def get_embedding_function(settings: Settings | None = None) -> Any:
    settings = settings or get_settings()
    return _build(
        settings.embedding_provider, settings.embedding_model, settings.embedding_api_key
    )


def reset_embedding_cache() -> None:
    """Descarta o cache — usado quando a configuração muda em teste."""
    _build.cache_clear()
