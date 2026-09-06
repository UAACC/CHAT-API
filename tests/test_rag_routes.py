"""Knowledge-base routes: admin token and the crawl endpoint (services mocked)."""

import pytest

from app.config import get_settings
from app.routes import rag as rag_routes
from app.services.crawler import CrawlResult, Page


@pytest.fixture
def admin(monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_token", "s3cret")
    return {"Authorization": "Bearer s3cret"}


@pytest.fixture
def fake_documents(monkeypatch):
    """Record document_service calls instead of touching GCS/Pinecone."""
    calls = {"deleted": [], "processed": []}

    async def delete_document(document_id, namespace="__default__", storage_prefix=None):
        calls["deleted"].append((document_id, namespace, storage_prefix))
        return {"document_id": document_id}

    async def process_document(document_id, filename, content, content_type, namespace="__default__",
                               storage_prefix=None, extra_metadata=None):
        calls["processed"].append({
            "document_id": document_id, "filename": filename, "content": content.decode("utf-8"),
            "namespace": namespace, "storage_prefix": storage_prefix, "extra_metadata": extra_metadata,
        })
        return {"document_id": document_id, "filename": filename, "file_size": len(content),
                "chunk_count": 3, "vector_count": 3}

    monkeypatch.setattr(rag_routes.document_service, "delete_document", delete_document)
    monkeypatch.setattr(rag_routes.document_service, "process_document", process_document)
    return calls


@pytest.fixture
def fake_crawl(monkeypatch):
    async def crawl_site(url, max_pages=30, max_depth=3):
        return CrawlResult(
            start_url="https://studio.example/",
            pages=[
                Page(url="https://studio.example/", title="Studio", text="We teach art. " * 20, depth=0),
                Page(url="https://studio.example/classes", title="Classes", text="Watercolor and more. " * 20, depth=1),
            ],
            skipped=[{"url": "https://studio.example/x.pdf", "reason": "not html"}],
        )

    monkeypatch.setattr(rag_routes.crawler, "crawl_site", crawl_site)


class TestAdminToken:
    def test_open_when_unset(self, two_tenant_client, fake_documents, fake_crawl):
        assert get_settings().admin_token == ""
        r = two_tenant_client.post("/rag/crawl", params={"site": "studio"}, json={"url": "https://studio.example"})
        assert r.status_code == 200

    def test_missing_token_is_401(self, two_tenant_client, admin, fake_documents, fake_crawl):
        r = two_tenant_client.post("/rag/crawl", params={"site": "studio"}, json={"url": "https://studio.example"})
        assert r.status_code == 401
        assert r.headers["www-authenticate"] == "Bearer"

    def test_wrong_token_is_401(self, two_tenant_client, admin, fake_documents, fake_crawl):
        r = two_tenant_client.post(
            "/rag/crawl", params={"site": "studio"}, json={"url": "https://studio.example"},
            headers={"Authorization": "Bearer nope"},
        )
        assert r.status_code == 401

    def test_delete_requires_token(self, two_tenant_client, admin):
        r = two_tenant_client.delete("/rag/documents/abc", params={"site": "studio"})
        assert r.status_code == 401

    def test_upload_requires_token(self, two_tenant_client, admin):
        r = two_tenant_client.post("/rag/documents/upload", params={"site": "studio"}, files={"file": ("a.txt", b"hello")})
        assert r.status_code == 401

    def test_reads_stay_open(self, two_tenant_client, admin, monkeypatch):
        async def none(prefix=None):
            return []

        monkeypatch.setattr(rag_routes.storage_service, "list_documents", none)
        assert two_tenant_client.get("/rag/documents", params={"site": "studio"}).status_code == 200


class TestCrawlEndpoint:
    def test_indexes_each_page_in_tenant_scope(self, two_tenant_client, admin, fake_documents, fake_crawl):
        r = two_tenant_client.post(
            "/rag/crawl", params={"site": "studio"}, json={"url": "https://studio.example", "max_pages": 10},
            headers=admin,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["site"] == "studio"
        assert body["pages_indexed"] == 2
        assert body["chunks"] == 6
        assert body["skipped"] == [{"url": "https://studio.example/x.pdf", "reason": "not html"}]
        assert [p["url"] for p in body["pages"]] == ["https://studio.example/", "https://studio.example/classes"]

        processed = fake_documents["processed"]
        assert all(p["namespace"] == "studio-ns" for p in processed)
        assert all(p["storage_prefix"].endswith("/studio") for p in processed)
        assert processed[0]["filename"] == "index.txt"
        assert processed[1]["filename"] == "classes.txt"
        assert processed[0]["content"].startswith("Studio\n\n")
        assert processed[1]["extra_metadata"] == {"url": "https://studio.example/classes", "title": "Classes"}
        assert all(p["document_id"].startswith("url-") for p in processed)

    def test_recrawl_replaces_previous_page_documents(self, two_tenant_client, admin, fake_documents, fake_crawl):
        two_tenant_client.post("/rag/crawl", params={"site": "studio"}, json={"url": "https://studio.example"}, headers=admin)
        deleted_ids = [d[0] for d in fake_documents["deleted"]]
        processed_ids = [p["document_id"] for p in fake_documents["processed"]]
        assert deleted_ids == processed_ids

    def test_requires_knowledge_base(self, two_tenant_client, admin, fake_documents, fake_crawl):
        r = two_tenant_client.post("/rag/crawl", params={"site": "consultancy"}, json={"url": "https://x.example"}, headers=admin)
        assert r.status_code == 400

    def test_rejects_non_http_url(self, two_tenant_client, admin, fake_documents, fake_crawl):
        r = two_tenant_client.post("/rag/crawl", params={"site": "studio"}, json={"url": "ftp://x"}, headers=admin)
        assert r.status_code == 400

    def test_validates_limits(self, two_tenant_client, admin):
        r = two_tenant_client.post("/rag/crawl", params={"site": "studio"}, json={"url": "https://x", "max_pages": 0}, headers=admin)
        assert r.status_code == 422
