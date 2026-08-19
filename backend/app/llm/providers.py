"""
Construção do modelo de chat por provedor (diretriz §35).

Este é o **único** módulo do backend autorizado a perguntar qual é o provedor.
A camada de domínio recebe um `LLMClient` já pronto e não sabe se por trás dele
está OpenAI, Groq, OpenRouter, Gemini ou um Ollama local — que é a condição para
a §35.2 valer (modelo aberto como caminho de primeira classe) e para a §28
(trocar de modelo não pode mudar regra nenhuma).

Os imports das integrações são locais a cada ramo de propósito: carregar o
`langchain_huggingface` puxa o stack do sentence-transformers, e não há motivo
para pagar isso quando o provedor configurado é outro.
"""

import logging
from typing import Any

from config import Settings, get_settings

logger = logging.getLogger("uvicorn.error")


class LLMUnavailable(RuntimeError):
    """Provedor mal configurado ou inacessível (status LLM_UNAVAILABLE da §26)."""


def build_chat_model(settings: Settings | None = None) -> Any:
    """Instancia o modelo de chat do provedor configurado."""
    settings = settings or get_settings()
    provider = settings.llm_provider

    common = {"temperature": settings.llm_temperature}

    try:
        if provider == "groq":
            from langchain_groq import ChatGroq

            return ChatGroq(
                groq_api_key=settings.llm_api_key,
                model_name=settings.llm_model,
                max_tokens=settings.llm_max_tokens,
                **common,
            )

        if provider == "openrouter":
            from langchain_openai import ChatOpenAI

            # Endpoint compatível com a API da OpenAI — daí reusar ChatOpenAI.
            return ChatOpenAI(
                openai_api_key=settings.llm_api_key,
                openai_api_base="https://openrouter.ai/api/v1",
                model_name=settings.llm_model,
                max_tokens=settings.llm_max_tokens,
                **common,
            )

        if provider == "gemini":
            from langchain_google_genai import ChatGoogleGenerativeAI

            return ChatGoogleGenerativeAI(
                google_api_key=settings.llm_api_key,
                model=settings.llm_model,
                max_output_tokens=settings.llm_max_tokens,
                **common,
            )

        if provider == "ollama":
            from langchain_ollama import ChatOllama

            # Sem max_tokens: no Ollama o limite equivalente é `num_predict`, e
            # deixá-lo no default do servidor é o comportamento que já valia.
            return ChatOllama(
                base_url=settings.ollama_base_url,
                model=settings.llm_model,
                **common,
            )

        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            openai_api_key=settings.llm_api_key,
            model_name=settings.llm_model,
            max_tokens=settings.llm_max_tokens,
            **common,
        )
    except ImportError as exc:
        raise LLMUnavailable(
            f"Integração do provedor {provider!r} não instalada: {exc}."
        ) from exc
    except Exception as exc:  # configuração inválida (chave ausente, URL errada)
        raise LLMUnavailable(
            f"Não foi possível inicializar o provedor {provider!r}: {exc}."
        ) from exc
