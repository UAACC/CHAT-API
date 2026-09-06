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

    def test_reads_require_token_too(self, two_tenant_client, admin, monkeypatch):
        async def none(prefix=None):
            return []

        monkeypatch.setattr(rag_routes.storage_service, "list_documents", none)
        assert two_tenant_client.get("/rag/documents", params={"site": "studio"}).status_code == 401
        assert two_tenant_client.get("/rag/documents", params={"site": "studio"}, headers=admin).status_code == 200

    def test_status_stays_open(self, two_tenant_client, admin, monkeypatch):
        monkeypatch.setattr(rag_routes.storage_service, "check_storage_health", lambda: {"status": "healthy"})
        monkeypatch.setattr(rag_routes.vector_store_service, "check_vector_store_health", lambda: {"status": "healthy"})
        assert two_tenant_client.get("/rag/status").status_code == 200


class TestConsoleEndpoints:
    def test_document_chunks(self, two_tenant_client, admin, monkeypatch):
        seen = {}

        async def fake_records(document_id, namespace="__default__"):
            seen["args"] = (document_id, namespace)
            return [
                {"id": "d_1", "chunk_index": 1, "text": "second", "url": "https://s.example/a", "title": "A"},
                {"id": "d_0", "chunk_index": 0, "text": "first", "url": None, "title": None},
            ]

        monkeypatch.setattr(rag_routes.vector_store_service, "get_document_records", fake_records)
        r = two_tenant_client.get("/rag/documents/d/chunks", params={"site": "studio"}, headers=admin)
        assert r.status_code == 200
        assert seen["args"] == ("d", "studio-ns")
        assert [c["text"] for c in r.json()["chunks"]] == ["second", "first"]  # service order preserved

    def test_search_flags_threshold(self, two_tenant_client, admin, monkeypatch):
        seen = {}

        async def fake_query(**kwargs):
            seen.update(kwargs)
            return [
                {"id": "a_0", "score": 0.42, "metadata": {"document_id": "a", "filename": "a.md", "text": "strong"}},
                {"id": "b_0", "score": 0.05, "metadata": {"document_id": "b", "filename": "b.md", "text": "weak"}},
            ]

        monkeypatch.setattr(rag_routes.vector_store_service, "query_vectors", fake_query)
        r = two_tenant_client.get("/rag/search", params={"site": "studio", "q": "trial class", "top_k": 6}, headers=admin)
        assert r.status_code == 200
        body = r.json()
        assert seen["namespace"] == "studio-ns" and seen["top_k"] == 6 and seen["min_score"] == 0.0
        assert body["threshold"] == get_settings().rag_min_score_threshold
        assert [(h["filename"], h["used"]) for h in body["hits"]] == [("a.md", True), ("b.md", False)]

    def test_search_requires_query(self, two_tenant_client, admin):
        assert two_tenant_client.get("/rag/search", params={"site": "studio"}, headers=admin).status_code == 422

    def test_notes_round_trip(self, two_tenant_client, admin, monkeypatch):
        store = {"text": ""}
        deleted, upserted, uploaded = [], [], []

        async def delete_document(document_id, namespace="__default__", storage_prefix=None):
            deleted.append((document_id, namespace, storage_prefix))

        async def upload_file(document_id, filename, content, content_type, prefix=None):
            uploaded.append((document_id, filename, prefix))
            store["text"] = content.decode("utf-8")
            return "gs://x"

        async def download_file(document_id, filename, prefix=None):
            return store["text"].encode("utf-8") if store["text"] else None

        async def upsert_records(records, namespace="__default__"):
            upserted.append((namespace, records))
            return len(records)

        from app.services import document_service as ds

        monkeypatch.setattr(ds, "delete_document", delete_document)
        monkeypatch.setattr(ds.storage_service, "upload_file", upload_file)
        monkeypatch.setattr(ds.storage_service, "download_file", download_file)
        monkeypatch.setattr(ds.vector_store_service, "upsert_records", upsert_records)

        text = "Summer term starts July 2.\n\n\nWe do not offer online classes.\n"
        r = two_tenant_client.put("/rag/notes", params={"site": "studio"}, json={"text": text}, headers=admin)
        assert r.status_code == 200
        assert r.json()["notes"] == 2
        assert deleted == [("notes", "studio-ns", f"{get_settings().gcs_prefix}/studio")]
        assert uploaded[0][:2] == ("notes", "notes.md")
        namespace, records = upserted[0]
        assert namespace == "studio-ns"
        assert [rec["_id"] for rec in records] == ["notes_0", "notes_1"]
        assert records[1]["text"] == "We do not offer online classes."
        assert records[0]["filename"] == "notes.md" and records[0]["document_id"] == "notes"

        r = two_tenant_client.get("/rag/notes", params={"site": "studio"}, headers=admin)
        assert r.json() == {"text": text, "notes": 2}

        # Empty text clears the document without indexing anything
        r = two_tenant_client.put("/rag/notes", params={"site": "studio"}, json={"text": "  \n"}, headers=admin)
        assert r.json()["notes"] == 0
        assert len(upserted) == 1 and len(deleted) == 2

    def test_console_page(self, client):
        r = client.get("/admin")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        assert "/rag/search" in r.text and "/rag/notes" in r.text


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
