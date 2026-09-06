"""Multi-tenant mode: configuration loading and per-request resolution."""

import textwrap

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.config import get_settings
from app.middleware import rate_limit as rl
from app.services import llm_service
from app.tenants import (
    DEFAULT_TENANT_ID,
    Tenant,
    TenantRegistry,
    implicit_registry,
    load_tenants_file,
    normalize_origin,
)
from tests.conftest import chat_payload


@pytest.fixture(autouse=True)
def fresh_rate_limiter(monkeypatch):
    """The studio tenant allows two requests per window; start each test clean."""
    monkeypatch.setattr(rl, "rate_limiter", rl.RateLimiter())


# ---------------------------------------------------------------------------
# Loading and validation
# ---------------------------------------------------------------------------


class TestLoadTenantsFile:
    def test_loads_both_tenants_and_default(self, two_tenant_registry):
        assert set(two_tenant_registry.ids) == {"studio", "consultancy"}
        assert two_tenant_registry.default.id == "studio"

    def test_api_keys_come_from_environment(self, two_tenant_registry):
        assert two_tenant_registry.by_id("studio").llm.api_key == "sk-studio"
        assert two_tenant_registry.by_id("consultancy").llm.api_key == "AIza-consultancy"

    def test_api_key_is_not_serialised(self, two_tenant_registry):
        dumped = two_tenant_registry.by_id("studio").model_dump()
        assert "api_key" not in dumped["llm"]
        assert dumped["llm"]["api_key_env"] == "STUDIO_KEY"

    def test_origins_are_normalised(self, two_tenant_registry):
        assert "https://studio.example" in two_tenant_registry.origins
        assert two_tenant_registry.by_origin("HTTPS://Studio.Example/") is two_tenant_registry.by_id("studio")

    def test_storage_prefix_defaults_to_tenant_folder(self, two_tenant_registry):
        kb = two_tenant_registry.by_id("studio").knowledge_base
        assert kb.namespace == "studio-ns"
        assert kb.storage_prefix == f"{get_settings().gcs_prefix}/studio"

    def test_tenant_without_knowledge_base(self, two_tenant_registry):
        assert two_tenant_registry.by_id("consultancy").knowledge_base is None

    def test_empty_key_variable_fails_loudly(self, tenants_file, monkeypatch):
        monkeypatch.setenv("CONSULTANCY_KEY", "")
        with pytest.raises(ValueError, match="CONSULTANCY_KEY"):
            load_tenants_file(tenants_file)

    def test_missing_file(self, tmp_path):
        with pytest.raises(ValueError, match="not found"):
            load_tenants_file(tmp_path / "nope.yaml")

    def test_duplicate_origin_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("K", "x")
        path = tmp_path / "t.yaml"
        path.write_text(
            textwrap.dedent(
                """
                tenants:
                  a: {origins: [https://same.example], llm: {provider: openai, model: m, api_key_env: K}}
                  b: {origins: [https://same.example], llm: {provider: openai, model: m, api_key_env: K}}
                """
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="claimed by both"):
            load_tenants_file(path)

    def test_unknown_default_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("K", "x")
        path = tmp_path / "t.yaml"
        path.write_text(
            "default: ghost\ntenants:\n  a: {origins: [https://a.example], llm: {provider: openai, model: m, api_key_env: K}}\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="ghost"):
            load_tenants_file(path)

    def test_invalid_provider_reported_with_tenant_id(self, tmp_path, monkeypatch):
        monkeypatch.setenv("K", "x")
        path = tmp_path / "t.yaml"
        path.write_text(
            "tenants:\n  weird: {origins: [https://a.example], llm: {provider: pigeon, model: m, api_key_env: K}}\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="'weird' is invalid"):
            load_tenants_file(path)


class TestImplicitRegistry:
    def test_mirrors_environment_settings(self):
        registry = implicit_registry()
        tenant = registry.default
        assert registry.ids == [DEFAULT_TENANT_ID]
        assert tenant.llm.provider == "openai"
        assert tenant.llm.api_key == "test-key"
        assert tenant.origins == ["https://example.com", "http://localhost:5173"]
        assert tenant.knowledge_base.namespace == "__default__"
        assert tenant.knowledge_base.storage_prefix == get_settings().gcs_prefix

    def test_prompt_falls_back_to_defaults(self):
        tenant = Tenant(id="x", name="x", llm={"provider": "openai", "model": "m"}, prompts={"en": "only en"})
        assert tenant.prompt("zh") == "only en"
        bare = Tenant(id="y", name="y", llm={"provider": "openai", "model": "m"})
        assert "assistant" in bare.prompt("en").lower()
        assert "助手" in bare.prompt("zh")


def test_normalize_origin_rejects_bare_hosts():
    with pytest.raises(ValueError):
        normalize_origin("example.com")


# ---------------------------------------------------------------------------
# Resolution and per-tenant behaviour over HTTP
# ---------------------------------------------------------------------------


@pytest.fixture
def per_tenant_llm(monkeypatch):
    """A canned model per tenant, so replies reveal which tenant answered."""
    models = {}

    def fake_get_llm(tenant, candidate=0):
        if tenant.id not in models:
            models[tenant.id] = FakeListChatModel(responses=[f"reply from {tenant.id}"] * 10)
        return models[tenant.id]

    monkeypatch.setattr(llm_service, "get_llm", fake_get_llm)
    return models


class TestResolution:
    def test_health_lists_tenants(self, two_tenant_client):
        body = two_tenant_client.get("/health").json()
        assert set(body["tenants"]) == {"studio", "consultancy"}
        assert body["provider"] == "openai"

    def test_origin_selects_tenant(self, two_tenant_client, per_tenant_llm):
        r = two_tenant_client.post("/chat", json=chat_payload("hi"), headers={"Origin": "https://consultancy.example"})
        assert r.status_code == 200
        assert r.json()["reply"] == "reply from consultancy"

    def test_explicit_site_wins_over_origin(self, two_tenant_client, per_tenant_llm):
        r = two_tenant_client.post(
            "/chat",
            json=chat_payload("hi", site="consultancy"),
            headers={"Origin": "https://studio.example"},
        )
        assert r.json()["reply"] == "reply from consultancy"

    def test_no_origin_uses_default(self, two_tenant_client, per_tenant_llm):
        r = two_tenant_client.post("/chat", json=chat_payload("hi"))
        assert r.json()["reply"] == "reply from studio"

    def test_unknown_site_is_404(self, two_tenant_client, per_tenant_llm):
        r = two_tenant_client.post("/chat", json=chat_payload("hi", site="nope"))
        assert r.status_code == 404
        assert r.json()["detail"] == "unknown site"

    def test_unclaimed_origin_is_403(self, two_tenant_client, per_tenant_llm):
        r = two_tenant_client.post("/chat", json=chat_payload("hi"), headers={"Origin": "https://evil.example"})
        assert r.status_code == 403

    def test_each_tenant_gets_its_own_prompt(self, two_tenant_client, per_tenant_llm, monkeypatch):
        seen = {}

        async def capture(messages, locale, tenant, context_chunks=None):
            seen[tenant.id] = llm_service.build_system_prompt(tenant, locale, context_chunks)
            return "ok"

        from app.routes import chat as chat_routes

        monkeypatch.setattr(chat_routes, "generate_response", capture)
        two_tenant_client.post("/chat", json=chat_payload("hi"), headers={"Origin": "https://studio.example"})
        two_tenant_client.post("/chat", json=chat_payload("hi"), headers={"Origin": "https://consultancy.example"})
        assert seen["studio"] == "You are the Studio assistant."
        assert seen["consultancy"] == "You are the Consultancy assistant."

    def test_cors_allow_list_is_the_union(self, two_tenant_client):
        for origin in ("https://studio.example", "https://consultancy.example", "http://localhost:5173"):
            r = two_tenant_client.options(
                "/chat/stream",
                headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
            )
            assert r.headers.get("access-control-allow-origin") == origin, origin
        r = two_tenant_client.options(
            "/chat/stream",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
        )
        assert "access-control-allow-origin" not in r.headers


class TestPerTenantLimits:
    def test_rate_limit_is_isolated_per_tenant(self, two_tenant_client, per_tenant_llm):
        ip = {"X-Forwarded-For": "203.0.113.9"}
        studio = {"Origin": "https://studio.example", **ip}
        consultancy = {"Origin": "https://consultancy.example", **ip}

        # studio allows 2 per window (tenant override)
        assert two_tenant_client.post("/chat", json=chat_payload("a"), headers=studio).status_code == 200
        assert two_tenant_client.post("/chat", json=chat_payload("a"), headers=studio).status_code == 200
        assert two_tenant_client.post("/chat", json=chat_payload("a"), headers=studio).status_code == 429
        # same IP, other tenant: its own budget (global default of 1000)
        assert two_tenant_client.post("/chat", json=chat_payload("a"), headers=consultancy).status_code == 200

    def test_input_length_override(self, two_tenant_client, per_tenant_llm):
        studio = {"Origin": "https://studio.example"}
        assert two_tenant_client.post("/chat", json=chat_payload("x" * 41), headers=studio).status_code == 400
        consultancy = {"Origin": "https://consultancy.example"}
        assert two_tenant_client.post("/chat", json=chat_payload("x" * 41), headers=consultancy).status_code == 200


class TestKnowledgeBaseRouting:
    def test_retrieval_uses_tenant_namespace(self, two_tenant_client, per_tenant_llm, monkeypatch):
        monkeypatch.setattr(get_settings(), "pinecone_api_key", "pc-test")
        calls = []

        async def fake_query(**kwargs):
            calls.append(kwargs["namespace"])
            return []

        from app.routes import chat as chat_routes

        monkeypatch.setattr(chat_routes.vector_store_service, "query_vectors", fake_query)
        two_tenant_client.post("/chat", json=chat_payload("hi"), headers={"Origin": "https://studio.example"})
        two_tenant_client.post("/chat", json=chat_payload("hi"), headers={"Origin": "https://consultancy.example"})
        # consultancy has no knowledge base, so only studio queried
        assert calls == ["studio-ns"]

    def test_document_endpoints_require_a_knowledge_base(self, two_tenant_client):
        r = two_tenant_client.get("/rag/documents", params={"site": "consultancy"})
        assert r.status_code == 400
        assert "no knowledge base" in r.json()["detail"]

    def test_document_list_scoped_to_tenant(self, two_tenant_client, monkeypatch):
        from app.routes import rag as rag_routes

        seen = {}

        async def fake_list(prefix=None):
            seen["prefix"] = prefix
            return []

        monkeypatch.setattr(rag_routes.storage_service, "list_documents", fake_list)
        r = two_tenant_client.get("/rag/documents", params={"site": "studio"})
        assert r.status_code == 200
        assert seen["prefix"] == f"{get_settings().gcs_prefix}/studio"
