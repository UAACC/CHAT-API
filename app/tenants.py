"""
Tenants: the websites served by one deployment.

A tenant owns its prompts, model, allowed origins and (optionally) a
knowledge-base namespace. Tenants come from the YAML file named by
TENANTS_FILE; without that file a single implicit tenant is built from the
plain environment variables, so a one-site deployment needs no extra config.

Resolution per request: explicit `site` id, then the Origin header, then the
default tenant. See docs/specs/2026-09-05-multi-tenant-design.md.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlsplit

import yaml
from fastapi import HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.config import Settings, get_settings
from app.prompts.system_prompts import DEFAULT_PROMPT_EN, DEFAULT_PROMPT_ZH, load_prompts

logger = logging.getLogger(__name__)

DEFAULT_TENANT_ID = "default"
DEFAULT_NAMESPACE = "__default__"


class LlmConfig(BaseModel):
    provider: Literal["openai", "anthropic", "gemini"]
    model: str
    api_key_env: str = ""
    api_key: str = Field("", exclude=True, repr=False)
    max_tokens: int = 512
    temperature: float = 0.7


class KnowledgeBaseConfig(BaseModel):
    namespace: str = DEFAULT_NAMESPACE
    storage_prefix: Optional[str] = None


class LimitsConfig(BaseModel):
    rate_limit_requests: Optional[int] = None
    max_input_length: Optional[int] = None


class Tenant(BaseModel):
    id: str
    name: str
    origins: list[str] = Field(default_factory=list)
    llm: LlmConfig
    prompts: dict[str, str] = Field(default_factory=dict)
    knowledge_base: Optional[KnowledgeBaseConfig] = None
    limits: LimitsConfig = Field(default_factory=LimitsConfig)

    @field_validator("origins")
    @classmethod
    def _normalize_origins(cls, origins: list[str]) -> list[str]:
        return [normalize_origin(o) for o in origins if o and o.strip()]

    def prompt(self, locale: str) -> str:
        """System prompt for a locale, falling back to English, then defaults."""
        if locale.startswith("zh"):
            return self.prompts.get("zh") or self.prompts.get("en") or DEFAULT_PROMPT_ZH
        return self.prompts.get("en") or DEFAULT_PROMPT_EN

    def rate_limit_requests(self, settings: Settings) -> int:
        return self.limits.rate_limit_requests or settings.rate_limit_requests

    def max_input_length(self, settings: Settings) -> int:
        return self.limits.max_input_length or settings.max_input_length


def normalize_origin(origin: str) -> str:
    """Reduce an origin or URL to lower-case scheme://host[:port]."""
    parts = urlsplit(origin.strip())
    if not parts.scheme or not parts.netloc:
        raise ValueError(f"Invalid origin: {origin!r} (expected scheme://host)")
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


class TenantRegistry:
    """All tenants of a deployment plus the lookups the routes need."""

    def __init__(self, tenants: list[Tenant], default_id: str):
        if not tenants:
            raise ValueError("At least one tenant is required")
        self._by_id: dict[str, Tenant] = {}
        self._by_origin: dict[str, Tenant] = {}
        for tenant in tenants:
            if tenant.id in self._by_id:
                raise ValueError(f"Duplicate tenant id: {tenant.id}")
            self._by_id[tenant.id] = tenant
            for origin in tenant.origins:
                owner = self._by_origin.get(origin)
                if owner is not None and owner.id != tenant.id:
                    raise ValueError(f"Origin {origin} is claimed by both {owner.id} and {tenant.id}")
                self._by_origin[origin] = tenant
        if default_id not in self._by_id:
            raise ValueError(f"Default tenant {default_id!r} is not defined")
        self._default = self._by_id[default_id]

    @property
    def default(self) -> Tenant:
        return self._default

    @property
    def ids(self) -> list[str]:
        return list(self._by_id)

    @property
    def tenants(self) -> list[Tenant]:
        return list(self._by_id.values())

    @property
    def origins(self) -> list[str]:
        """Union of every tenant's origins, for the CORS allow-list."""
        return list(self._by_origin)

    def by_id(self, tenant_id: str) -> Optional[Tenant]:
        return self._by_id.get(tenant_id)

    def by_origin(self, origin: str) -> Optional[Tenant]:
        try:
            return self._by_origin.get(normalize_origin(origin))
        except ValueError:
            return None

    def resolve(self, request: Request, site: Optional[str] = None) -> Tenant:
        """
        Pick the tenant for a request.

        Raises:
            HTTPException 404 for an unknown explicit site id,
            HTTPException 403 for an Origin that no tenant claims.
        """
        if site:
            tenant = self.by_id(site)
            if tenant is None:
                raise HTTPException(status_code=404, detail="unknown site")
            return tenant

        origin = request.headers.get("origin")
        if origin and not _same_host(origin, request):
            tenant = self.by_origin(origin)
            if tenant is None:
                raise HTTPException(status_code=403, detail="origin not allowed")
            return tenant

        return self._default


