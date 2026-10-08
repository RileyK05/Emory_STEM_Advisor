"""Keyword seam tests against a real Postgres. No models load."""

import pytest

from app.ingest.pipeline import ingest_file
from app.retrieval.config import KeywordSeamConfig, load_retrieval_config
from app.retrieval.seams.keyword import keyword_seam
from tests.fakes import FakeEmbedder, whitespace_tokens

pytestmark = pytest.mark.db


def _ingest(db, collection, tmp_path, name, text, content_type="text/plain"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return ingest_file(
        db,
        collection_id=collection,
        path=path,
        content_type=content_type,
        count_tokens=whitespace_tokens,
        embedder=FakeEmbedder(dim=8),
        embed_model="fake-model",
        config=load_retrieval_config(),
    )


def _config(**overrides):
    base = {"enabled": True, "limit": 40, "min_token_len": 2}
    base.update(overrides)
    return KeywordSeamConfig(**base)


def test_or_match_finds_chunk_with_one_word(db, collection, tmp_path):
    _ingest(db, collection, tmp_path, "a.txt", "alpha beta gamma delta\n")
    result = keyword_seam(db, collection, "gamma", _config())
    assert result.state == "active"
    assert result.candidates


def test_any_single_word_matches(db, collection, tmp_path):
    _ingest(db, collection, tmp_path, "a.txt", "alpha beta\n")
    _ingest(db, collection, tmp_path, "b.txt", "gamma delta\n")
    result = keyword_seam(db, collection, "beta delta", _config())
    sources = {c.source_id for c in result.candidates}
    assert len(sources) == 2


@pytest.mark.parametrize(
    "query",
    ["f(x) = x^2", "a <-> b & !c", "''", "((", ":*", "!!!", "a & | b"],
)
def test_hostile_queries_do_not_error(db, collection, tmp_path, query):
    _ingest(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
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
    source_id = _ingest(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    db.execute("UPDATE sources SET status = 'failed' WHERE source_id = %(s)s", {"s": source_id})
    db.commit()
    result = keyword_seam(db, collection, "alpha", _config())
    assert result.candidates == []


def test_ordering_is_deterministic(db, collection, tmp_path):
    _ingest(db, collection, tmp_path, "a.txt", "alpha beta\n")
    _ingest(db, collection, tmp_path, "b.txt", "alpha gamma\n")
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
    _ingest(db, collection, tmp_path, "a.txt", "alpha to beta\n")
    result = keyword_seam(db, collection, "to", _config(min_token_len=3))
    assert result.state == "dormant"
