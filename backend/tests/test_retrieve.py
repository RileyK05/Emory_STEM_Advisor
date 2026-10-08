"""Retrieve: trace writing, refusal, seam failure isolation. Real Postgres."""

from dataclasses import replace

import pytest

from app.db import queries
from app.ingest.pipeline import ingest_file
from app.retrieval.config import load_retrieval_config
from app.retrieval.retrieve import ContractViolation, retrieve
from app.retrieval.types import Refusal, Retrieved
from tests.fakes import FakeEmbedder, whitespace_tokens

pytestmark = pytest.mark.db

_MODEL = "fake-model"


def _no_embed_config():
    config = load_retrieval_config()
    return replace(
        config,
        seams=replace(config.seams, embed=replace(config.seams.embed, enabled=False)),
    )


def _lenient_config():
    # Embeddings on with no floors, so keyword/embed plumbing (seam isolation,
    # trace writing) is what a test observes. FakeEmbedder's similarity to a
    # query is arbitrary, so a real floor would make these tests flaky.
    config = load_retrieval_config()
    seams = replace(
        config.seams, embed=replace(config.seams.embed, min_similarity=0.0)
    )
    return replace(
        config, seams=seams, fusion=replace(config.fusion, min_score=0.0)
    )


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


def _retrieve(db, collection, query, config=None):
    return retrieve(
        db,
        collection_id=collection,
        query=query,
        embedder=FakeEmbedder(dim=8),
        embed_model=_MODEL,
        config=config or load_retrieval_config(),
    )


def _min_score_config(min_score, dedupe=True):
    config = load_retrieval_config()
    return replace(
        config,
        fusion=replace(config.fusion, min_score=min_score, dedupe=dedupe),
    )


