"""
Knowledge-base document management endpoints.

Documents belong to a tenant: they are stored under the tenant's storage
prefix and indexed in the tenant's Pinecone namespace. The tenant is chosen
with the `site` query parameter, or inferred from the Origin header, or the
default tenant.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile

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
from app.tenants import KnowledgeBaseConfig, Tenant, TenantRegistry, get_registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rag", tags=["rag"])


def knowledge_base_for(
    request: Request,
    site: Optional[str] = Query(None, description="Tenant id; inferred from Origin when omitted"),
    registry: TenantRegistry = Depends(get_registry),
) -> tuple[Tenant, KnowledgeBaseConfig]:
    """Resolve the tenant and require it to have a knowledge base."""
    tenant = registry.resolve(request, site)
    if tenant.knowledge_base is None:
        raise HTTPException(status_code=400, detail=f"Site {tenant.id!r} has no knowledge base configured")
    return tenant, tenant.knowledge_base


@router.post("/documents/upload", response_model=DocumentUploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    kb: tuple[Tenant, KnowledgeBaseConfig] = Depends(knowledge_base_for),
):
    """
    Upload and index a document (PDF, TXT or Markdown).

    The file is stored, parsed, chunked and embedded. An existing document
    with the same filename for this site is replaced. Maximum size is
    RAG_MAX_FILE_SIZE_MB.
    """
    tenant, config = kb

    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="File is empty")

    try:
        existing_docs = await storage_service.list_documents(config.storage_prefix)
        for doc in existing_docs:
            if doc.get("filename") == file.filename:
                logger.info(f"Replacing existing document {doc['id']} for {tenant.id}")
                await document_service.delete_document(doc["id"], config.namespace, config.storage_prefix)
    except Exception as e:
        logger.warning(f"Error checking for existing documents: {e}")

    document_id = document_service.generate_document_id()
    logger.info(f"Processing upload for {tenant.id}: {file.filename} ({len(content)} bytes)")

    try:
        result = await document_service.process_document(
            document_id=document_id,
            filename=file.filename,
            content=content,
            content_type=file.content_type or "application/octet-stream",
            namespace=config.namespace,
            storage_prefix=config.storage_prefix,
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
async def list_documents(kb: tuple[Tenant, KnowledgeBaseConfig] = Depends(knowledge_base_for)):
    """List the site's indexed documents."""
    _, config = kb
    try:
        docs = await storage_service.list_documents(config.storage_prefix)
        document_infos = []
        for doc in docs:
            vector_count = await vector_store_service.get_document_vector_count(doc["id"], config.namespace)
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
        return DocumentListResponse(documents=document_infos, total=len(document_infos))

    except Exception as e:
        logger.error(f"Error listing documents: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list documents: {str(e)}")


@router.get("/documents/{document_id}", response_model=DocumentDetailResponse)
async def get_document(document_id: str, kb: tuple[Tenant, KnowledgeBaseConfig] = Depends(knowledge_base_for)):
    """Details for one document, including its storage URI and vector count."""
    _, config = kb
    try:
        doc = await storage_service.get_document_info(document_id, config.storage_prefix)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        vector_count = await vector_store_service.get_document_vector_count(document_id, config.namespace)
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
async def delete_document(document_id: str, kb: tuple[Tenant, KnowledgeBaseConfig] = Depends(knowledge_base_for)):
    """Remove a document: its stored file and all of its vectors."""
    _, config = kb
    try:
        doc = await storage_service.get_document_info(document_id, config.storage_prefix)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        await document_service.delete_document(document_id, config.namespace, config.storage_prefix)
        return DocumentDeleteResponse(document_id=document_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting document {document_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete document: {str(e)}")


@router.get("/status", response_model=RAGStatusResponse)
async def get_rag_status():
    """Connectivity of the storage bucket and the vector store."""
    storage_status = storage_service.check_storage_health()
    vector_status = vector_store_service.check_vector_store_health()

    all_healthy = (
        storage_status.get("status") == "healthy"
        and vector_status.get("status") == "healthy"
    )

    return RAGStatusResponse(
        status="healthy" if all_healthy else "degraded",
        storage=RAGServiceStatus(status=storage_status.get("status", "unknown"), error=storage_status.get("error")),
        vector_store=RAGServiceStatus(status=vector_status.get("status", "unknown"), error=vector_status.get("error")),
        embedding=RAGServiceStatus(status="integrated", error=None),
    )
