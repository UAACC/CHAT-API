"""
CHAT-API - FastAPI application.

A drop-in assistant backend for websites: streaming chat, pluggable LLM
providers, optional knowledge base, one deployment for many sites.
"""

import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routes import health, chat, rag, widget
from app.tenants import TenantRegistry, build_registry

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Log the effective configuration on startup."""
    registry: TenantRegistry = app.state.registry
    logger.info(f"Starting {settings.app_name} ({settings.app_env})")
    for tenant in registry.tenants:
        kb = tenant.knowledge_base.namespace if tenant.knowledge_base else "none"
        chain = " -> ".join(f"{c.provider}/{c.model}" for c in tenant.llm_candidates)
        logger.info(f"Tenant {tenant.id}: {chain}, origins={tenant.origins}, knowledge_base={kb}")
    if not settings.admin_token:
        logger.warning("ADMIN_TOKEN is not set: knowledge-base upload, delete and crawl endpoints are open")
    if settings.pinecone_api_key:
        logger.info(f"Knowledge base: Pinecone index={settings.pinecone_index}, GCS bucket={settings.gcs_bucket}")
    else:
        logger.info("Knowledge base: disabled (no PINECONE_API_KEY), answering from system prompts only")
    yield
    logger.info(f"Shutting down {settings.app_name}")


def create_app(registry: Optional[TenantRegistry] = None) -> FastAPI:
    """
    Build the application.

    `registry` defaults to the tenants configured through the environment
    (TENANTS_FILE, or the implicit single tenant). Tests pass their own.
    """
    registry = registry or build_registry(settings)

    app = FastAPI(
        title=settings.app_name,
        description=settings.app_description,
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.registry = registry

    app.add_middleware(
        CORSMiddleware,
        allow_origins=registry.origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(rag.router)
    app.include_router(rag.status_router)
    app.include_router(widget.router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.app_env == "development",
    )
