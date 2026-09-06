"""
Shared test fixtures.

The application reads its configuration once at import time, so the
environment is pinned here before anything from `app` is imported. No
test talks to a real LLM, vector store or cloud service.
"""

import os
import textwrap

# Deterministic configuration for every test module. Must run before `app`
# is imported anywhere in the test session.
os.environ.update(
    {
        "TENANTS_FILE": "",
        "LLM_PROVIDER": "openai",
        "OPENAI_API_KEY": "test-key",
        "PINECONE_API_KEY": "",
        "CORS_ORIGINS": "https://example.com,http://localhost:5173",
        "RATE_LIMIT_REQUESTS": "1000",
        "RATE_LIMIT_WINDOW": "60",
        "MAX_INPUT_LENGTH": "500",
        "MAX_MESSAGES_PER_SESSION": "20",
        "MAX_CONTEXT_MESSAGES": "10",
        "APP_ENV": "test",
        "LOG_LEVEL": "WARNING",
    }
)

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.main import app, create_app
from app.services import llm_service
from app.tenants import load_tenants_file


@pytest.fixture(autouse=True)
def reset_sse_state():
    """sse-starlette keeps loop-bound shutdown state; reset it per test."""
    try:
        from sse_starlette.sse import AppStatus

        if hasattr(AppStatus, "should_exit_event"):
            AppStatus.should_exit_event = None
    except ImportError:  # pragma: no cover
        pass
    yield


@pytest.fixture(autouse=True)
def clear_llm_cache():
    llm_service.clear_llm_cache()
    yield
    llm_service.clear_llm_cache()


@pytest.fixture
def fake_llm(monkeypatch):
    """
    Replace the LLM factory with a canned model.

    Returns a function so tests can choose the reply text.
    """

    def _install(reply: str = "Hello from the assistant."):
        model = FakeListChatModel(responses=[reply])
        monkeypatch.setattr(llm_service, "get_llm", lambda tenant=None, candidate=0: model)
        return model

    return _install


@pytest.fixture
def client():
    """Synchronous test client for plain JSON endpoints."""
    return TestClient(app)


@pytest.fixture
async def async_client():
    """Async client for streaming (SSE) endpoints."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


TWO_TENANTS_YAML = textwrap.dedent(
    """
    default: studio
    tenants:
      studio:
        name: Art Studio
        origins:
          - https://studio.example
          - http://localhost:5173
        llm:
          provider: openai
          model: gpt-4o-mini
          api_key_env: STUDIO_KEY
        prompts:
          en: You are the Studio assistant.
          zh: 您是工作室助手。
        knowledge_base:
          namespace: studio-ns
        limits:
          rate_limit_requests: 2
          max_input_length: 40
      consultancy:
        name: Consultancy
        origins:
          - https://consultancy.example
        llm:
          provider: gemini
          model: gemini-3.1-flash-lite
          api_key_env: CONSULTANCY_KEY
        prompts:
          en: You are the Consultancy assistant.
    """
)


@pytest.fixture
def tenants_file(tmp_path, monkeypatch):
    """A two-tenant YAML file with its API keys present in the environment."""
    monkeypatch.setenv("STUDIO_KEY", "sk-studio")
    monkeypatch.setenv("CONSULTANCY_KEY", "AIza-consultancy")
    path = tmp_path / "tenants.yaml"
    path.write_text(TWO_TENANTS_YAML, encoding="utf-8")
    return path


@pytest.fixture
def two_tenant_registry(tenants_file):
    return load_tenants_file(tenants_file)


@pytest.fixture
def two_tenant_client(two_tenant_registry):
    """Test client for an app serving the two fixture tenants."""
    return TestClient(create_app(two_tenant_registry))


def chat_payload(*contents: str, locale: str = "en", site: str = None) -> dict:
    """Build a chat request body from alternating user/assistant turns."""
    messages = []
    for i, text in enumerate(contents):
        role = "user" if i % 2 == 0 else "assistant"
        messages.append({"role": role, "content": text})
    payload = {
        "session_id": "test-session",
        "messages": messages,
        "page_url": "https://example.com/",
        "locale": locale,
    }
    if site is not None:
        payload["site"] = site
    return payload
