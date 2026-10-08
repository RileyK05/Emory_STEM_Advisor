"""Eval runner semantics: exact labels, unresolved excluded. Real Postgres."""


import pytest

from app.ingest.pipeline import ingest_file
from app.retrieval.config import load_retrieval_config
from app.retrieval.evals import EvalCase, run_eval
from tests.fakes import FakeEmbedder, whitespace_tokens

pytestmark = pytest.mark.db

_MODEL = "fake-model"


def _ingest(db, collection, tmp_path, name, text):
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
        config=load_retrieval_config(),
    )


def test_exact_label_and_unresolved(db, collection, tmp_path):
    _ingest(db, collection, tmp_path, "doc.txt", "alpha beta gamma\n")
    name = db.execute(
        "SELECT name FROM collections WHERE collection_id = %(c)s",
        {"c": collection},
    ).fetchone()["name"]

    cases = [
        EvalCase("hit", name, "alpha", (("doc.txt", "lines 1-2"),)),
        EvalCase("miss", name, "alpha", (("doc.txt", "page 12"),)),
        EvalCase("gone", "no-such-collection", "alpha", (("doc.txt", "lines 1-2"),)),
    ]
    summary = run_eval(
        db,
        cases,
        embedder=FakeEmbedder(dim=8),
        embed_model=_MODEL,
        config=load_retrieval_config(),
    )
    assert summary.total == 3
    assert summary.resolved == 1
    assert "gone" in summary.unresolved
    # "page 12" does not exist, so it is unresolved, not a miss.
    assert "miss" in summary.unresolved


def test_page_1_does_not_match_page_12(db, collection, tmp_path):
    path = tmp_path / "doc.txt"
    path.write_text("alpha beta gamma\n", encoding="utf-8")
    _ingest(db, collection, tmp_path, "doc.txt", "alpha beta gamma\n")
    name = db.execute(
        "SELECT name FROM collections WHERE collection_id = %(c)s",
        {"c": collection},
    ).fetchone()["name"]
    cases = [EvalCase("x", name, "alpha", (("doc.txt", "page 1"),))]
    summary = run_eval(
        db,
        cases,
        embedder=FakeEmbedder(dim=8),
        embed_model=_MODEL,
        config=load_retrieval_config(),
    )
    assert "x" in summary.unresolved
