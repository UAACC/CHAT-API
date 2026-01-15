"""
Pydantic models for API request/response schemas.
"""

from pydantic import BaseModel, Field
from typing import Literal


class ChatMessage(BaseModel):
    """Individual chat message in conversation history."""

    role: Literal["user", "assistant", "system"]
    content: str


class ChatRequest(BaseModel):
    """Request body for chat endpoints."""

    session_id: str = Field(..., description="Unique session identifier from client")
    messages: list[ChatMessage] = Field(..., description="Conversation history")
    page_url: str = Field("", description="Current page URL for context")
    locale: Literal["en", "zh"] = Field("en", description="Response language preference")


class ChatResponse(BaseModel):
    """Response body for non-streaming chat endpoint."""

    reply: str = Field(..., description="Assistant's response")
    session_id: str = Field(..., description="Session identifier echoed back")


class HealthResponse(BaseModel):
    """Response body for health check endpoint."""

    status: str = "healthy"
    version: str = "1.0.0"
    provider: str = Field(..., description="Current LLM provider")
