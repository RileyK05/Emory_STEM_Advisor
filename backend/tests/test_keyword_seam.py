"""Keyword seam tests against a real Postgres. No models load."""

import pytest

from app.retrieval.config import KeywordSeamConfig
from app.retrieval.seams.keyword import keyword_seam
from tests.helpers import ingest_text


def _config(**overrides):
    base = {"enabled": True, "limit": 40, "min_token_len": 2}
    base.update(overrides)
    return KeywordSeamConfig(**base)


def test_any_single_word_matches(db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "a.txt", "alpha beta\n")
    ingest_text(db, collection, tmp_path, "b.txt", "gamma delta\n")
    result = keyword_seam(db, collection, "beta delta", _config())
    sources = {c.source_id for c in result.candidates}
    assert len(sources) == 2


@pytest.mark.parametrize(
    "query",
    ["f(x) = x^2", "a <-> b & !c", "''", "((", ":*", "!!!", "a & | b"],
)
def test_hostile_queries_do_not_error(db, collection, tmp_path, query):
    ingest_text(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    result = keyword_seam(db, collection, query, _config())
    assert result.state in {"active", "dormant"}


def test_no_searchable_terms_is_dormant(db, collection):
    result = keyword_seam(db, collection, "!  @  #", _config())
    assert result.state == "dormant"
    assert result.reason == "no searchable terms"


def test_disabled_in_config(db, collection):
    result = keyword_seam(db, collection, "alpha", _config(enabled=False))
    assert result.state == "disabled"


def test_failed_source_chunks_never_appear(db, collection, tmp_path):
    source_id = ingest_text(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    db.execute("UPDATE sources SET status = 'failed' WHERE source_id = %(s)s", {"s": source_id})
    db.commit()
    result = keyword_seam(db, collection, "alpha", _config())
    assert result.candidates == []


def test_ordering_is_deterministic(db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "a.txt", "alpha beta\n")
    ingest_text(db, collection, tmp_path, "b.txt", "alpha gamma\n")
    first = keyword_seam(db, collection, "alpha", _config())
    second = keyword_seam(db, collection, "alpha", _config())
    assert [(c.source_id, c.chunk_index) for c in first.candidates] == [
        (c.source_id, c.chunk_index) for c in second.candidates
    ]
    # Within one source, chunks are ordered by chunk_index ascending.
    per_source: dict = {}
    for cand in first.candidates:
        per_source.setdefault(cand.source_id, []).append(cand.chunk_index)
    for indices in per_source.values():
        assert indices == sorted(indices)


def test_min_token_len_respected(db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "a.txt", "alpha to beta\n")
    result = keyword_seam(db, collection, "to", _config(min_token_len=3))
    assert result.state == "dormant"
