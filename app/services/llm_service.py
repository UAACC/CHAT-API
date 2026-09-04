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

    elif settings.llm_provider == "gemini":
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY environment variable is required")

        from langchain_google_genai import ChatGoogleGenerativeAI

        # Temperature is intentionally left at the model default: Google
        # recommends against lowering it on Gemini 3+ models.
        return ChatGoogleGenerativeAI(
            model=settings.gemini_model,
            api_key=settings.gemini_api_key,
            max_tokens=settings.max_tokens,
        )

    else:
        raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")


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


def build_langchain_messages_with_rag(
    messages: list[ChatMessage],
    locale: str,
    context_chunks: list[dict],
) -> list:
    """
    Convert API messages to LangChain message format with RAG context.

    The retrieved context is prepended to the system prompt to provide
    relevant information for answering user queries.

    Args:
        messages: List of chat messages from API request
        locale: Language locale for system prompt selection
        context_chunks: List of retrieved context dicts with 'text', 'filename', 'score'

    Returns:
        List of LangChain message objects with RAG context
    """
    # Build RAG context section
    if context_chunks:
        context_text = "\n\n---\n\n".join([
            f"[Source: {chunk.get('filename', 'Unknown')}]\n{chunk.get('text', '')}"
            for chunk in context_chunks
        ])
        rag_prefix = f"""The following information has been retrieved from the knowledge base to help answer the user's question:

<retrieved_context>
{context_text}
</retrieved_context>

Use this context to inform your response when relevant. If the context doesn't contain the answer, say so clearly.

---

"""
    else:
        rag_prefix = ""

    # Combine RAG context with system prompt
    system_prompt = get_system_prompt(locale)
    enhanced_system_prompt = rag_prefix + system_prompt

    langchain_messages = [SystemMessage(content=enhanced_system_prompt)]

    # Add conversation history
    for msg in messages:
        if msg.role == "user":
            langchain_messages.append(HumanMessage(content=msg.content))
        elif msg.role == "assistant":
            langchain_messages.append(AIMessage(content=msg.content))

    return langchain_messages


async def generate_response_with_rag(
    messages: list[ChatMessage],
    locale: str,
    context_chunks: list[dict],
) -> str:
    """
    Generate a complete response with RAG context (non-streaming).

    Args:
        messages: Conversation history
        locale: Response language preference
        context_chunks: Retrieved context from vector store

    Returns:
        Complete response string
    """
    llm = get_llm()
    langchain_messages = build_langchain_messages_with_rag(messages, locale, context_chunks)

    logger.info(f"Generating RAG response for {len(messages)} messages with {len(context_chunks)} context chunks")

    response = await llm.ainvoke(langchain_messages)
    return response.content


async def generate_response_stream_with_rag(
    messages: list[ChatMessage],
    locale: str,
    context_chunks: list[dict],
) -> AsyncGenerator[str, None]:
    """
    Generate a streaming response with RAG context.

    Args:
        messages: Conversation history
        locale: Response language preference
        context_chunks: Retrieved context from vector store

    Yields:
        Response tokens/chunks as they are generated
    """
    llm = get_llm()
    langchain_messages = build_langchain_messages_with_rag(messages, locale, context_chunks)

    logger.info(f"Streaming RAG response for {len(messages)} messages with {len(context_chunks)} context chunks")

    async for chunk in llm.astream(langchain_messages):
        text = chunk_text(chunk)
        if text:
            yield text


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
        text = chunk_text(chunk)
        if text:
            yield text
