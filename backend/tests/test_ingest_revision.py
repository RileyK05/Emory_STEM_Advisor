"""Last-good source replacement and revision fencing against real Postgres."""

import pytest

from app.db import queries
from app.ingest.pipeline import ingest_file
from tests.fakes import FakeEmbedder, whitespace_tokens
from tests.helpers import api_client, lenient_config


def ingest_text(db, collection, tmp_path, name="private.txt", raw=b"alpha beta gamma\n",
            *, embedder=None, force=False, callback=None, expected=None, origin_key=None):
    path = tmp_path / name
    path.write_bytes(raw)
    return ingest_file(
        db, collection_id=collection, path=path, content_type="text/plain",
        count_tokens=whitespace_tokens, embedder=embedder or FakeEmbedder(dim=8),
        embed_model="test-embed", config=lenient_config(), workspace_key="development",
        origin_key=origin_key,
        force=force, on_chunk_transaction=callback, expected_index_revision=expected,
    )


def _source(db, source_id):
    return db.execute("SELECT status, index_revision, error_message FROM sources WHERE source_id=%(id)s",
                      {"id": source_id}).fetchone()


def _chunks(db, source_id):
    return db.execute(queries.get("list_chunks_for_source"), {"source_id": source_id}).fetchall()


def test_reindex_callback_failure_keeps_last_good_index_and_error(db, collection, tmp_path):
    source_id = ingest_text(db, collection, tmp_path)
    before = _chunks(db, source_id)
    revision = _source(db, source_id)["index_revision"]

    def fail_after_replacement_starts(_conn, _source, _chunk_ids):
        raise RuntimeError("replacement callback failed")

    with pytest.raises(RuntimeError, match="callback failed"):
        ingest_text(db, collection, tmp_path, force=True, callback=fail_after_replacement_starts,
                expected=revision)
    state = _source(db, source_id)
    assert state["status"] == "indexed"
    assert state["index_revision"] == revision + 1
    assert "replacement callback failed" in state["error_message"]
    assert _chunks(db, source_id) == before


def test_stale_reindex_completion_cannot_overwrite_newer_attempt(db, collection, tmp_path):
    source_id = ingest_text(db, collection, tmp_path)
    original_chunks = _chunks(db, source_id)
    revision = _source(db, source_id)["index_revision"]
    nested_done = False

    class RacingEmbedder:
        def embed_documents(self, texts):
            nonlocal nested_done
            if not nested_done:
                nested_done = True
                ingest_text(db, collection, tmp_path, force=True, expected=revision + 1)
            return FakeEmbedder(dim=8).embed_documents(texts)

    # Outer claims revision + 1. During its expensive embed, the newer attempt
    # claims revision + 2 and publishes; the stale outer result is then ignored.
    ingest_text(db, collection, tmp_path, force=True, embedder=RacingEmbedder(), expected=revision)
    state = _source(db, source_id)
    assert state["status"] == "indexed"
    assert state["index_revision"] == revision + 2
    assert _chunks(db, source_id) == original_chunks


def test_failed_new_source_has_no_chunks_and_is_marked_failed(db, collection, tmp_path):
    class BrokenEmbedder:
        def embed_documents(self, _texts):
            raise RuntimeError("embedding down")

    with pytest.raises(RuntimeError):
        ingest_text(db, collection, tmp_path, embedder=BrokenEmbedder())
    row = db.execute("SELECT status, error_message FROM sources WHERE workspace_key='development' "
                     "ORDER BY created_at DESC LIMIT 1").fetchone()
    assert row["status"] == "failed"
    assert "embedding down" in row["error_message"]


def test_origin_replacement_keeps_last_good_current_until_new_index_is_ready(
    db, collection, tmp_path,
):
    origin = "programs:department:chemistry-emory-edu-bs"
    first = ingest_text(db, collection, tmp_path, name="first.md", raw=b"old requirements",
                        origin_key=origin)
    first_chunks = _chunks(db, first)

    class BrokenEmbedder:
        def embed_documents(self, _texts):
            raise RuntimeError("replacement embedding failed")

    with pytest.raises(RuntimeError, match="replacement embedding failed"):
        ingest_text(db, collection, tmp_path, name="second.md", raw=b"new requirements",
                    origin_key=origin, embedder=BrokenEmbedder())
    first_state = db.execute("SELECT is_current FROM sources WHERE source_id=%s", (first,)).fetchone()
    assert first_state["is_current"] is True
    assert _chunks(db, first) == first_chunks

    second = ingest_text(db, collection, tmp_path, name="second.md", raw=b"new requirements",
                         origin_key=origin)
    assert second != first
    current = db.execute(
        "SELECT source_id FROM sources WHERE collection_id=%s AND origin_key=%s AND is_current",
        (collection, origin),
    ).fetchall()
    assert [row["source_id"] for row in current] == [second]
    assert _chunks(db, first) == first_chunks
    assert db.execute("SELECT is_current FROM sources WHERE source_id=%s", (first,)).fetchone()[
        "is_current"
    ] is False


def test_reindex_endpoint_is_owner_scoped_and_revision_checked(db, collection, tmp_path):
    source_id = ingest_text(db, collection, tmp_path)
    client = api_client(db, collection)
    state = _source(db, source_id)
    response = client.post(f"/api/sources/{source_id}/reindex", json={
        "expectedIndexRevision": state["index_revision"] - 1,
    })
    assert response.status_code == 409
    assert _source(db, source_id)["status"] == "indexed"
    success = client.post(f"/api/sources/{source_id}/reindex", json={
        "expectedIndexRevision": state["index_revision"],
    })
    assert success.status_code == 200, success.text
    assert success.json()["indexRevision"] == state["index_revision"] + 1
    assert success.json()["name"] == "private.txt"
    assert success.json()["canReindex"] is True

    # A shared (official) source is visible for retrieval but never owner-reindexable.
    shared_path = tmp_path / "shared.txt"
    shared_path.write_text("shared source", encoding="utf-8")
    shared_id = ingest_file(
        db, collection_id=collection, path=shared_path, content_type="text/plain",
        count_tokens=whitespace_tokens, embedder=FakeEmbedder(dim=8),
        embed_model="test-embed", config=lenient_config(), workspace_key=None,
    )
    denied = client.post(f"/api/sources/{shared_id}/reindex", json={"expectedIndexRevision": 0})
    assert denied.status_code == 404
    name = db.execute("SELECT name FROM collections WHERE collection_id=%s", (collection,)).fetchone()["name"]
    sources = client.get("/api/sources", params={"collection": name}).json()
    assert next(item for item in sources if item["id"] == str(shared_id))["canReindex"] is False
    assert next(item for item in sources if item["id"] == str(source_id))["canReindex"] is True
