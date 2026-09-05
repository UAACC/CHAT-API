"""
Chat endpoints with SSE streaming support.

Every request is resolved to a tenant, rate limited, validated, trimmed to the
recent history, optionally enriched from the tenant's knowledge base, and
answered by the tenant's model.
"""

import asyncio
import logging
import traceback
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from app.config import Settings, get_settings
from app.middleware.rate_limit import check_rate_limit
from app.models.schemas import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    RAGChatRequest,
    RAGChatResponse,
    RAGContext,
)
from app.services import vector_store_service
from app.services.llm_service import generate_response, generate_response_stream
from app.tenants import Tenant, TenantRegistry, get_registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


def validate_request(body: ChatRequest, tenant: Tenant, settings: Optional[Settings] = None) -> None:
    """
    Enforce the cost-protection limits.

    Raises:
        HTTPException: 400 Bad Request if validation fails
    """
    settings = settings or get_settings()

    if len(body.messages) > settings.max_messages_per_session:
        raise HTTPException(
            status_code=400,
            detail=f"Conversation too long. Maximum {settings.max_messages_per_session} messages allowed.",
        )

    user_messages = [m for m in body.messages if m.role == "user"]
    if user_messages:
        limit = tenant.max_input_length(settings)
        if len(user_messages[-1].content) > limit:
            raise HTTPException(
                status_code=400,
                detail=f"Message too long. Maximum {limit} characters allowed.",
            )


def truncate_messages(messages: list, max_context: Optional[int] = None) -> list:
    """Keep only the most recent messages to bound token usage."""
    if max_context is None:
        max_context = get_settings().max_context_messages
    if len(messages) <= max_context:
        return messages
    return messages[-max_context:]


def last_user_query(messages: list[ChatMessage]) -> str:
    user_messages = [m for m in messages if m.role == "user"]
    return user_messages[-1].content if user_messages else ""


async def retrieve_context(query: str, tenant: Tenant, top_k: int, min_score: float) -> list[dict]:
    """
    Retrieve knowledge-base context for a query.

    Retrieval is optional: tenants without a knowledge base, deployments
    without a Pinecone key, and vector-store outages all yield no context
    rather than an error.
    """
    settings = get_settings()
    if not tenant.knowledge_base or not settings.pinecone_api_key or not query:
        return []

    try:
        matches = await vector_store_service.query_vectors(
            query_text=query,
            top_k=top_k,
            min_score=min_score,
            namespace=tenant.knowledge_base.namespace,
        )
    except Exception as e:
        logger.warning(f"Context retrieval failed, continuing without RAG: {e}")
        return []

    return [
        {
            "document_id": m.get("metadata", {}).get("document_id", ""),
            "filename": m.get("metadata", {}).get("filename", ""),
            "text": m.get("metadata", {}).get("text", ""),
            "score": m.get("score", 0.0),
        }
        for m in matches
    ]


def _prepare(request: Request, body: ChatRequest, registry: TenantRegistry) -> tuple[Tenant, list[ChatMessage], str]:
    """Shared front half of every chat endpoint."""
    tenant = registry.resolve(request, body.site)
    check_rate_limit(request, tenant)
    validate_request(body, tenant)
    truncated = truncate_messages(body.messages)
    logger.info(
        f"Chat request: tenant={tenant.id}, session={body.session_id}, locale={body.locale}, "
        f"messages={len(body.messages)}, truncated={len(truncated)}"
    )
    return tenant, truncated, last_user_query(body.messages)


def _sse_response(request: Request, tenant: Tenant, body: ChatRequest, messages: list[ChatMessage],
                  query: str, top_k: int, min_score: float) -> EventSourceResponse:
    """Stream tokens as SSE, stopping early if the client goes away."""

    async def event_generator():
        try:
            context_chunks = await retrieve_context(query, tenant, top_k, min_score)
            logger.info(f"Retrieved {len(context_chunks)} context chunks for stream")

            async for token in generate_response_stream(messages, body.locale, tenant, context_chunks):
                if await request.is_disconnected():
                    logger.info(f"Client disconnected: session={body.session_id}")
                    break
                yield {"event": "token", "data": token}

            if not await request.is_disconnected():
                yield {"event": "done", "data": ""}
                logger.info(f"Stream complete: session={body.session_id}")

        except asyncio.CancelledError:
            logger.info(f"Stream cancelled: session={body.session_id}")

        except Exception as e:
            logger.error(f"Stream error: session={body.session_id}\n{traceback.format_exc()}")
            yield {"event": "error", "data": str(e)}

    return EventSourceResponse(event_generator())


@router.post("/stream")
async def chat_stream(request: Request, body: ChatRequest, registry: TenantRegistry = Depends(get_registry)):
    """
    SSE streaming chat endpoint.

    Events: `token` (text chunk), `done` (completion), `error` (message).
    The stream stops if the client disconnects, so abandoned tabs do not
    keep consuming model output.
    """
    tenant, messages, query = _prepare(request, body, registry)
    settings = get_settings()
    return _sse_response(request, tenant, body, messages, query,
                         settings.rag_default_top_k, settings.rag_min_score_threshold)


@router.post("", response_model=ChatResponse)
async def chat(request: Request, body: ChatRequest, registry: TenantRegistry = Depends(get_registry)) -> ChatResponse:
    """Non-streaming chat endpoint: the complete reply in one JSON body."""
    tenant, messages, query = _prepare(request, body, registry)
    settings = get_settings()

    try:
        context_chunks = await retrieve_context(query, tenant, settings.rag_default_top_k, settings.rag_min_score_threshold)
        reply = await generate_response(messages, body.locale, tenant, context_chunks)
        logger.info(f"Chat complete: session={body.session_id}, reply_length={len(reply)}")
        return ChatResponse(reply=reply, session_id=body.session_id)

    except Exception as e:
        logger.error(f"Chat error: session={body.session_id}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rag/stream")
async def chat_rag_stream(request: Request, body: RAGChatRequest, registry: TenantRegistry = Depends(get_registry)):
    """Streaming chat with caller-controlled retrieval depth (`top_k`, `min_score`)."""
    tenant, messages, query = _prepare(request, body, registry)
    return _sse_response(request, tenant, body, messages, query, body.top_k, body.min_score)


@router.post("/rag", response_model=RAGChatResponse)
async def chat_rag(request: Request, body: RAGChatRequest, registry: TenantRegistry = Depends(get_registry)) -> RAGChatResponse:
    """Non-streaming chat that also returns the retrieved context."""
    tenant, messages, query = _prepare(request, body, registry)

    try:
        context_chunks = await retrieve_context(query, tenant, body.top_k, body.min_score)
        reply = await generate_response(messages, body.locale, tenant, context_chunks)
        return RAGChatResponse(
            reply=reply,
            session_id=body.session_id,
            context=[RAGContext(**c) for c in context_chunks],
        )

    except Exception as e:
        logger.error(f"RAG chat error: session={body.session_id}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))
