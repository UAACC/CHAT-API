"""
Pinecone vector store service with integrated embeddings.
Uses Pinecone's inference API for automatic text embedding.
"""

import logging
from typing import Optional

from pinecone import Pinecone

from app.config import get_settings

logger = logging.getLogger(__name__)

_pinecone_client: Optional[Pinecone] = None
_index = None


def get_pinecone_client() -> Pinecone:
    """Get or create Pinecone client instance."""
    global _pinecone_client
    if _pinecone_client is None:
        settings = get_settings()
        if not settings.pinecone_api_key:
            raise RuntimeError("PINECONE_API_KEY is required")
        _pinecone_client = Pinecone(api_key=settings.pinecone_api_key)
    return _pinecone_client


def get_index():
    """Get or create Pinecone index instance."""
    global _index
    if _index is None:
        settings = get_settings()
        client = get_pinecone_client()
        _index = client.Index(settings.pinecone_index)
    return _index


async def upsert_records(
    records: list[dict],
    namespace: str = "__default__",
) -> int:
    """
    Upsert records to Pinecone with integrated embeddings.
    Pinecone will automatically embed the text field.

    Args:
        records: List of dicts with '_id', 'text', and metadata fields
        namespace: Optional namespace for multi-tenancy

    Returns:
        Number of records upserted
    """
    if not records:
        return 0

    index = get_index()

    # Upsert in batches of 96 (Pinecone integrated inference limit)
    batch_size = 96
    total_upserted = 0

    for i in range(0, len(records), batch_size):
        batch = records[i : i + batch_size]
        index.upsert_records(namespace=namespace, records=batch)
        total_upserted += len(batch)
        logger.debug(f"Upserted batch of {len(batch)} records")

    logger.info(f"Upserted {total_upserted} records to Pinecone")
    return total_upserted


async def search_by_text(
    query_text: str,
    top_k: Optional[int] = None,
    min_score: Optional[float] = None,
    filter: Optional[dict] = None,
    namespace: str = "__default__",
) -> list[dict]:
    """
    Search Pinecone using text query with integrated embeddings.

    Args:
        query_text: Query text (will be embedded by Pinecone)
        top_k: Number of results to return
        min_score: Minimum similarity score threshold
        filter: Metadata filter
        namespace: Optional namespace

    Returns:
        List of matches with id, score, and fields
    """
    settings = get_settings()
    index = get_index()

    top_k = top_k if top_k is not None else settings.rag_default_top_k
    min_score = min_score if min_score is not None else settings.rag_min_score_threshold

    # Build query with rerank for better results
    query_params = {
        "namespace": namespace,
        "query": {
            "top_k": top_k,
            "inputs": {"text": query_text},
        },
    }

    if filter:
        query_params["query"]["filter"] = filter

    results = index.search(**query_params)

    # Convert to dict for easier access
    results_dict = results.to_dict() if hasattr(results, 'to_dict') else results

    # Filter by minimum score and extract results
    matches = []

    try:
        hits = results_dict.get('result', {}).get('hits', [])

        for hit in hits:
            # pinecone<10 serialises hits as _id/_score, pinecone>=10 as id_/score_
            score = hit.get('_score', hit.get('score_', 0)) or 0
            hit_id = hit.get('_id') or hit.get('id_', '')
            fields = hit.get('fields', {})

            if score >= min_score:
                matches.append({
                    "id": hit_id,
                    "score": score,
                    "fields": fields,
                })
    except Exception as e:
        logger.error(f"Error parsing search results: {e}")

    logger.info(f"Search returned {len(matches)} results above threshold {min_score}")
    return matches


async def delete_by_filter(
    document_id: str,
    namespace: str = "__default__",
) -> bool:
    """
    Delete all records for a document.

    Args:
        document_id: Document ID to delete records for
        namespace: Optional namespace

    Returns:
        True if deletion was successful
    """
    index = get_index()

    # Delete by metadata filter
    index.delete(
        filter={"document_id": {"$eq": document_id}},
        namespace=namespace,
    )

    logger.info(f"Deleted records for document: {document_id}")
    return True


