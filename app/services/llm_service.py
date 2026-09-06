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


def get_llm(tenant: Tenant, candidate: int = 0) -> BaseChatModel:
    """The tenant's chat model (primary or a fallback), built on first use and cached."""
    key = f"{tenant.id}#{candidate}"
    llm = _llm_cache.get(key)
    if llm is None:
        llm = build_llm(tenant.llm_candidates[candidate])
        _llm_cache[key] = llm
    return llm


_FALLBACK_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}
_FALLBACK_MARKERS = (
    "rate limit", "ratelimit", "resource_exhausted", "quota", "credit",
    "overloaded", "unavailable", "high demand", "not found", "no longer available",
    "internal server", "server error", "timeout", "timed out", "connection",
    "503", "502", "504", "429",
)


def should_fall_back(error: BaseException) -> bool:
    """
    Would a different model plausibly succeed?

    True for overload, rate limits, exhausted quota or credit, withdrawn
    models, upstream 5xx and connection failures. False for errors a second
    model would repeat: invalid requests, content policy, authentication.
    """
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    if isinstance(status, int):
        if status in _FALLBACK_STATUSES:
            return True
        if status in (400, 401, 403, 422):
            return False
    haystack = f"{type(error).__name__} {error}".lower()
    if any(word in haystack for word in ("invalid_argument", "permission_denied", "unauthenticated", "api key not valid", "invalid api key", "content policy", "safety")):
        return False
    return any(marker in haystack for marker in _FALLBACK_MARKERS)


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
    """Complete (non-streaming) reply, trying fallback models on retryable failures."""
    langchain_messages = build_langchain_messages(messages, locale, tenant, context_chunks)
    logger.info(
        f"Generating response: tenant={tenant.id}, messages={len(messages)}, "
        f"context={len(context_chunks or [])}"
    )
    candidates = tenant.llm_candidates
    for index, config in enumerate(candidates):
        try:
            response = await get_llm(tenant, index).ainvoke(langchain_messages)
            return chunk_text(response)
        except Exception as error:
            if index == len(candidates) - 1 or not should_fall_back(error):
                raise
            _log_fallback(tenant, config, candidates[index + 1], error)
    raise RuntimeError("no model candidates configured")


def _log_fallback(tenant: Tenant, failed: LlmConfig, next_config: LlmConfig, error: BaseException) -> None:
    logger.warning(
        f"tenant={tenant.id} fell back from {failed.provider}/{failed.model} "
        f"to {next_config.provider}/{next_config.model}: {type(error).__name__}: {str(error)[:200]}"
    )


async def generate_response_stream(
    messages: list[ChatMessage],
    locale: str,
    tenant: Tenant,
    context_chunks: Optional[list[dict]] = None,
) -> AsyncGenerator[str, None]:
    """
    Streaming reply, yielding text as it arrives.

    Falls back to the next configured model when the current one fails before
    producing any text. Once text has been streamed the request stays on that
    model, so a reply is never spliced from two models.
    """
    langchain_messages = build_langchain_messages(messages, locale, tenant, context_chunks)
    logger.info(
        f"Streaming response: tenant={tenant.id}, messages={len(messages)}, "
        f"context={len(context_chunks or [])}"
    )
    candidates = tenant.llm_candidates
    for index, config in enumerate(candidates):
        produced = False
        try:
            async for chunk in get_llm(tenant, index).astream(langchain_messages):
                text = chunk_text(chunk)
                if text:
                    produced = True
                    yield text
            return
        except Exception as error:
            if produced or index == len(candidates) - 1 or not should_fall_back(error):
                raise
            _log_fallback(tenant, config, candidates[index + 1], error)
