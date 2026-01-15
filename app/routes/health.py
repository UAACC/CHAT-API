"""
Health check endpoint for Cloud Run and monitoring.
"""

from fastapi import APIRouter

from app.models.schemas import HealthResponse
from app.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """
    Health check endpoint.

    Returns service status for Cloud Run health probes
    and monitoring systems.
    """
    settings = get_settings()
    return HealthResponse(
        status="healthy",
        version="1.0.0",
        provider=settings.llm_provider,
    )


@router.get("/")
async def root():
    """Root endpoint with API information."""
    settings = get_settings()
    return {
        "service": settings.app_name,
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
    }