def test_retrieve_writes_one_trace(db, collection, tmp_path):
    _ingest(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    outcome = _retrieve(db, collection, "alpha", config=_lenient_config())
    assert isinstance(outcome, Retrieved)
    traces = db.execute(
        "SELECT COUNT(*) AS n FROM retrieval_traces WHERE collection_id = %(c)s",
        {"c": collection},
    ).fetchone()
    assert traces["n"] == 1
    seams = db.execute(
        "SELECT seam FROM trace_seams WHERE trace_id = %(t)s ORDER BY seam",
        {"t": outcome.trace.trace_id},
    ).fetchall()
    assert {row["seam"] for row in seams} == {"keyword", "embed", "prereq"}


def test_refusal_on_no_matches(db, collection, tmp_path):
    # With embeddings off, a query matching no keyword and no course code has
    # zero grounded candidates and the refusal rule (invariant 4) applies.
    _ingest(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    outcome = _retrieve(db, collection, "zzzzz qqqqq", config=_no_embed_config())
    assert isinstance(outcome, Refusal)
    assert outcome.reason == "no relevant material found"
    traces = db.execute(
        "SELECT refused FROM retrieval_traces WHERE trace_id = %(t)s",
        {"t": outcome.trace.trace_id},
    ).fetchone()
    assert traces["refused"] is True


def test_every_trace_has_a_row_even_for_refusals(db, collection, tmp_path):
    _ingest(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    _retrieve(db, collection, "nothingmatcheshere")
    count = db.execute(
        "SELECT COUNT(*) AS n FROM retrieval_traces WHERE collection_id = %(c)s",
        {"c": collection},
    ).fetchone()
    assert count["n"] == 1


def test_disabled_embed_seam_still_appears_in_trace(db, collection, tmp_path):
    _ingest(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    outcome = _retrieve(db, collection, "alpha", config=_no_embed_config())
    embed = next(s for s in outcome.trace.seams if s.name == "embed")
    assert embed.state == "disabled"


def test_embedder_failure_is_isolated(db, collection, tmp_path):
    _ingest(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")

    class _BrokenEmbedder:
        def embed_documents(self, texts):
            raise RuntimeError("no model")

        def embed_query(self, text):
            raise RuntimeError("embedder exploded")

    outcome = retrieve(
        db,
        collection_id=collection,
        query="alpha",
        embedder=_BrokenEmbedder(),
        embed_model=_MODEL,
        config=_lenient_config(),
    )
    embed = next(s for s in outcome.trace.seams if s.name == "embed")
    assert embed.state == "failed"
    assert "embedder exploded" in embed.reason


def test_failing_seam_is_isolated(db, collection, tmp_path, monkeypatch):
    _ingest(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")

    import app.retrieval.retrieve as retrieve_mod

    def boom(*args, **kwargs):
        raise RuntimeError("seam exploded")

    monkeypatch.setattr(retrieve_mod, "keyword_seam", boom)
    outcome = _retrieve(db, collection, "alpha", config=_lenient_config())
    assert isinstance(outcome, Retrieved)
    keyword = next(s for s in outcome.trace.seams if s.name == "keyword")
    assert keyword.state == "failed"
    assert "seam exploded" in keyword.reason


def test_non_indexed_source_is_excluded(db, collection, tmp_path):
    source_id = _ingest(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    db.execute(
        "UPDATE sources SET status = 'chunked' WHERE source_id = %(s)s",
        {"s": source_id},
    )
    db.commit()
    outcome = _retrieve(db, collection, "alpha", config=_no_embed_config())
    assert isinstance(outcome, Refusal)


def test_cited_chunks_have_locators(db, collection, tmp_path):
    _ingest(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    outcome = _retrieve(db, collection, "alpha", config=_lenient_config())
    assert isinstance(outcome, Retrieved)
    for chunk in outcome.chunks:
        assert chunk.locator_label
        assert chunk.source_name == "a.txt"
        row = db.execute(
            queries.get("cited_chunk_details"), {"chunk_id": chunk.chunk_id}
        ).fetchone()
        assert row is not None


def test_contract_violation_raises_for_missing_chunk(db, collection, tmp_path, monkeypatch):
    from uuid import uuid4

    from app.retrieval import retrieve as retrieve_mod
    from app.retrieval.fusion import FusedChunk

    _ingest(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")

    def fake_fuse(results, cited_set_size, embed_only_quota):
        return [
            FusedChunk(
                chunk_id=uuid4(),
                source_id=uuid4(),
                chunk_index=0,
                seams=("keyword",),
                normalized_score=1.0,
                source_slot=1,
                rank=1,
            )
        ]

    monkeypatch.setattr(retrieve_mod, "fuse", fake_fuse)
    with pytest.raises(ContractViolation):
        _retrieve(db, collection, "alpha")


def test_fused_score_floor_drops_weak_candidates(db, collection, tmp_path):
    # Two sources; keyword min-max makes the weaker source score 0.0. A floor
    # of 0.5 drops it, so only the top source is cited.
    _ingest(db, collection, tmp_path, "strong.txt", "alpha alpha alpha\n")
    _ingest(db, collection, tmp_path, "weak.txt", "alpha omega\n")
    outcome = _retrieve(db, collection, "alpha", config=_min_score_config(0.5))
    assert isinstance(outcome, Retrieved)
    assert {c.source_name for c in outcome.chunks} == {"strong.txt"}


def test_duplicate_passages_are_cited_once(db, collection, tmp_path):
    # Two files with different bytes but the same chunk text (trailing
    # whitespace is stripped), both matching. Dedupe cites the passage once.
    _ingest(db, collection, tmp_path, "one.txt", "shared passage words here\n")
    _ingest(db, collection, tmp_path, "two.txt", "shared passage words here\n\n\n")
    deduped = _retrieve(db, collection, "shared passage", config=_min_score_config(0.0))
    assert isinstance(deduped, Retrieved)
    texts = [c.text.strip() for c in deduped.chunks]
    assert len(texts) == len(set(texts))

    kept = _retrieve(
        db, collection, "shared passage", config=_min_score_config(0.0, dedupe=False)
    )
    assert isinstance(kept, Retrieved)
    assert len([c for c in kept.chunks if c.text.strip() == "shared passage words here"]) == 2
