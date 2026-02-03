"""
RAG document management endpoints.
"""

import logging
from fastapi import APIRouter, HTTPException, UploadFile, File

from app.models.schemas import (
    DocumentUploadResponse,
    DocumentListResponse,
    DocumentDetailResponse,
    DocumentDeleteResponse,
    DocumentInfo,
    RAGStatusResponse,
    RAGServiceStatus,
)
from app.services import (
    document_service,
    storage_service,
    vector_store_service,
)
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/rag", tags=["rag"])


@router.post("/documents/upload", response_model=DocumentUploadResponse)
async def upload_document(file: UploadFile = File(...)):
    """
    Upload and process a document for RAG.

    Accepts PDF, TXT, and Markdown files.
    The document will be:
    1. Stored in Google Cloud Storage
    2. Parsed and chunked
    3. Embedded and stored in Pinecone

    If a document with the same filename exists, it will be replaced.
    Maximum file size is configurable via RAG_MAX_FILE_SIZE_MB.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")

    # Read file content
    content = await file.read()

    if not content:
        raise HTTPException(status_code=400, detail="File is empty")

    # Check for existing document with same filename and delete it
    try:
        existing_docs = await storage_service.list_documents()
        for doc in existing_docs:
            if doc.get("filename") == file.filename:
                logger.info(f"Found existing document with same filename: {doc['id']}, deleting...")
                await document_service.delete_document(doc["id"])
                logger.info(f"Deleted existing document: {doc['id']}")
    except Exception as e:
        logger.warning(f"Error checking for existing documents: {e}")

    # Generate document ID
    document_id = document_service.generate_document_id()

    logger.info(f"Processing upload: {file.filename} ({len(content)} bytes)")

    try:
        result = await document_service.process_document(
            document_id=document_id,
            filename=file.filename,
            content=content,
            content_type=file.content_type or "application/octet-stream",
        )

        return DocumentUploadResponse(
            document_id=result["document_id"],
            filename=result["filename"],
            file_size=result["file_size"],
            chunk_count=result["chunk_count"],
            vector_count=result["vector_count"],
        )

    except ValueError as e:
        logger.warning(f"Upload validation error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        logger.error(f"Upload processing error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to process document: {str(e)}")


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents():
    """
    List all uploaded documents.

    Returns document metadata including filename, size, and vector count.
    """
    try:
        docs = await storage_service.list_documents()

        # Enrich with vector counts
        document_infos = []
        for doc in docs:
            vector_count = await vector_store_service.get_document_vector_count(doc["id"])
            document_infos.append(
                DocumentInfo(
                    id=doc["id"],
                    filename=doc["filename"],
                    size=doc.get("size", 0),
                    content_type=doc.get("content_type"),
                    created_at=doc.get("created_at"),
                    vector_count=vector_count,
                )
            )

        return DocumentListResponse(
            documents=document_infos,
            total=len(document_infos),
        )

    except Exception as e:
        logger.error(f"Error listing documents: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list documents: {str(e)}")


@router.get("/documents/{document_id}", response_model=DocumentDetailResponse)
async def get_document(document_id: str):
    """
    Get details of a specific document.

    Returns full document metadata including GCS URI and vector count.
    """
    try:
        doc = await storage_service.get_document_info(document_id)

        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        vector_count = await vector_store_service.get_document_vector_count(document_id)

        return DocumentDetailResponse(
            id=doc["id"],
            filename=doc["filename"],
            size=doc.get("size", 0),
            content_type=doc.get("content_type"),
            created_at=doc.get("created_at"),
            gcs_uri=doc.get("gcs_uri"),
            vector_count=vector_count,
        )

    except HTTPException:
        raise

    except Exception as e:
        logger.error(f"Error getting document {document_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get document: {str(e)}")


@router.delete("/documents/{document_id}", response_model=DocumentDeleteResponse)
async def delete_document(document_id: str):
    """
    Delete a document and all its associated data.

    Removes:
    - Original file from GCS
    - All vectors from Pinecone
    """
    try:
        # Check if document exists
        doc = await storage_service.get_document_info(document_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        # Delete document
        await document_service.delete_document(document_id)

        return DocumentDeleteResponse(document_id=document_id)

    except HTTPException:
        raise

    except Exception as e:
        logger.error(f"Error deleting document {document_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete document: {str(e)}")


@router.get("/status", response_model=RAGStatusResponse)
async def get_rag_status():
    """
    Check RAG system health.

    Returns status of:
    - Google Cloud Storage
    - Pinecone vector store (with integrated embeddings)
    """
    storage_status = storage_service.check_storage_health()
    vector_status = vector_store_service.check_vector_store_health()

    # Determine overall status
    all_healthy = (
        storage_status.get("status") == "healthy"
        and vector_status.get("status") == "healthy"
    )

    return RAGStatusResponse(
        status="healthy" if all_healthy else "degraded",
        storage=RAGServiceStatus(
            status=storage_status.get("status", "unknown"),
            error=storage_status.get("error"),
        ),
        vector_store=RAGServiceStatus(
            status=vector_status.get("status", "unknown"),
            error=vector_status.get("error"),
        ),
        embedding=RAGServiceStatus(
            status="integrated",  # Embeddings handled by Pinecone
            error=None,
        ),
    )
