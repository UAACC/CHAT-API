"""
Knowledge-base document management endpoints.

Documents belong to a tenant: they are stored under the tenant's storage
prefix and indexed in the tenant's Pinecone namespace. The tenant is chosen
with the `site` query parameter, or inferred from the Origin header, or the
default tenant.
"""

import hmac
import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile

from app.config import get_settings
from app.models.schemas import (
    ChunkInfo,
    CrawlRequest,
    CrawlResponse,
    CrawledPage,
    DocumentChunksResponse,
    DocumentUploadResponse,
    DocumentListResponse,
    DocumentDetailResponse,
    DocumentDeleteResponse,
    DocumentInfo,
    NotesResponse,
    NotesUpdate,
    RAGStatusResponse,
    RAGServiceStatus,
    SearchHit,
    SearchResponse,
)
from app.services import (
    crawler,
    document_service,
    storage_service,
    vector_store_service,
)
from app.tenants import KnowledgeBaseConfig, Tenant, TenantRegistry, get_registry

logger = logging.getLogger(__name__)


def require_admin(authorization: Optional[str] = Header(None)) -> None:
    """
    Guard for the knowledge-base endpoints.

    Enforced only when ADMIN_TOKEN is configured; otherwise open (the app
    warns at startup).
    """
    expected = get_settings().admin_token
    if not expected:
        return
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(token.strip(), expected):
        raise HTTPException(status_code=401, detail="admin token required", headers={"WWW-Authenticate": "Bearer"})


# Everything that reads or changes a knowledge base is behind the admin token.
router = APIRouter(prefix="/rag", tags=["rag"], dependencies=[Depends(require_admin)])
# Monitoring stays open.
status_router = APIRouter(prefix="/rag", tags=["rag"])


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


@router.get("/documents/{document_id}/chunks", response_model=DocumentChunksResponse)
async def document_chunks(document_id: str, kb: tuple[Tenant, KnowledgeBaseConfig] = Depends(knowledge_base_for)):
    """Every stored chunk of a document, in order: what the assistant can actually retrieve."""
    _, config = kb
    try:
        records = await vector_store_service.get_document_records(document_id, config.namespace)
    except Exception as e:
        logger.error(f"Error reading chunks for {document_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to read chunks: {str(e)}")
    return DocumentChunksResponse(document_id=document_id, chunks=[ChunkInfo(**r) for r in records])


@router.get("/search", response_model=SearchResponse)
async def search(
    q: str = Query(..., min_length=1, max_length=500),
    top_k: int = Query(5, ge=1, le=20),
    kb: tuple[Tenant, KnowledgeBaseConfig] = Depends(knowledge_base_for),
):
    """Retrieval test: the chunks a question would pull in, with scores, no model call."""
    _, config = kb
    threshold = get_settings().rag_min_score_threshold
    try:
        matches = await vector_store_service.query_vectors(
            query_text=q, top_k=top_k, min_score=0.0, namespace=config.namespace
        )
    except Exception as e:
        logger.error(f"Search failed: {e}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")
    hits = [
        SearchHit(
            id=m["id"],
            score=m["score"],
            used=m["score"] >= threshold,
            document_id=m["metadata"].get("document_id", ""),
            filename=m["metadata"].get("filename", ""),
            text=m["metadata"].get("text", ""),
        )
        for m in matches
    ]
    return SearchResponse(query=q, threshold=threshold, hits=hits)


@router.get("/notes", response_model=NotesResponse)
async def get_notes(kb: tuple[Tenant, KnowledgeBaseConfig] = Depends(knowledge_base_for)):
    """The site's curated notes."""
    _, config = kb
    try:
        text = await document_service.read_notes(config.storage_prefix)
    except Exception as e:
        logger.error(f"Error reading notes: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to read notes: {str(e)}")
    return NotesResponse(text=text, notes=len(document_service.split_notes(text)))


@router.put("/notes", response_model=NotesResponse)
async def put_notes(body: NotesUpdate, kb: tuple[Tenant, KnowledgeBaseConfig] = Depends(knowledge_base_for)):
    """Replace the site's curated notes; one paragraph becomes one retrievable chunk."""
    tenant, config = kb
    try:
        result = await document_service.index_notes(body.text, config.namespace, config.storage_prefix)
    except Exception as e:
        logger.error(f"Error saving notes for {tenant.id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save notes: {str(e)}")
    logger.info(f"Notes updated for {tenant.id}: {result['notes']} notes")
    return NotesResponse(text=body.text, notes=result["notes"])


@router.post("/crawl", response_model=CrawlResponse)
async def crawl_site(
    body: CrawlRequest,
    kb: tuple[Tenant, KnowledgeBaseConfig] = Depends(knowledge_base_for),
):
    """
    Build or refresh the site's knowledge base from its website.

    Crawls same-host pages from the start URL, extracts readable text and
    indexes one document per page. Page ids derive from the URL, so a repeat
    crawl replaces pages instead of duplicating them. Client-rendered sites
    yield little or nothing; upload documents for those instead.
    """
    tenant, config = kb
    if not body.url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="url must start with http:// or https://")

    logger.info(f"Crawl for {tenant.id}: {body.url} (max_pages={body.max_pages}, max_depth={body.max_depth})")
    try:
        result = await crawler.crawl_site(body.url, max_pages=body.max_pages, max_depth=body.max_depth)
    except Exception as e:
        logger.error(f"Crawl failed for {body.url}: {e}")
        raise HTTPException(status_code=502, detail=f"Crawl failed: {e}")

    pages: list[CrawledPage] = []
    total_chunks = 0
    for page in result.pages:
        try:
            await document_service.delete_document(page.document_id, config.namespace, config.storage_prefix)
        except Exception as e:
            logger.debug(f"No previous version of {page.document_id} to remove: {e}")
        try:
            processed = await document_service.process_document(
                document_id=page.document_id,
                filename=crawler.filename_for(page.url),
                content=f"{page.title}\n\n{page.text}".encode("utf-8"),
                content_type="text/plain",
                namespace=config.namespace,
                storage_prefix=config.storage_prefix,
                extra_metadata={"url": page.url, "title": page.title},
            )
        except ValueError as e:
            result.skipped.append({"url": page.url, "reason": str(e)})
            continue
        pages.append(CrawledPage(url=page.url, title=page.title, document_id=page.document_id, chunks=processed["chunk_count"]))
        total_chunks += processed["chunk_count"]

    logger.info(f"Crawl complete for {tenant.id}: {len(pages)} pages, {total_chunks} chunks, {len(result.skipped)} skipped")
    return CrawlResponse(
        site=tenant.id,
        start_url=result.start_url,
        pages_indexed=len(pages),
        chunks=total_chunks,
        pages=pages,
        skipped=result.skipped,
    )


@status_router.get("/status", response_model=RAGStatusResponse)
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
