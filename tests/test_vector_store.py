"""Parsing of Pinecone search results across SDK versions."""

import pytest

from app.services import vector_store_service as vs


class _FakeResult:
    def __init__(self, hits):
        self._hits = hits

    def to_dict(self):
        return {"result": {"hits": self._hits}, "usage": {}}


class _FakeIndex:
    def __init__(self, hits):
        self.hits = hits
        self.calls = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResult(self.hits)


@pytest.fixture
def index(monkeypatch):
    holder = {}

    def install(hits):
        holder["index"] = _FakeIndex(hits)
        monkeypatch.setattr(vs, "get_index", lambda: holder["index"])
        return holder["index"]

    return install


async def test_sdk10_hit_shape_is_parsed(index):
    """pinecone>=10 serialises hits as id_/score_ (trailing underscore)."""
    index([
        {"id_": "doc_4", "score_": 0.23, "fields": {"text": "programs", "filename": "kb.md"}},
        {"id_": "doc_1", "score_": 0.05, "fields": {"text": "too weak", "filename": "kb.md"}},
    ])
    matches = await vs.search_by_text("which program", top_k=3, min_score=0.1)
    assert [m["id"] for m in matches] == ["doc_4"]
    assert matches[0]["score"] == pytest.approx(0.23)
    assert matches[0]["fields"]["text"] == "programs"


async def test_legacy_hit_shape_is_parsed(index):
    index([{"_id": "doc_2", "_score": 0.5, "fields": {"text": "old sdk"}}])
    matches = await vs.search_by_text("q", top_k=3, min_score=0.1)
    assert matches[0]["id"] == "doc_2"
    assert matches[0]["score"] == pytest.approx(0.5)


async def test_namespace_and_top_k_are_forwarded(index):
    fake = index([])
    await vs.search_by_text("q", top_k=7, min_score=0.0, namespace="studio")
    call = fake.calls[0]
    assert call["namespace"] == "studio"
    assert call["query"]["top_k"] == 7
    assert call["query"]["inputs"] == {"text": "q"}


async def test_query_vectors_maps_to_legacy_metadata_format(index):
    index([{"id_": "x_0", "score_": 0.9, "fields": {"text": "t", "filename": "f.md", "document_id": "x", "chunk_index": 0}}])
    results = await vs.query_vectors(query_text="q", top_k=1, min_score=0.1, namespace="ns")
    assert results == [{
        "id": "x_0",
        "score": pytest.approx(0.9),
        "metadata": {"document_id": "x", "filename": "f.md", "text": "t", "chunk_index": 0},
    }]
