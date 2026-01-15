"""
LangChain LLM integration service.
Supports multiple providers with streaming capability.
"""

import logging
from typing import AsyncGenerator

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.language_models import BaseChatModel

from app.config import get_settings
from app.prompts.system_prompts import get_system_prompt
from app.models.schemas import ChatMessage

logger = logging.getLogger(__name__)


def get_llm() -> BaseChatModel:
    """
    Factory function to get the configured LLM provider.

    Returns:
        Configured LangChain chat model instance

    Raises:
        ValueError: If unsupported provider is configured
        RuntimeError: If API key is missing for the provider
    """
    settings = get_settings()

    if settings.llm_provider == "openai":
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY environment variable is required")

        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            max_tokens=settings.max_tokens,
            temperature=settings.temperature,
            streaming=True,
        )

    elif settings.llm_provider == "anthropic":
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY environment variable is required")

        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=settings.anthropic_model,
            api_key=settings.anthropic_api_key,
            max_tokens=settings.max_tokens,
            temperature=settings.temperature,
            streaming=True,
        )

    else:
        raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")


def build_langchain_messages(messages: list[ChatMessage], locale: str) -> list:
    """
    Convert API messages to LangChain message format.

    Args:
        messages: List of chat messages from API request
        locale: Language locale for system prompt selection

    Returns:
        List of LangChain message objects
    """
    # Start with system prompt
    langchain_messages = [SystemMessage(content=get_system_prompt(locale))]

    # Add conversation history
    for msg in messages:
        if msg.role == "user":
            langchain_messages.append(HumanMessage(content=msg.content))
        elif msg.role == "assistant":
            langchain_messages.append(AIMessage(content=msg.content))
        # Skip system messages from client - we use our own

    return langchain_messages


async def generate_response(messages: list[ChatMessage], locale: str) -> str:
    """
    Generate a complete response (non-streaming).

    Args:
        messages: Conversation history
        locale: Response language preference

    Returns:
        Complete response string
    """
    llm = get_llm()
    langchain_messages = build_langchain_messages(messages, locale)

    logger.info(f"Generating response for {len(messages)} messages in locale: {locale}")

    response = await llm.ainvoke(langchain_messages)
    return response.content


async def generate_response_stream(
    messages: list[ChatMessage],
    locale: str,
) -> AsyncGenerator[str, None]:
    """
    Generate a streaming response.

    Args:
        messages: Conversation history
        locale: Response language preference

    Yields:
        Response tokens/chunks as they are generated
    """
    llm = get_llm()
    langchain_messages = build_langchain_messages(messages, locale)

    logger.info(f"Streaming response for {len(messages)} messages in locale: {locale}")

    async for chunk in llm.astream(langchain_messages):
        if chunk.content:
            yield chunk.content
