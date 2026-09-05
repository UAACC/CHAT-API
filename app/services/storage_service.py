"""
Google Cloud Storage service for document files.

Every function takes an optional `prefix` (the tenant's storage folder);
when omitted the global GCS_PREFIX is used, which is the single-tenant layout.
"""

import logging
from typing import Optional

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


def resolve_prefix(prefix: Optional[str]) -> str:
    return (prefix or get_settings().gcs_prefix).strip("/")


def build_blob_path(document_id: str, filename: str, prefix: Optional[str] = None) -> str:
    """Build the full blob path for a document."""
    return f"{resolve_prefix(prefix)}/{document_id}/original/{filename}"


async def upload_file(
    document_id: str,
    filename: str,
    content: bytes,
    content_type: str,
    prefix: Optional[str] = None,
) -> str:
    """
    Upload a file to GCS.

    Returns:
        GCS URI of the uploaded file (gs://bucket/path)
    """
    settings = get_settings()
    bucket = get_bucket()
    blob_path = build_blob_path(document_id, filename, prefix)
    blob = bucket.blob(blob_path)

    blob.upload_from_string(content, content_type=content_type)

    gcs_uri = f"gs://{settings.gcs_bucket}/{blob_path}"
    logger.info(f"Uploaded file to {gcs_uri}")
    return gcs_uri


async def download_file(document_id: str, filename: str, prefix: Optional[str] = None) -> Optional[bytes]:
    """Download a file from GCS, or None if it does not exist."""
    bucket = get_bucket()
    blob_path = build_blob_path(document_id, filename, prefix)
    blob = bucket.blob(blob_path)

    try:
        content = blob.download_as_bytes()
        logger.info(f"Downloaded file: {blob_path}")
        return content
    except NotFound:
        logger.warning(f"File not found: {blob_path}")
        return None


async def delete_document_files(document_id: str, prefix: Optional[str] = None) -> int:
    """Delete all files for a document. Returns the number deleted."""
    bucket = get_bucket()
    folder = f"{resolve_prefix(prefix)}/{document_id}/"

    deleted_count = 0
    for blob in list(bucket.list_blobs(prefix=folder)):
        blob.delete()
        deleted_count += 1
        logger.info(f"Deleted file: {blob.name}")
    return deleted_count


async def list_documents(prefix: Optional[str] = None) -> list[dict]:
    """List the documents stored under a prefix."""
    bucket = get_bucket()
    folder = f"{resolve_prefix(prefix)}/"
    depth = folder.count("/")  # segments before the document id

    documents = {}
    for blob in bucket.list_blobs(prefix=folder):
        # <prefix>/{document_id}/original/{filename}
        parts = blob.name.split("/")
        if len(parts) >= depth + 3 and parts[depth + 1] == "original":
            doc_id = parts[depth]
            if doc_id not in documents:
                documents[doc_id] = {
                    "id": doc_id,
                    "filename": parts[depth + 2],
                    "size": blob.size,
                    "created_at": blob.time_created.isoformat() if blob.time_created else None,
                    "content_type": blob.content_type,
                }

    return list(documents.values())


async def get_document_info(document_id: str, prefix: Optional[str] = None) -> Optional[dict]:
    """Metadata for one document, or None if not found."""
    settings = get_settings()
    bucket = get_bucket()
    folder = f"{resolve_prefix(prefix)}/{document_id}/original/"

    blobs = list(bucket.list_blobs(prefix=folder))
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
    """Check GCS connectivity and bucket access."""
    settings = get_settings()
    try:
        bucket = get_bucket()
        bucket.reload()
        return {"status": "healthy", "bucket": settings.gcs_bucket}
    except Exception as e:
        logger.error(f"GCS health check failed: {e}")
        return {"status": "unhealthy", "bucket": settings.gcs_bucket, "error": str(e)}
