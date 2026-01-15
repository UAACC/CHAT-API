"""
Chat endpoints with SSE streaming support.
"""

import asyncio
import logging

from fastapi import APIRouter, Request, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.models.schemas import ChatRequest, ChatResponse
from app.services.llm_service import generate_response, generate_response_stream
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
    SSE streaming chat endpoint.

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

    logger.info(f"Stream request: session={body.session_id}, locale={body.locale}, messages={len(body.messages)}, truncated={len(truncated_messages)}")

    async def event_generator():
        try:
            async for token in generate_response_stream(truncated_messages, body.locale):
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
    Non-streaming chat endpoint (fallback).

    Returns the complete response in a single JSON object.
    Use this endpoint if SSE streaming is not supported.
    """
    # Apply rate limiting
    check_rate_limit(request)

    # Validate request
    validate_request(body)

    # Truncate messages to save tokens
    truncated_messages = truncate_messages(body.messages)

    logger.info(f"Chat request: session={body.session_id}, locale={body.locale}, messages={len(body.messages)}, truncated={len(truncated_messages)}")

    try:
        reply = await generate_response(truncated_messages, body.locale)
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
