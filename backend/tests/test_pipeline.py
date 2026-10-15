"""Pipeline tests against a real Postgres. No models load."""

from pathlib import Path
from uuid import uuid4

import pytest
from psycopg import errors

from app.db import queries
from app.ingest.extract import UnsupportedContentType
from app.ingest.pipeline import ingest_file
from app.retrieval.config import load_retrieval_config
from tests.fakes import FakeEmbedder, whitespace_tokens

_MODEL = "fake-model"


def _config():
    return load_retrieval_config()


def _embedder():
    return FakeEmbedder(dim=8)


def test_ingest_markdown_twice_dedupes(db, collection, tmp_path):
    path = tmp_path / "doc.md"
    path.write_text("# Title\n\none two three four five\n", encoding="utf-8")
    config = _config()

    first = ingest_file(
        db,
        collection_id=collection,
        path=path,
        content_type="text/markdown",
        count_tokens=whitespace_tokens,
        embedder=_embedder(),
        embed_model=_MODEL,
        config=config,
    )
    second = ingest_file(
        db,
        collection_id=collection,
        path=path,
        content_type="text/markdown",
        count_tokens=whitespace_tokens,
        embedder=_embedder(),
        embed_model=_MODEL,
        config=config,
    )
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


def test_failed_extract_records_error(db, collection, tmp_path):
    path = tmp_path / "broken.bin"
    path.write_bytes(b"\x00\x01\x02")
    with pytest.raises(UnsupportedContentType):
        ingest_file(
            db,
            collection_id=collection,
            path=path,
            content_type="application/octet-stream",
            count_tokens=whitespace_tokens,
            embedder=_embedder(),
            embed_model=_MODEL,
            config=_config(),
        )
    row = db.execute(
        "SELECT status, error_message FROM sources WHERE collection_id = %(c)s",
        {"c": collection},
    ).fetchone()
    assert row["status"] == "failed"
    assert row["error_message"]


def test_failed_chunking_records_error(db, collection, tmp_path):
    path = tmp_path / "doc.txt"
    path.write_text("alpha beta gamma\n", encoding="utf-8")

    def broken_tokens(text: str) -> int:
        raise RuntimeError("tokenizer broke")

    with pytest.raises(RuntimeError, match="tokenizer broke"):
        ingest_file(
            db,
            collection_id=collection,
            path=path,
            content_type="text/plain",
            count_tokens=broken_tokens,
            embedder=_embedder(),
            embed_model=_MODEL,
            config=_config(),
        )
    row = db.execute(
        "SELECT status, error_message FROM sources WHERE collection_id = %(c)s",
        {"c": collection},
    ).fetchone()
    assert row["status"] == "failed"
    assert "tokenizer broke" in row["error_message"]


def test_raw_upload_goes_to_data_dir(db, collection, tmp_path):
    from app.config import load_settings

    path = tmp_path / "doc.txt"
    path.write_text("alpha beta gamma\n", encoding="utf-8")
    ingest_file(
        db,
        collection_id=collection,
        path=path,
        content_type="text/plain",
        count_tokens=whitespace_tokens,
        embedder=_embedder(),
        embed_model=_MODEL,
        config=_config(),
    )
    raw_path = db.execute(
        "SELECT raw_path FROM sources WHERE collection_id = %(c)s", {"c": collection}
    ).fetchone()["raw_path"]
    data_dir = load_settings().data_dir.resolve()
    assert data_dir in Path(raw_path).resolve().parents


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
