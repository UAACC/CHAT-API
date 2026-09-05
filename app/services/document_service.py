"""
Document parsing and chunking service.
Supports PDF, TXT, and Markdown files.
"""

import logging
import uuid
from typing import Optional

import tiktoken
from pypdf import PdfReader
from io import BytesIO

from app.config import get_settings
from app.services import storage_service, vector_store_service

logger = logging.getLogger(__name__)

# Supported file types
SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".md", ".markdown"}
SUPPORTED_CONTENT_TYPES = {
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/x-markdown": ".md",
}


def get_file_extension(filename: str, content_type: str) -> Optional[str]:
    """
    Determine file extension from filename or content type.

    Returns:
        File extension (e.g., '.pdf') or None if unsupported
    """
    # Try filename first
    for ext in SUPPORTED_EXTENSIONS:
        if filename.lower().endswith(ext):
            return ext

    # Try content type
    return SUPPORTED_CONTENT_TYPES.get(content_type)


def parse_pdf(content: bytes) -> str:
    """
    Extract text from PDF file.

    Args:
        content: PDF file bytes

    Returns:
        Extracted text content
    """
    reader = PdfReader(BytesIO(content))
    text_parts = []

    for page_num, page in enumerate(reader.pages):
        page_text = page.extract_text()
        if page_text:
            text_parts.append(page_text)

    text = "\n\n".join(text_parts)
    logger.info(f"Extracted {len(text)} characters from PDF ({len(reader.pages)} pages)")
    return text


def parse_text(content: bytes) -> str:
    """
    Decode text file content.

    Args:
        content: Text file bytes

    Returns:
        Decoded text content
    """
    # Try UTF-8 first, then fall back to latin-1
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    logger.info(f"Parsed text file: {len(text)} characters")
    return text


def parse_document(content: bytes, file_extension: str) -> str:
    """
    Parse document content based on file type.

    Args:
        content: File content as bytes
        file_extension: File extension (e.g., '.pdf')

    Returns:
        Extracted text content
    """
    if file_extension == ".pdf":
        return parse_pdf(content)
    elif file_extension in {".txt", ".md", ".markdown"}:
        return parse_text(content)
    else:
        raise ValueError(f"Unsupported file extension: {file_extension}")


def count_tokens(text: str, model: str = "cl100k_base") -> int:
    """
    Count tokens in text using tiktoken.

    Args:
        text: Text to count tokens for
        model: Tokenizer model name

    Returns:
        Number of tokens
    """
    encoding = tiktoken.get_encoding(model)
    return len(encoding.encode(text))


def chunk_text(
    text: str,
    chunk_size: Optional[int] = None,
    chunk_overlap: Optional[int] = None,
) -> list[str]:
    """
    Split text into overlapping chunks based on token count.

    Uses a recursive character-based splitting approach:
    1. Try to split on paragraphs
    2. Then sentences
    3. Then words
    4. Finally characters

    Args:
        text: Text to chunk
        chunk_size: Target chunk size in tokens
        chunk_overlap: Overlap between chunks in tokens

    Returns:
        List of text chunks
    """
    settings = get_settings()
    chunk_size = chunk_size or settings.rag_chunk_size
    chunk_overlap = chunk_overlap or settings.rag_chunk_overlap

    encoding = tiktoken.get_encoding("cl100k_base")

    # Separators in order of preference
    separators = ["\n\n", "\n", ". ", " ", ""]

    def split_text_recursive(text: str, separators: list[str]) -> list[str]:
        """Recursively split text using separators."""
        if not text:
            return []

        # Check if text fits in chunk
        if count_tokens(text) <= chunk_size:
            return [text.strip()] if text.strip() else []

        # Find the best separator
        separator = separators[-1]  # Default to empty string (character split)
        for sep in separators:
            if sep in text:
                separator = sep
                break

        # Split on separator
        if separator:
            parts = text.split(separator)
        else:
            # Character-level split
            parts = list(text)

        # Merge parts into chunks
        chunks = []
        current_chunk = []
        current_tokens = 0

        for part in parts:
            part_text = part if not separator else part + separator
            part_tokens = count_tokens(part_text)

            if current_tokens + part_tokens <= chunk_size:
                current_chunk.append(part_text)
                current_tokens += part_tokens
            else:
                # Save current chunk
                if current_chunk:
                    chunk_text = "".join(current_chunk).strip()
                    if chunk_text:
                        chunks.append(chunk_text)

                # Start new chunk with overlap
                if chunk_overlap > 0 and current_chunk:
                    # Keep some parts for overlap
                    overlap_parts = []
                    overlap_tokens = 0
                    for p in reversed(current_chunk):
                        p_tokens = count_tokens(p)
                        if overlap_tokens + p_tokens <= chunk_overlap:
                            overlap_parts.insert(0, p)
                            overlap_tokens += p_tokens
                        else:
                            break
                    current_chunk = overlap_parts + [part_text]
                    current_tokens = overlap_tokens + part_tokens
                else:
                    current_chunk = [part_text]
                    current_tokens = part_tokens

        # Don't forget the last chunk
        if current_chunk:
            chunk_text = "".join(current_chunk).strip()
            if chunk_text:
                chunks.append(chunk_text)

        return chunks

    chunks = split_text_recursive(text, separators)
    logger.info(f"Split text into {len(chunks)} chunks (target size: {chunk_size} tokens)")

    return chunks


