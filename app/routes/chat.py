"""
Chat endpoints with SSE streaming support.
"""

import asyncio
import logging

from fastapi import APIRouter, Request, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.models.schemas import ChatRequest, ChatResponse, RAGChatRequest, RAGChatResponse, RAGContext
from app.services.llm_service import (
    generate_response,
    generate_response_stream,
    generate_response_with_rag,
    generate_response_stream_with_rag,
)
from app.services import vector_store_service
from app.middleware.rate_limit import check_rate_limit
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/chat", tags=["chat"])


def validate_request(body: ChatRequest) -> None:
    """
    Validate chat request against cost protection limits.

    Raises:
        HTTPException: 400 Bad Request if validation fails
    """
    # Check message count
    if len(body.messages) > settings.max_messages_per_session:
        raise HTTPException(
            status_code=400,
            detail=f"Conversation too long. Maximum {settings.max_messages_per_session} messages allowed.",
        )

    # Check last user message length
    user_messages = [m for m in body.messages if m.role == "user"]
    if user_messages:
        last_message = user_messages[-1].content
        if len(last_message) > settings.max_input_length:
            raise HTTPException(
                status_code=400,
                detail=f"Message too long. Maximum {settings.max_input_length} characters allowed.",
            )


def truncate_messages(messages: list) -> list:
    """
    Truncate conversation to only include recent messages.
    Keeps the last N messages to reduce token usage.
    """
    if len(messages) <= settings.max_context_messages:
        return messages

    # Keep only the last N messages
    return messages[-settings.max_context_messages:]


@router.post("/stream")
async def chat_stream(request: Request, body: ChatRequest):
    """
    SSE streaming chat endpoint with RAG support.

    Automatically retrieves relevant context from the knowledge base.

    Streams tokens as Server-Sent Events:
    - event: token, data: <text chunk>
    - event: done (on completion)
    - event: error, data: <message> (on error)

    The stream automatically stops if the client disconnects,
    preventing wasted LLM compute.
    """
    # Apply rate limiting
    check_rate_limit(request)

    # Validate request
    validate_request(body)

    # Truncate messages to save tokens
    truncated_messages = truncate_messages(body.messages)

    # Get the last user message for context retrieval
    user_messages = [m for m in body.messages if m.role == "user"]
    query = user_messages[-1].content if user_messages else ""

    logger.info(f"Stream request: session={body.session_id}, locale={body.locale}, messages={len(body.messages)}, truncated={len(truncated_messages)}")

    async def event_generator():
        try:
            # Retrieve RAG context
            context_chunks = await retrieve_context(query, settings.rag_default_top_k, settings.rag_min_score_threshold)
            logger.info(f"Retrieved {len(context_chunks)} context chunks for stream")

            # Stream response with RAG context
            async for token in generate_response_stream_with_rag(truncated_messages, body.locale, context_chunks):
                # Check if client disconnected
                if await request.is_disconnected():
                    logger.info(f"Client disconnected: session={body.session_id}")
                    break

                yield {
                    "event": "token",
                    "data": token,
                }

            # Send done event if client still connected
            if not await request.is_disconnected():
                yield {
                    "event": "done",
                    "data": "",
                }
                logger.info(f"Stream complete: session={body.session_id}")

        except asyncio.CancelledError:
            # Client disconnected, graceful shutdown
            logger.info(f"Stream cancelled: session={body.session_id}")

        except Exception as e:
            import traceback
            logger.error(f"Stream error: session={body.session_id}")
            logger.error(traceback.format_exc())
            yield {
                "event": "error",
                "data": str(e),
            }

    return EventSourceResponse(event_generator())


