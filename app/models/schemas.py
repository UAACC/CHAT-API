"""
Pydantic models for API request/response schemas.
"""

from pydantic import BaseModel, Field
from typing import Literal, Optional


class ChatMessage(BaseModel):
    """Individual chat message in conversation history."""

    role: Literal["user", "assistant", "system"]
    content: str


class ChatRequest(BaseModel):
    """Request body for chat endpoints."""

    site: Optional[str] = Field(None, description="Tenant id; normally inferred from the Origin header")
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
    provider: str = Field(..., description="LLM provider of the default tenant")
    tenants: list[str] = Field(default_factory=list, description="Configured tenant ids")


# =============================================================================
# RAG Models
# =============================================================================


class DocumentUploadResponse(BaseModel):
    """Response body for document upload endpoint."""

    document_id: str = Field(..., description="Unique document identifier")
    filename: str = Field(..., description="Original filename")
    file_size: int = Field(..., description="File size in bytes")
    chunk_count: int = Field(..., description="Number of chunks created")
    vector_count: int = Field(..., description="Number of vectors stored")
    message: str = Field(default="Document processed successfully")


class DocumentInfo(BaseModel):
    """Document information model."""

    id: str = Field(..., description="Document ID")
    filename: str = Field(..., description="Original filename")
    size: int = Field(..., description="File size in bytes")
    content_type: Optional[str] = Field(None, description="MIME content type")
    created_at: Optional[str] = Field(None, description="Creation timestamp")
    vector_count: int = Field(0, description="Number of vectors")


class DocumentListResponse(BaseModel):
    """Response body for document list endpoint."""

    documents: list[DocumentInfo] = Field(default_factory=list)
    total: int = Field(..., description="Total number of documents")


class DocumentDetailResponse(BaseModel):
    """Response body for document detail endpoint."""

    id: str = Field(..., description="Document ID")
    filename: str = Field(..., description="Original filename")
    size: int = Field(..., description="File size in bytes")
    content_type: Optional[str] = Field(None, description="MIME content type")
    created_at: Optional[str] = Field(None, description="Creation timestamp")
    gcs_uri: Optional[str] = Field(None, description="GCS storage URI")
    vector_count: int = Field(0, description="Number of vectors")


class DocumentDeleteResponse(BaseModel):
    """Response body for document delete endpoint."""

    document_id: str = Field(..., description="Deleted document ID")
    message: str = Field(default="Document deleted successfully")


class RAGChatRequest(ChatRequest):
    """Chat request with caller-controlled retrieval depth."""

    top_k: int = Field(5, description="Number of context chunks to retrieve", ge=1, le=20)
    min_score: float = Field(0.1, description="Minimum similarity score", ge=0.0, le=1.0)


class RAGContext(BaseModel):
    """Retrieved context information."""

    document_id: str = Field(..., description="Source document ID")
    filename: str = Field(..., description="Source filename")
    text: str = Field(..., description="Retrieved text chunk")
    score: float = Field(..., description="Similarity score")


class RAGChatResponse(BaseModel):
    """Response body for non-streaming RAG chat endpoint."""

    reply: str = Field(..., description="Assistant's response")
    session_id: str = Field(..., description="Session identifier echoed back")
    context: list[RAGContext] = Field(default_factory=list, description="Retrieved context")


class RAGServiceStatus(BaseModel):
    """Status of a RAG service component."""

    status: str = Field(..., description="Health status (healthy/unhealthy)")
    error: Optional[str] = Field(None, description="Error message if unhealthy")


class RAGStatusResponse(BaseModel):
    """Response body for RAG status endpoint."""

    status: str = Field(..., description="Overall RAG system status")
    storage: RAGServiceStatus = Field(..., description="GCS status")
    vector_store: RAGServiceStatus = Field(..., description="Pinecone status")
    embedding: RAGServiceStatus = Field(..., description="Embedding service status")
