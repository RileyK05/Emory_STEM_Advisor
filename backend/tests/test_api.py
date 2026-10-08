"""HTTP API tests. Fake provider/embedder; real Postgres. No models load."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import load_settings
from app.retrieval.config import load_retrieval_config
from tests.fakes import FakeEmbedder, FakeProvider, whitespace_tokens

pytestmark = pytest.mark.db

_MODEL = "fake-model"


def _lenient_config():
    # FakeEmbedder similarity is arbitrary; disable floors so keyword routing
    # is deterministic in these tests.
    config = load_retrieval_config()
    seams = replace(config.seams, embed=replace(config.seams.embed, min_similarity=0.0))
    return replace(config, seams=seams, fusion=replace(config.fusion, min_score=0.0))


def _client(db, collection, provider=None, embedder=None):
    settings = load_settings()
    app = create_app(
        settings=settings,
        config=_lenient_config(),
        embedder=embedder or FakeEmbedder(dim=8),
        provider=provider or FakeProvider(),
    )
    app.state.override_conn = db
    return TestClient(app)


def _ingest(db, collection, tmp_path, name, text):
    from app.ingest.pipeline import ingest_file

    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return ingest_file(
        db,
        collection_id=collection,
        path=path,
        content_type="text/plain",
        count_tokens=whitespace_tokens,
        embedder=FakeEmbedder(dim=8),
        embed_model=_MODEL,
        config=_lenient_config(),
    )


def _collection_name(db, collection):
    return db.execute(
        "SELECT name FROM collections WHERE collection_id = %(c)s", {"c": collection}
    ).fetchone()["name"]


def test_health(db, collection):
    client = _client(db, collection)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_list_collections(db, collection):
    client = _client(db, collection)
    resp = client.get("/api/collections")
    assert resp.status_code == 200
    names = {item["name"] for item in resp.json()}
    assert _collection_name(db, collection) in names


def test_list_sources(db, collection, tmp_path):
    _ingest(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    client = _client(db, collection)
    resp = client.get("/api/sources", params={"collection": _collection_name(db, collection)})
    assert resp.status_code == 200
    sources = resp.json()
    assert [s["name"] for s in sources] == ["a.txt"]
    assert sources[0]["status"] == "indexed"
    assert sources[0]["chunkCount"] == 1


def test_unknown_collection_is_404(db, collection):
    client = _client(db, collection)
    resp = client.get("/api/sources", params={"collection": "no-such"})
    assert resp.status_code == 404


def test_query_returns_grounded_answer(db, collection, tmp_path):
    _ingest(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    provider = FakeProvider(text="The answer is alpha [1].")
    client = _client(db, collection, provider=provider)
    resp = client.post(
        "/api/query",
        json={"query": "alpha", "collectionId": _collection_name(db, collection)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "answer"
    assert body["answer"] == "The answer is alpha [1]."
    assert body["citations"][0]["sourceName"] == "a.txt"
    assert provider.calls == 1
    # The prompt fences the context as untrusted data.
    assert "untrusted" in body["trace"]["prompt"]["user"]
    assert body["trace"]["contextBlock"][0]["chunkId"]


def test_query_refusal_does_not_call_model(db, collection, tmp_path):
    _ingest(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    provider = FakeProvider()
    client = _client(db, collection, provider=provider)
    resp = client.post(
        "/api/query",
        json={"query": "zzzzz qqqqq nothing", "collectionId": _collection_name(db, collection)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "refusal"
    assert provider.calls == 0


def test_query_empty_string_rejected(db, collection):
    client = _client(db, collection)
    resp = client.post("/api/query", json={"query": ""})
    assert resp.status_code == 422


def test_upload_source(db, collection):
    client = _client(db, collection)
    resp = client.post(
        "/api/sources",
        files={"file": ("notes.txt", b"alpha beta gamma\n", "text/plain")},
        data={"collection": _collection_name(db, collection)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "notes.txt"
    assert body["status"] == "indexed"
    assert body["chunkCount"] == 1
    listed = client.get(
        "/api/sources", params={"collection": _collection_name(db, collection)}
    ).json()
    assert any(s["name"] == "notes.txt" for s in listed)
