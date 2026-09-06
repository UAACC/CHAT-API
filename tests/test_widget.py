"""The embeddable widget endpoints and the same-host origin rule they rely on."""

from tests.conftest import chat_payload


class TestWidgetScript:
    def test_served_as_javascript_with_caching(self, client):
        r = client.get("/widget.js")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/javascript")
        assert r.headers["cache-control"] == "public, max-age=3600"
        assert r.headers["etag"].startswith('"')
        assert "window.ChatWidget" in r.text
        assert "/chat/stream" in r.text

    def test_etag_revalidation(self, client):
        etag = client.get("/widget.js").headers["etag"]
        r = client.get("/widget.js", headers={"If-None-Match": etag})
        assert r.status_code == 304
        assert r.content == b""

    def test_size_budget(self, client):
        """Keep the widget small: it loads on every page of every site."""
        raw = client.get("/widget.js", headers={"Accept-Encoding": "identity"})
        assert "content-encoding" not in raw.headers
        assert len(raw.content) < 40_000

        gz = client.get("/widget.js", headers={"Accept-Encoding": "gzip"})
        assert gz.headers["content-encoding"] == "gzip"
        assert gz.headers["vary"] == "Accept-Encoding"
        assert int(gz.headers["content-length"]) < 10_000
        assert gz.text == raw.text  # the client transparently decompresses

    def test_hidden_from_openapi(self, client):
        paths = client.get("/openapi.json").json()["paths"]
        assert "/widget.js" not in paths


class TestDemoPage:
    def test_html_that_embeds_the_widget(self, client):
        r = client.get("/widget/demo")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        assert "/widget.js" in r.text
        assert "/health" in r.text


class TestSameHostOrigin:
    """Pages served by the API itself must be able to call it."""

    def test_same_host_origin_uses_default_tenant(self, two_tenant_client, monkeypatch):
        from app.services import llm_service
        from langchain_core.language_models.fake_chat_models import FakeListChatModel

        seen = []

        def fake(tenant):
            seen.append(tenant.id)
            return FakeListChatModel(responses=["ok"])

        monkeypatch.setattr(llm_service, "get_llm", fake)
        r = two_tenant_client.post("/chat", json=chat_payload("hi"), headers={"Origin": "http://testserver"})
        assert r.status_code == 200
        assert seen == ["studio"]

    def test_same_host_origin_with_explicit_site(self, two_tenant_client, monkeypatch):
        from app.services import llm_service
        from langchain_core.language_models.fake_chat_models import FakeListChatModel

        seen = []

        def fake(tenant):
            seen.append(tenant.id)
            return FakeListChatModel(responses=["ok"])

        monkeypatch.setattr(llm_service, "get_llm", fake)
        r = two_tenant_client.post(
            "/chat", json=chat_payload("hi", site="consultancy"), headers={"Origin": "http://testserver"}
        )
        assert r.status_code == 200
        assert seen == ["consultancy"]

    def test_foreign_origin_still_rejected(self, two_tenant_client):
        r = two_tenant_client.post("/chat", json=chat_payload("hi"), headers={"Origin": "https://evil.example"})
        assert r.status_code == 403