async def process_document(
    document_id: str,
    filename: str,
    content: bytes,
    content_type: str,
    namespace: str = "__default__",
    storage_prefix: Optional[str] = None,
) -> dict:
    """
    Process a document: parse, chunk, embed, and store.

    Args:
        document_id: Unique document identifier
        filename: Original filename
        content: File content as bytes
        content_type: MIME type of the file
        namespace: Pinecone namespace of the owning tenant
        storage_prefix: Storage folder of the owning tenant (default: global prefix)

    Returns:
        Processing result with document info and stats
    """
    settings = get_settings()

    # Validate file size
    max_size = settings.rag_max_file_size_mb * 1024 * 1024
    if len(content) > max_size:
        raise ValueError(f"File exceeds maximum size of {settings.rag_max_file_size_mb}MB")

    # Determine file type
    file_extension = get_file_extension(filename, content_type)
    if not file_extension:
        raise ValueError(f"Unsupported file type: {filename} ({content_type})")

    # Store original file in GCS
    gcs_uri = await storage_service.upload_file(
        document_id=document_id,
        filename=filename,
        content=content,
        content_type=content_type,
        prefix=storage_prefix,
    )

    # Parse document
    text = parse_document(content, file_extension)
    if not text.strip():
        raise ValueError("Document contains no extractable text")

    # Chunk text
    chunks = chunk_text(text)
    if not chunks:
        raise ValueError("Document produced no chunks after processing")

    # Prepare records for Pinecone (embeddings handled by Pinecone's integrated model)
    records = []
    for i, chunk in enumerate(chunks):
        record_id = f"{document_id}_{i}"
        records.append({
            "_id": record_id,
            "text": chunk,  # Pinecone will embed this field
            "document_id": document_id,
            "filename": filename,
            "chunk_index": i,
            "total_chunks": len(chunks),
        })

    # Upsert to Pinecone (Pinecone handles embedding automatically)
    upserted_count = await vector_store_service.upsert_records(records, namespace)

    result = {
        "document_id": document_id,
        "filename": filename,
        "file_extension": file_extension,
        "gcs_uri": gcs_uri,
        "file_size": len(content),
        "text_length": len(text),
        "chunk_count": len(chunks),
        "vector_count": upserted_count,
    }

    logger.info(f"Processed document: {result}")
    return result


async def delete_document(
    document_id: str,
    namespace: str = "__default__",
    storage_prefix: Optional[str] = None,
) -> dict:
    """
    Delete a document and its vectors.

    Args:
        document_id: Document ID to delete
        namespace: Pinecone namespace of the owning tenant
        storage_prefix: Storage folder of the owning tenant (default: global prefix)

    Returns:
        Deletion result
    """
    # Delete vectors from Pinecone
    await vector_store_service.delete_vectors(document_id, namespace)

    # Delete files from GCS
    files_deleted = await storage_service.delete_document_files(document_id, storage_prefix)

    result = {
        "document_id": document_id,
        "files_deleted": files_deleted,
        "vectors_deleted": True,
    }

    logger.info(f"Deleted document: {result}")
    return result


def generate_document_id() -> str:
    """Generate a unique document ID."""
    return str(uuid.uuid4())