async def get_document_record_count(document_id: str, namespace: str = "__default__") -> int:
    """
    Get the number of records for a specific document.

    Note: This uses a search with filter to count matches.

    Args:
        document_id: Document ID to count records for
        namespace: Optional namespace

    Returns:
        Number of records for the document
    """
    index = get_index()

    try:
        # Search with filter to count
        results = index.search(
            namespace=namespace,
            query={
                "top_k": 10000,
                "inputs": {"text": "count"},
                "filter": {"document_id": {"$eq": document_id}},
            },
        )
        count = len(results.get("result", {}).get("hits", []))
        logger.debug(f"Document {document_id} has {count} records")
        return count
    except Exception as e:
        logger.warning(f"Could not count records for {document_id}: {e}")
        return 0


def check_vector_store_health() -> dict:
    """
    Check Pinecone connectivity and index status.

    Returns:
        Health status dictionary
    """
    settings = get_settings()
    try:
        index = get_index()
        stats = index.describe_index_stats()
        return {
            "status": "healthy",
            "index": settings.pinecone_index,
            "total_vectors": stats.total_vector_count,
            "namespaces": list(stats.namespaces.keys()) if stats.namespaces else [],
        }
    except Exception as e:
        logger.error(f"Pinecone health check failed: {e}")
        return {
            "status": "unhealthy",
            "index": settings.pinecone_index,
            "error": str(e),
        }


# Legacy functions for backwards compatibility
async def upsert_vectors(vectors: list[dict], namespace: str = "__default__") -> int:
    """Legacy: Convert vector format to record format and upsert."""
    records = []
    for v in vectors:
        record = {
            "_id": v["id"],
            "text": v.get("metadata", {}).get("text", ""),
            "document_id": v.get("metadata", {}).get("document_id", ""),
            "filename": v.get("metadata", {}).get("filename", ""),
            "chunk_index": v.get("metadata", {}).get("chunk_index", 0),
            "total_chunks": v.get("metadata", {}).get("total_chunks", 0),
        }
        records.append(record)
    return await upsert_records(records, namespace)


async def query_vectors(
    query_vector: list[float] = None,
    query_text: str = None,
    top_k: Optional[int] = None,
    min_score: Optional[float] = None,
    filter: Optional[dict] = None,
    namespace: str = "__default__",
    include_metadata: bool = True,
) -> list[dict]:
    """Legacy: Query with text, return in old format."""
    if not query_text:
        raise ValueError("query_text is required for integrated embeddings")

    matches = await search_by_text(query_text, top_k, min_score, filter, namespace)

    # Convert to legacy format
    results = []
    for m in matches:
        results.append({
            "id": m["id"],
            "score": m["score"],
            "metadata": {
                "document_id": m.get("fields", {}).get("document_id", ""),
                "filename": m.get("fields", {}).get("filename", ""),
                "text": m.get("fields", {}).get("text", ""),
                "chunk_index": m.get("fields", {}).get("chunk_index", 0),
            },
        })
    return results


async def delete_vectors(document_id: str, namespace: str = "__default__") -> bool:
    """Legacy: Delete vectors by document ID."""
    return await delete_by_filter(document_id, namespace)


async def get_document_vector_count(document_id: str, namespace: str = "__default__") -> int:
    """Legacy: Get vector count for document."""
    return await get_document_record_count(document_id, namespace)


async def get_document_records(document_id: str, namespace: str = "__default__") -> list[dict]:
    """
    Every stored chunk of a document, ordered by chunk index.

    Returns:
        Dicts with id, chunk_index, text, url, title
    """
    index = get_index()
    results = index.search(
        namespace=namespace,
        query={
            "top_k": 10000,
            "inputs": {"text": "document"},
            "filter": {"document_id": {"$eq": document_id}},
        },
        fields=["text", "chunk_index", "url", "title", "filename"],
    )
    results_dict = results.to_dict() if hasattr(results, "to_dict") else results
    records = []
    for hit in results_dict.get("result", {}).get("hits", []):
        fields = hit.get("fields", {}) or {}
        records.append({
            "id": hit.get("_id") or hit.get("id_", ""),
            "chunk_index": int(fields.get("chunk_index", 0) or 0),
            "text": fields.get("text", ""),
            "url": fields.get("url"),
            "title": fields.get("title"),
        })
    records.sort(key=lambda r: r["chunk_index"])
    return records
