"""Pipeline tests against a real Postgres. No models load."""

from pathlib import Path
from uuid import uuid4

import pytest
from psycopg import errors

from app.config import load_settings
from app.db import queries
from app.ingest.extract import UnsupportedContentType
from app.ingest.pipeline import ingest_file
from app.retrieval.config import load_retrieval_config
from tests.fakes import FakeEmbedder, whitespace_tokens
from tests.fixtures.pdf import make_pdf

_MODEL = "fake-model"


def _ingest(db, collection, path, content_type, **overrides):
    return ingest_file(db, **{
        "collection_id": collection, "path": path, "content_type": content_type,
        "count_tokens": whitespace_tokens, "embedder": FakeEmbedder(dim=8),
        "embed_model": _MODEL, "config": load_retrieval_config(), **overrides,
    })


def test_ingest_markdown_twice_dedupes(db, collection, tmp_path):
    path = tmp_path / "doc.md"
    path.write_text("# Title\n\none two three four five\n", encoding="utf-8")
    first = _ingest(db, collection, path, "text/markdown")
    second = _ingest(db, collection, path, "text/markdown")
    assert first == second

    sources = db.execute(
        "SELECT COUNT(*) AS n FROM sources WHERE collection_id = %(c)s",
        {"c": collection},
    ).fetchone()
    assert sources["n"] == 1
    status = db.execute(
        "SELECT status FROM sources WHERE source_id = %(s)s", {"s": first}
    ).fetchone()
    assert status["status"] == "indexed"

    locator = db.execute(queries.get("list_locators_for_source"), {"source_id": first}).fetchone()
    with pytest.raises(errors.UniqueViolation), db.transaction():
        db.execute(queries.get("insert_chunk"), {
            "chunk_id": uuid4(), "source_id": first, "locator_id": locator["locator_id"],
            "chunk_index": 0, "start_char": 0, "end_char": 3, "token_count": 1, "text": "dup",
        })


class _BrokenEmbedder:
    def embed_documents(self, _texts):
        raise RuntimeError("embedding down")


def _broken_tokens(_text):
    raise RuntimeError("tokenizer broke")


@pytest.mark.parametrize("raw, content_type, overrides, error, message", [
    (b"\x00\x01\x02", "application/octet-stream", {}, UnsupportedContentType, ""),
    (b"alpha beta gamma\n", "text/plain", {"count_tokens": _broken_tokens}, RuntimeError,
     "tokenizer broke"),
    (b"alpha beta gamma\n", "text/plain", {"embedder": _BrokenEmbedder()}, RuntimeError,
     "embedding down"),
], ids=["extract", "chunking", "embedding"])
def test_failed_ingest_marks_the_source_failed(db, collection, tmp_path, raw, content_type,
                                               overrides, error, message):
    path = tmp_path / "doc"
    path.write_bytes(raw)
    with pytest.raises(error):
        _ingest(db, collection, path, content_type, **overrides)
    row = db.execute("SELECT status, error_message FROM sources WHERE collection_id = %(c)s",
                     {"c": collection}).fetchone()
    assert row["status"] == "failed"
    assert row["error_message"] and message in row["error_message"]


def test_raw_upload_goes_to_data_dir(db, collection, tmp_path):
    path = tmp_path / "doc.txt"
    path.write_text("alpha beta gamma\n", encoding="utf-8")
    _ingest(db, collection, path, "text/plain")
    raw_path = db.execute(
        "SELECT raw_path FROM sources WHERE collection_id = %(c)s", {"c": collection}
    ).fetchone()["raw_path"]
    assert load_settings().data_dir.resolve() in Path(raw_path).resolve().parents


def test_verified_provenance_publishes_with_the_indexed_version(db, collection, tmp_path):
    from app.ingest.provenance import provenance_from_manifest

    path = tmp_path / "data-engineer.md"
    path.write_text("# Data engineers\n\nThey build data pipelines.\n", encoding="utf-8")
    common = {"collection_id": collection, "path": path, "content_type": "text/markdown",
              "count_tokens": whitespace_tokens, "embedder": FakeEmbedder(dim=8),
              "embed_model": _MODEL, "config": load_retrieval_config(),
              "origin_key": "careers:bls:data-engineer"}
    plain = ingest_file(db, **common)
    db.commit()
    # An ordinary ingest makes no trusted claim.
    assert db.execute("SELECT 1 FROM source_provenance WHERE source_id = %(s)s",
                      {"s": plain}).fetchone() is None
    # Re-importing the same content through a verified manifest records it.
    metadata = {
        "source_category": "institutional_publication", "publisher": "Emory University",
        "title": "Data science careers", "institution": "Emory University",
        "issuing_unit": "Career Center", "document_roles": ["career exploration", "advising"],
        "applicability": {"audience": ["undergraduate", "prospective student"]},
        "applicability_status": "reviewed_default",
    }
    verified = ingest_file(db, **common,
                           provenance=provenance_from_manifest({"provenance": metadata}))
    row = db.execute("SELECT * FROM source_provenance WHERE source_id = %(s)s",
                     {"s": verified}).fetchone()
    assert verified == plain
    assert (row["source_category"], row["classification_origin"], row["publisher"]) == (
        "institutional_publication", "verified_import", "Emory University")
    assert len(row["content_hash"]) == 64
    assert (row["title"], row["institution"], row["issuing_unit"]) == (
        metadata["title"], metadata["institution"], metadata["issuing_unit"])
    assert row["document_roles"] == ["advising", "career exploration"]
    assert row["applicability"] == {"audience": ["prospective student", "undergraduate"]}
    assert row["applicability_status"] == "reviewed_default"
    revision = row["metadata_revision"]
    # Reordering set-like labels is the same provenance, not a reason to stale evidence.
    metadata["document_roles"].reverse()
    metadata["applicability"]["audience"].reverse()
    ingest_file(db, **common, provenance=provenance_from_manifest({"provenance": metadata}))
    unchanged = db.execute("SELECT * FROM source_provenance WHERE source_id = %(s)s",
                           {"s": verified}).fetchone()
    assert unchanged["metadata_revision"] == revision
    assert unchanged["updated_at"] == row["updated_at"]
    # A reviewed applicability correction changes metadata without changing content identity.
    metadata["applicability_status"] = "historical"
    corrected = ingest_file(db, **common,
                            provenance=provenance_from_manifest({"provenance": metadata}))
    changed = db.execute("SELECT * FROM source_provenance WHERE source_id = %(s)s",
                         {"s": corrected}).fetchone()
    assert corrected == verified
    assert changed["metadata_revision"] == revision + 1
    assert changed["applicability_status"] == "historical"
    assert changed["content_hash"] == row["content_hash"]


_MODEL = "fake-model"


@pytest.mark.parametrize("pages, expected", [
    (["First page", "", "Third page"], ["page 2:"]),  # an empty page warns and still indexes
    (["Only page", "Second page"], []),
])
def test_pdf_warnings_persist_with_the_index(db, collection, tmp_path, pages, expected):
    path = tmp_path / "doc.pdf"
    path.write_bytes(make_pdf(pages))
    source_id = _ingest(db, collection, path, "application/pdf")
    row = db.execute("SELECT status, warnings FROM sources WHERE source_id = %(s)s",
                     {"s": source_id}).fetchone()
    assert row["status"] == "indexed"
    assert [prefix for prefix in expected
            if not any(prefix in warning for warning in row["warnings"])] == []
    assert bool(row["warnings"]) == bool(expected)
