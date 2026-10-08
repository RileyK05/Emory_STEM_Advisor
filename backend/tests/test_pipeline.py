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

pytestmark = pytest.mark.db

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


def test_duplicate_chunk_index_rejected(db, collection, tmp_path):
    path = tmp_path / "doc.txt"
    path.write_text("alpha beta gamma\n", encoding="utf-8")
    source_id = ingest_file(
        db,
        collection_id=collection,
        path=path,
        content_type="text/plain",
        count_tokens=whitespace_tokens,
        embedder=_embedder(),
        embed_model=_MODEL,
        config=_config(),
    )
    loc = db.execute(
        queries.get("list_locators_for_source"), {"source_id": source_id}
    ).fetchone()
    with pytest.raises(errors.UniqueViolation), db.transaction():
        db.execute(
            queries.get("insert_chunk"),
            {
                "chunk_id": uuid4(),
                "source_id": source_id,
                "locator_id": loc["locator_id"],
                "chunk_index": 0,
                "start_char": 0,
                "end_char": 5,
                "token_count": 1,
                "text": "dup",
            },
        )


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
