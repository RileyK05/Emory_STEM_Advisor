"""Ingest warning persistence: mixed-PDF warnings survive read-back."""


from app.ingest.pipeline import ingest_file
from app.retrieval.config import load_retrieval_config
from tests.fakes import FakeEmbedder, whitespace_tokens
from tests.fixtures.pdf import make_pdf

_MODEL = "fake-model"


def _ingest_pdf(db, collection, tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return ingest_file(
        db,
        collection_id=collection,
        path=path,
        content_type="application/pdf",
        count_tokens=whitespace_tokens,
        embedder=FakeEmbedder(dim=8),
        embed_model=_MODEL,
        config=load_retrieval_config(),
    )


def test_mixed_pdf_warnings_persist_with_the_index(db, collection, tmp_path):
    source_id = _ingest_pdf(
        db, collection, tmp_path, "mixed.pdf", make_pdf(["First page", "", "Third page"])
    )
    row = db.execute(
        "SELECT status, warnings FROM sources WHERE source_id = %(s)s",
        {"s": source_id},
    ).fetchone()
    assert row["status"] == "indexed"
    assert any("page 2:" in warning for warning in row["warnings"])


def test_clean_pdf_persists_an_empty_warning_list(db, collection, tmp_path):
    source_id = _ingest_pdf(
        db, collection, tmp_path, "clean.pdf", make_pdf(["Only page", "Second page"])
    )
    row = db.execute(
        "SELECT warnings FROM sources WHERE source_id = %(s)s",
        {"s": source_id},
    ).fetchone()
    assert row["warnings"] == []
