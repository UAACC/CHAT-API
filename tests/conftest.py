"""
Shared test fixtures.

The application reads its configuration once at import time, so the
environment is pinned here before anything from `app` is imported. No
test talks to a real LLM, vector store or cloud service.
"""

import os

# Deterministic configuration for every test module. Must run before `app`
# is imported anywhere in the test session.
os.environ.update(
    {
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

from app.main import app
from app.services import llm_service


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


@pytest.fixture
def fake_llm(monkeypatch):
    """
    Replace the LLM factory with a canned model.

    Returns a function so tests can choose the reply text.
    """

    def _install(reply: str = "Hello from the assistant."):
        model = FakeListChatModel(responses=[reply])
        monkeypatch.setattr(llm_service, "get_llm", lambda: model)
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


def chat_payload(*contents: str, locale: str = "en") -> dict:
    """Build a chat request body from alternating user/assistant turns."""
    messages = []
    for i, text in enumerate(contents):
        role = "user" if i % 2 == 0 else "assistant"
        messages.append({"role": role, "content": text})
    return {
        "session_id": "test-session",
        "messages": messages,
        "page_url": "https://example.com/",
        "locale": locale,
    }
