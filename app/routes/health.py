"""
Health check endpoint for Cloud Run and monitoring.
"""

from fastapi import APIRouter, Depends

from app.config import get_settings
from app.models.schemas import HealthResponse
from app.tenants import TenantRegistry, get_registry

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check(registry: TenantRegistry = Depends(get_registry)) -> HealthResponse:
    """Service status, default provider and configured tenants."""
    return HealthResponse(
        status="healthy",
        version="1.0.0",
        provider=registry.default.llm.provider,
        tenants=registry.ids,
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
