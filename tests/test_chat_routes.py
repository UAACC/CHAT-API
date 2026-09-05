"""HTTP-level tests for the chat endpoints, using a canned LLM."""

import pytest

from app.config import get_settings
from app.routes import chat as chat_routes
from tests.conftest import chat_payload


async def read_sse(response):
    """Collect (event, data) pairs from an SSE response body."""
    events = []
    current = "message"
    async for line in response.aiter_lines():
        line = line.rstrip("\r")
        if line.startswith(":"):
            continue  # keep-alive ping
        if line.startswith("event:"):
            current = line[6:].strip()
        elif line.startswith("data:"):
            # SSE allows exactly one optional space after the colon; anything
            # beyond that is payload (a single-space token must survive).
            data = line[5:]
            if data.startswith(" "):
                data = data[1:]
            events.append((current, data))
    return events


class TestHealth:
    def test_health_reports_provider_and_tenants(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "healthy"
        assert body["provider"] == "openai"
        assert body["tenants"] == ["default"]

    def test_root_lists_docs(self, client):
        assert client.get("/").json()["docs"] == "/docs"


class TestStreaming:
    async def test_tokens_then_done(self, async_client, fake_llm):
        fake_llm("Hi there!")
        async with async_client.stream("POST", "/chat/stream", json=chat_payload("hello")) as r:
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/event-stream")
            events = await read_sse(r)

        tokens = [d for e, d in events if e == "token"]
        assert "".join(tokens) == "Hi there!"
        assert events[-1][0] == "done"
        assert not any(e == "error" for e, _ in events)

    async def test_llm_failure_becomes_error_event(self, async_client, monkeypatch):
        from app.services import llm_service

        def boom(tenant=None):
            raise RuntimeError("provider exploded")

        monkeypatch.setattr(llm_service, "get_llm", boom)

        async with async_client.stream("POST", "/chat/stream", json=chat_payload("hello")) as r:
            events = await read_sse(r)

        assert events == [("error", "provider exploded")]

    async def test_rag_is_skipped_without_pinecone_key(self, async_client, fake_llm, monkeypatch):
        fake_llm("ok")
        called = {"n": 0}

        async def should_not_run(**kwargs):
            called["n"] += 1
            return []

        monkeypatch.setattr(chat_routes.vector_store_service, "query_vectors", should_not_run)
        async with async_client.stream("POST", "/chat/stream", json=chat_payload("hello")) as r:
            await read_sse(r)
        assert called["n"] == 0

    async def test_rag_failure_does_not_break_chat(self, async_client, fake_llm, monkeypatch):
        fake_llm("still fine")
        monkeypatch.setattr(get_settings(), "pinecone_api_key", "pc-test")

        async def exploding_query(**kwargs):
            raise ConnectionError("pinecone down")

        monkeypatch.setattr(chat_routes.vector_store_service, "query_vectors", exploding_query)
        async with async_client.stream("POST", "/chat/stream", json=chat_payload("hello")) as r:
            events = await read_sse(r)
        assert "".join(d for e, d in events if e == "token") == "still fine"


class TestNonStreaming:
    def test_reply_and_session_echo(self, client, fake_llm):
        fake_llm("A complete answer.")
        r = client.post("/chat", json=chat_payload("hello"))
        assert r.status_code == 200
        assert r.json() == {"reply": "A complete answer.", "session_id": "test-session"}


class TestValidation:
    def test_message_too_long(self, client, fake_llm):
        fake_llm()
        r = client.post("/chat", json=chat_payload("x" * 501))
        assert r.status_code == 400
        assert "too long" in r.json()["detail"].lower()

    def test_too_many_messages(self, client, fake_llm):
        fake_llm()
        r = client.post("/chat", json=chat_payload(*["m"] * 21))
        assert r.status_code == 400
        assert "conversation too long" in r.json()["detail"].lower()

    def test_invalid_locale_rejected(self, client):
        r = client.post("/chat", json=chat_payload("hi", locale="fr"))
        assert r.status_code == 422

    def test_invalid_role_rejected(self, client):
        payload = chat_payload("hi")
        payload["messages"][0]["role"] = "robot"
        assert client.post("/chat", json=payload).status_code == 422


class TestTruncation:
    def test_keeps_last_n_messages(self):
        assert chat_routes.truncate_messages(list(range(10)), max_context=3) == [7, 8, 9]

    def test_short_history_untouched(self):
        assert chat_routes.truncate_messages([1, 2], max_context=3) == [1, 2]

    def test_default_comes_from_settings(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "max_context_messages", 2)
        assert chat_routes.truncate_messages([1, 2, 3]) == [2, 3]


class TestCors:
    def test_allowed_origin_gets_header(self, client):
        r = client.options(
            "/chat/stream",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert r.headers.get("access-control-allow-origin") == "https://example.com"

    def test_unknown_origin_gets_no_header(self, client):
        r = client.options(
            "/chat/stream",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert "access-control-allow-origin" not in r.headers
