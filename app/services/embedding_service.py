"""
OpenAI embedding service for text vectorization.
"""

import logging
from typing import Optional

from openai import OpenAI

from app.config import get_settings

logger = logging.getLogger(__name__)

_client: Optional[OpenAI] = None


def get_openai_client() -> OpenAI:
    """Get or create OpenAI client instance."""
    global _client
    if _client is None:
        settings = get_settings()
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required for embeddings")
        _client = OpenAI(api_key=settings.openai_api_key)
    return _client


async def embed_text(text: str) -> list[float]:
    """
    Generate embedding vector for a single text.

    Args:
        text: Text to embed

    Returns:
        Embedding vector as list of floats (1536 dimensions for text-embedding-3-small)
    """
    settings = get_settings()
    client = get_openai_client()

    response = client.embeddings.create(
        model=settings.embedding_model,
        input=text,
    )

    embedding = response.data[0].embedding
    logger.debug(f"Generated embedding for text ({len(text)} chars)")

    return embedding


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Generate embedding vectors for multiple texts in a batch.

    Args:
        texts: List of texts to embed

    Returns:
        List of embedding vectors
    """
    if not texts:
        return []

    settings = get_settings()
    client = get_openai_client()

    response = client.embeddings.create(
        model=settings.embedding_model,
        input=texts,
    )

    # Sort by index to maintain order
    embeddings = [None] * len(texts)
    for item in response.data:
        embeddings[item.index] = item.embedding

    logger.info(f"Generated {len(embeddings)} embeddings in batch")

    return embeddings


def check_embedding_health() -> dict:
    """
    Check embedding service connectivity.

    Returns:
        Health status dictionary
    """
    settings = get_settings()
    try:
        client = get_openai_client()
        # Test with a small embedding request
        response = client.embeddings.create(
            model=settings.embedding_model,
            input="test",
        )
        return {
            "status": "healthy",
            "model": settings.embedding_model,
            "dimensions": len(response.data[0].embedding),
        }
    except Exception as e:
        logger.error(f"Embedding health check failed: {e}")
        return {
            "status": "unhealthy",
            "model": settings.embedding_model,
            "error": str(e),
        }
