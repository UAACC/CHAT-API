"""
LangChain LLM integration service.

Builds one chat model per tenant (cached), assembles the message list and
streams replies. Providers are selected by the tenant's `llm.provider`.
"""

import logging
from typing import AsyncGenerator, Optional

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.language_models import BaseChatModel

from app.models.schemas import ChatMessage
from app.tenants import LlmConfig, Tenant

logger = logging.getLogger(__name__)

_llm_cache: dict[str, BaseChatModel] = {}


def clear_llm_cache() -> None:
    _llm_cache.clear()


def build_llm(config: LlmConfig) -> BaseChatModel:
    """
    Construct a LangChain chat model from an LLM config.

    Raises:
        RuntimeError: If the API key is missing
        ValueError: If the provider is not supported
    """
    if not config.api_key:
        env_name = config.api_key_env or f"{config.provider.upper()}_API_KEY"
        raise RuntimeError(f"{env_name} environment variable is required")

    if config.provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=config.model,
            api_key=config.api_key,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            streaming=True,
        )

    if config.provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=config.model,
            api_key=config.api_key,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            streaming=True,
        )

    if config.provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        # Temperature is intentionally left at the model default: Google
        # recommends against lowering it on Gemini 3+ models.
        return ChatGoogleGenerativeAI(
            model=config.model,
            api_key=config.api_key,
            max_tokens=config.max_tokens,
        )

    raise ValueError(f"Unsupported LLM provider: {config.provider}")


def get_llm(tenant: Tenant) -> BaseChatModel:
    """The tenant's chat model, built on first use and cached."""
    llm = _llm_cache.get(tenant.id)
    if llm is None:
        llm = build_llm(tenant.llm)
        _llm_cache[tenant.id] = llm
    return llm


def chunk_text(chunk) -> str:
    """
    Extract plain text from a streamed message chunk.

    Providers may return content as a plain string or as a list of
    content blocks; only text blocks are forwarded to the client.
    """
    content = chunk.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type", "text") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    return ""


def build_system_prompt(tenant: Tenant, locale: str, context_chunks: Optional[list[dict]] = None) -> str:
    """The tenant's prompt for the locale, with retrieved context prepended when present."""
    prompt = tenant.prompt(locale)
    if not context_chunks:
        return prompt

    context_text = "\n\n---\n\n".join(
        f"[Source: {chunk.get('filename', 'Unknown')}]\n{chunk.get('text', '')}"
        for chunk in context_chunks
    )
    return (
        "The following information has been retrieved from the knowledge base "
        "to help answer the user's question:\n\n"
        f"<retrieved_context>\n{context_text}\n</retrieved_context>\n\n"
        "Use this context to inform your response when relevant. "
        "If the context doesn't contain the answer, say so clearly.\n\n---\n\n"
        f"{prompt}"
    )


def build_langchain_messages(
    messages: list[ChatMessage],
    locale: str,
    tenant: Tenant,
    context_chunks: Optional[list[dict]] = None,
) -> list:
    """
    Convert API messages to LangChain messages, led by the system prompt.

    Client-supplied system messages are dropped: the prompt is ours.
    """
    langchain_messages = [SystemMessage(content=build_system_prompt(tenant, locale, context_chunks))]
    for msg in messages:
        if msg.role == "user":
            langchain_messages.append(HumanMessage(content=msg.content))
        elif msg.role == "assistant":
            langchain_messages.append(AIMessage(content=msg.content))
    return langchain_messages


async def generate_response(
    messages: list[ChatMessage],
    locale: str,
    tenant: Tenant,
    context_chunks: Optional[list[dict]] = None,
) -> str:
    """Complete (non-streaming) reply."""
    llm = get_llm(tenant)
    langchain_messages = build_langchain_messages(messages, locale, tenant, context_chunks)
    logger.info(
        f"Generating response: tenant={tenant.id}, messages={len(messages)}, "
        f"context={len(context_chunks or [])}"
    )
    response = await llm.ainvoke(langchain_messages)
    return chunk_text(response)


async def generate_response_stream(
    messages: list[ChatMessage],
    locale: str,
    tenant: Tenant,
    context_chunks: Optional[list[dict]] = None,
) -> AsyncGenerator[str, None]:
    """Streaming reply, yielding text as it arrives."""
    llm = get_llm(tenant)
    langchain_messages = build_langchain_messages(messages, locale, tenant, context_chunks)
    logger.info(
        f"Streaming response: tenant={tenant.id}, messages={len(messages)}, "
        f"context={len(context_chunks or [])}"
    )
    async for chunk in llm.astream(langchain_messages):
        text = chunk_text(chunk)
        if text:
            yield text