def _same_host(origin: str, request: Request) -> bool:
    """
    True when the Origin is the service itself (pages the API serves, such as
    the widget demo). Compared by host only: behind a proxy the request may
    be seen as http while the browser sent https.
    """
    try:
        origin_host = urlsplit(origin).netloc.lower()
    except ValueError:
        return False
    request_host = (request.headers.get("host") or request.url.netloc).lower()
    return bool(origin_host) and origin_host == request_host


def get_registry(request: Request) -> TenantRegistry:
    """FastAPI dependency: the registry attached to the running app."""
    return request.app.state.registry


# ---------------------------------------------------------------------------
# Building a registry
# ---------------------------------------------------------------------------


def _resolve_api_key(tenant_id: str, llm: LlmConfig) -> None:
    if not llm.api_key_env:
        raise ValueError(f"Tenant {tenant_id!r}: llm.api_key_env is required")
    key = os.environ.get(llm.api_key_env, "").strip()
    if not key:
        raise ValueError(
            f"Tenant {tenant_id!r}: environment variable {llm.api_key_env} is empty"
        )
    llm.api_key = key


def load_tenants_file(path: str | Path, settings: Optional[Settings] = None) -> TenantRegistry:
    """Parse and validate a tenants YAML file. Raises ValueError on any problem."""
    settings = settings or get_settings()
    path = Path(path)
    if not path.exists():
        raise ValueError(f"Tenants file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    entries = raw.get("tenants") or {}
    if not isinstance(entries, dict) or not entries:
        raise ValueError(f"{path}: 'tenants' must be a non-empty mapping")

    tenants: list[Tenant] = []
    for tenant_id, spec in entries.items():
        spec = dict(spec or {})
        spec.setdefault("id", tenant_id)
        spec.setdefault("name", tenant_id)
        try:
            tenant = Tenant(**spec)
        except Exception as e:  # pydantic ValidationError or ValueError
            raise ValueError(f"{path}: tenant {tenant_id!r} is invalid: {e}") from e
        _resolve_api_key(tenant.id, tenant.llm)
        if tenant.knowledge_base and tenant.knowledge_base.storage_prefix is None:
            tenant.knowledge_base.storage_prefix = f"{settings.gcs_prefix}/{tenant.id}"
        tenants.append(tenant)

    default_id = raw.get("default") or tenants[0].id
    registry = TenantRegistry(tenants, default_id)
    logger.info(f"Loaded {len(tenants)} tenant(s) from {path}: {', '.join(registry.ids)}")
    return registry


def implicit_registry(settings: Optional[Settings] = None) -> TenantRegistry:
    """One tenant assembled from the plain environment variables."""
    settings = settings or get_settings()
    provider = settings.llm_provider
    model, key = {
        "openai": (settings.openai_model, settings.openai_api_key),
        "anthropic": (settings.anthropic_model, settings.anthropic_api_key),
        "gemini": (settings.gemini_model, settings.gemini_api_key),
    }[provider]

    tenant = Tenant(
        id=DEFAULT_TENANT_ID,
        name=settings.app_name,
        origins=settings.cors_origins_list,
        llm=LlmConfig(
            provider=provider,
            model=model,
            api_key_env=f"{provider.upper()}_API_KEY",
            api_key=key,
            max_tokens=settings.max_tokens,
            temperature=settings.temperature,
        ),
        prompts=dict(load_prompts()),
        # Legacy layout: shared namespace and bare prefix, gated at request
        # time on PINECONE_API_KEY so a missing key simply disables retrieval.
        knowledge_base=KnowledgeBaseConfig(
            namespace=DEFAULT_NAMESPACE, storage_prefix=settings.gcs_prefix
        ),
    )
    return TenantRegistry([tenant], DEFAULT_TENANT_ID)


def build_registry(settings: Optional[Settings] = None) -> TenantRegistry:
    settings = settings or get_settings()
    if settings.tenants_file:
        return load_tenants_file(settings.tenants_file, settings)
    return implicit_registry(settings)
