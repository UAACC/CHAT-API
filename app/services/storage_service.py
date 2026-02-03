"""
Google Cloud Storage service for file operations.
"""

import logging
from typing import Optional
from datetime import datetime

from google.cloud import storage
from google.cloud.exceptions import NotFound

from app.config import get_settings

logger = logging.getLogger(__name__)


def get_storage_client() -> storage.Client:
    """Get GCS client instance."""
    return storage.Client()


def get_bucket() -> storage.Bucket:
    """Get the configured GCS bucket."""
    settings = get_settings()
    client = get_storage_client()
    return client.bucket(settings.gcs_bucket)


def build_blob_path(document_id: str, filename: str) -> str:
    """Build the full blob path for a document."""
    settings = get_settings()
    return f"{settings.gcs_prefix}/{document_id}/original/{filename}"


async def upload_file(
    document_id: str,
    filename: str,
    content: bytes,
    content_type: str,
) -> str:
    """
    Upload a file to GCS.

    Args:
        document_id: Unique document identifier
        filename: Original filename
        content: File content as bytes
        content_type: MIME type of the file

    Returns:
        GCS URI of the uploaded file (gs://bucket/path)
    """
    settings = get_settings()
    bucket = get_bucket()
    blob_path = build_blob_path(document_id, filename)
    blob = bucket.blob(blob_path)

    blob.upload_from_string(content, content_type=content_type)

    gcs_uri = f"gs://{settings.gcs_bucket}/{blob_path}"
    logger.info(f"Uploaded file to {gcs_uri}")

    return gcs_uri


async def download_file(document_id: str, filename: str) -> Optional[bytes]:
    """
    Download a file from GCS.

    Args:
        document_id: Unique document identifier
        filename: Original filename

    Returns:
        File content as bytes, or None if not found
    """
    bucket = get_bucket()
    blob_path = build_blob_path(document_id, filename)
    blob = bucket.blob(blob_path)

    try:
        content = blob.download_as_bytes()
        logger.info(f"Downloaded file: {blob_path}")
        return content
    except NotFound:
        logger.warning(f"File not found: {blob_path}")
        return None


async def delete_document_files(document_id: str) -> int:
    """
    Delete all files for a document from GCS.

    Args:
        document_id: Unique document identifier

    Returns:
        Number of files deleted
    """
    settings = get_settings()
    bucket = get_bucket()
    prefix = f"{settings.gcs_prefix}/{document_id}/"

    blobs = list(bucket.list_blobs(prefix=prefix))
    deleted_count = 0

    for blob in blobs:
        blob.delete()
        deleted_count += 1
        logger.info(f"Deleted file: {blob.name}")

    return deleted_count


async def list_documents() -> list[dict]:
    """
    List all document folders in GCS.

    Returns:
        List of document info dictionaries with id and metadata
    """
    settings = get_settings()
    bucket = get_bucket()
    prefix = f"{settings.gcs_prefix}/"

    # Get all blobs and extract unique document IDs
    blobs = bucket.list_blobs(prefix=prefix)
    documents = {}

    for blob in blobs:
        # Extract document_id from path: documents/{document_id}/original/{filename}
        parts = blob.name.split("/")
        if len(parts) >= 4 and parts[2] == "original":
            doc_id = parts[1]
            if doc_id not in documents:
                documents[doc_id] = {
                    "id": doc_id,
                    "filename": parts[3],
                    "size": blob.size,
                    "created_at": blob.time_created.isoformat() if blob.time_created else None,
                    "content_type": blob.content_type,
                }

    return list(documents.values())


async def get_document_info(document_id: str) -> Optional[dict]:
    """
    Get information about a specific document.

    Args:
        document_id: Unique document identifier

    Returns:
        Document info dictionary or None if not found
    """
    settings = get_settings()
    bucket = get_bucket()
    prefix = f"{settings.gcs_prefix}/{document_id}/original/"

    blobs = list(bucket.list_blobs(prefix=prefix))

    if not blobs:
        return None

    blob = blobs[0]
    return {
        "id": document_id,
        "filename": blob.name.split("/")[-1],
        "size": blob.size,
        "created_at": blob.time_created.isoformat() if blob.time_created else None,
        "content_type": blob.content_type,
        "gcs_uri": f"gs://{settings.gcs_bucket}/{blob.name}",
    }


def check_storage_health() -> dict:
    """
    Check GCS connectivity and bucket access.

    Returns:
        Health status dictionary
    """
    settings = get_settings()
    try:
        bucket = get_bucket()
        # Try to check if bucket exists
        bucket.reload()
        return {
            "status": "healthy",
            "bucket": settings.gcs_bucket,
        }
    except Exception as e:
        logger.error(f"GCS health check failed: {e}")
        return {
            "status": "unhealthy",
            "bucket": settings.gcs_bucket,
            "error": str(e),
        }