@router.post("", response_model=ChatResponse)
async def chat(request: Request, body: ChatRequest) -> ChatResponse:
    """
    Non-streaming chat endpoint with RAG support.

    Automatically retrieves relevant context from the knowledge base.
    Returns the complete response in a single JSON object.
    """
    # Apply rate limiting
    check_rate_limit(request)

    # Validate request
    validate_request(body)

    # Truncate messages to save tokens
    truncated_messages = truncate_messages(body.messages)

    # Get the last user message for context retrieval
    user_messages = [m for m in body.messages if m.role == "user"]
    query = user_messages[-1].content if user_messages else ""

    logger.info(f"Chat request: session={body.session_id}, locale={body.locale}, messages={len(body.messages)}, truncated={len(truncated_messages)}")

    try:
        # Retrieve RAG context
        context_chunks = await retrieve_context(query, settings.rag_default_top_k, settings.rag_min_score_threshold)
        logger.info(f"Retrieved {len(context_chunks)} context chunks")

        # Generate response with RAG context
        reply = await generate_response_with_rag(truncated_messages, body.locale, context_chunks)
        logger.info(f"Chat complete: session={body.session_id}, reply_length={len(reply)}")

        return ChatResponse(
            reply=reply,
            session_id=body.session_id,
        )

    except Exception as e:
        import traceback
        logger.error(f"Chat error: session={body.session_id}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# RAG Chat Endpoints
# =============================================================================


async def retrieve_context(query: str, top_k: int, min_score: float) -> list[dict]:
    """
    Retrieve relevant context from vector store for a query.
    Uses Pinecone's integrated embeddings for text-based search.

    Args:
        query: User query text
        top_k: Number of results to retrieve
        min_score: Minimum similarity score threshold

    Returns:
        List of context chunks with metadata
    """
    # Query vector store with text (Pinecone handles embedding)
    matches = await vector_store_service.query_vectors(
        query_text=query,
        top_k=top_k,
        min_score=min_score,
    )

    # Extract context from matches
    context_chunks = []
    for match in matches:
        metadata = match.get("metadata", {})
        context_chunks.append({
            "document_id": metadata.get("document_id", ""),
            "filename": metadata.get("filename", ""),
            "text": metadata.get("text", ""),
            "score": match.get("score", 0.0),
        })

    return context_chunks


@router.post("/rag/stream")
async def chat_rag_stream(request: Request, body: RAGChatRequest):
    """
    SSE streaming chat endpoint with RAG context retrieval.

    First retrieves relevant context from the vector store,
    then streams the LLM response with context injected.

    Streams tokens as Server-Sent Events:
    - event: context, data: <JSON array of retrieved context>
    - event: token, data: <text chunk>
    - event: done (on completion)
    - event: error, data: <message> (on error)
    """
    # Apply rate limiting
    check_rate_limit(request)

    # Validate request (reuse existing validation)
    validate_request(ChatRequest(
        session_id=body.session_id,
        messages=body.messages,
        page_url=body.page_url,
        locale=body.locale,
    ))

    # Truncate messages to save tokens
    truncated_messages = truncate_messages(body.messages)

    # Get the last user message for context retrieval
    user_messages = [m for m in body.messages if m.role == "user"]
    query = user_messages[-1].content if user_messages else ""

    logger.info(f"RAG stream request: session={body.session_id}, locale={body.locale}, messages={len(body.messages)}")

    async def event_generator():
        try:
            # Retrieve context
            context_chunks = await retrieve_context(query, body.top_k, body.min_score)

            # Send context event
            import json
            context_data = [
                {
                    "document_id": c["document_id"],
                    "filename": c["filename"],
                    "text": c["text"][:500],  # Truncate for event
                    "score": round(c["score"], 4),
                }
                for c in context_chunks
            ]
            yield {
                "event": "context",
                "data": json.dumps(context_data),
            }

            # Stream response with RAG context
            async for token in generate_response_stream_with_rag(
                truncated_messages, body.locale, context_chunks
            ):
                # Check if client disconnected
                if await request.is_disconnected():
                    logger.info(f"Client disconnected: session={body.session_id}")
                    break

                yield {
                    "event": "token",
                    "data": token,
                }

            # Send done event if client still connected
            if not await request.is_disconnected():
                yield {
                    "event": "done",
                    "data": "",
                }
                logger.info(f"RAG stream complete: session={body.session_id}")

        except asyncio.CancelledError:
            logger.info(f"RAG stream cancelled: session={body.session_id}")

        except Exception as e:
            import traceback
            logger.error(f"RAG stream error: session={body.session_id}")
            logger.error(traceback.format_exc())
            yield {
                "event": "error",
                "data": str(e),
            }

    return EventSourceResponse(event_generator())


@router.post("/rag", response_model=RAGChatResponse)
async def chat_rag(request: Request, body: RAGChatRequest) -> RAGChatResponse:
    """
    Non-streaming RAG chat endpoint.

    Retrieves relevant context from the vector store,
    then generates a response with context injected.

    Returns the complete response along with retrieved context.
    """
    # Apply rate limiting
    check_rate_limit(request)

    # Validate request
    validate_request(ChatRequest(
        session_id=body.session_id,
        messages=body.messages,
        page_url=body.page_url,
        locale=body.locale,
    ))

    # Truncate messages to save tokens
    truncated_messages = truncate_messages(body.messages)

    # Get the last user message for context retrieval
    user_messages = [m for m in body.messages if m.role == "user"]
    query = user_messages[-1].content if user_messages else ""

    logger.info(f"RAG chat request: session={body.session_id}, locale={body.locale}, messages={len(body.messages)}")

    try:
        # Retrieve context
        context_chunks = await retrieve_context(query, body.top_k, body.min_score)

        # Generate response with RAG context
        reply = await generate_response_with_rag(
            truncated_messages, body.locale, context_chunks
        )

        logger.info(f"RAG chat complete: session={body.session_id}, reply_length={len(reply)}, context_count={len(context_chunks)}")

        # Build response with context
        context_response = [
            RAGContext(
                document_id=c["document_id"],
                filename=c["filename"],
                text=c["text"],
                score=c["score"],
            )
            for c in context_chunks
        ]

        return RAGChatResponse(
            reply=reply,
            session_id=body.session_id,
            context=context_response,
        )

    except Exception as e:
        import traceback
        logger.error(f"RAG chat error: session={body.session_id}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))
