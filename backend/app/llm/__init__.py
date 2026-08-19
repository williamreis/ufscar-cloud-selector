"""Camada de LLM: adaptador multi-provedor, prompts versionados e saída validada."""

from llm.client import (
    STATUS_OK,
    STATUS_OUTPUT_INVALID,
    STATUS_UNAVAILABLE,
    LangChainLLMClient,
    LLMClient,
    LLMRunRecord,
    StructuredResult,
    get_llm_client,
)
from llm.embeddings import get_embedding_function, reset_embedding_cache
from llm.providers import LLMUnavailable, build_chat_model

__all__ = [
    "STATUS_OK",
    "STATUS_OUTPUT_INVALID",
    "STATUS_UNAVAILABLE",
    "LLMClient",
    "LLMRunRecord",
    "LLMUnavailable",
    "LangChainLLMClient",
    "StructuredResult",
    "build_chat_model",
    "get_embedding_function",
    "get_llm_client",
    "reset_embedding_cache",
]
