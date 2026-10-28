"""Retrieve: trace writing, refusal, seam failure isolation. Real Postgres."""

from dataclasses import replace

import pytest

from app.db import queries
from app.retrieval.config import load_retrieval_config
from app.retrieval.retrieve import ContractViolation, retrieve
from app.retrieval.types import Refusal, Retrieved
from tests.fakes import FakeEmbedder
from tests.helpers import ingest_text, lenient_config

_MODEL = "fake-model"


def _no_embed_config():
    config = load_retrieval_config()
    return replace(
        config,
        seams=replace(config.seams, embed=replace(config.seams.embed, enabled=False)),
    )


def _retrieve(db, collection, query, config=None, source_ids=None):
    return retrieve(
        db,
        collection_id=collection,
        query=query,
        embedder=FakeEmbedder(dim=8),
        embed_model=_MODEL,
        config=config or load_retrieval_config(),
        source_ids=source_ids,
    )


def _min_score_config(min_score, dedupe=True):
    config = load_retrieval_config()
    return replace(
        config,
        fusion=replace(config.fusion, min_score=min_score, dedupe=dedupe),
    )


def test_retrieve_writes_one_trace(db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    outcome = _retrieve(db, collection, "alpha", config=lenient_config())
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
    assert {"keyword_ms", "embed_ms", "prereq_ms", "fusion_ms", "total_ms"} <= (
        outcome.trace.timings.keys())
    assert "trace_write_ms" in outcome.trace.timings
    stored = db.execute(
        "SELECT timings FROM retrieval_traces WHERE trace_id = %(t)s",
        {"t": outcome.trace.trace_id},
    ).fetchone()["timings"]
    assert {"keyword_ms", "embed_ms", "prereq_ms"} <= stored.keys()


def test_refusal_on_no_matches(db, collection, tmp_path):
    # With embeddings off, a query matching no keyword and no course code has
    # zero grounded candidates and the refusal rule (invariant 4) applies.
    ingest_text(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    outcome = _retrieve(db, collection, "zzzzz qqqqq", config=_no_embed_config())
    assert isinstance(outcome, Refusal)
    assert outcome.reason == "no relevant material found"
    traces = db.execute(
        "SELECT refused FROM retrieval_traces WHERE trace_id = %(t)s",
        {"t": outcome.trace.trace_id},
    ).fetchone()
    assert traces["refused"] is True


def test_disabled_embed_seam_still_appears_in_trace(db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    outcome = _retrieve(db, collection, "alpha", config=_no_embed_config())
    embed = next(s for s in outcome.trace.seams if s.name == "embed")
    assert embed.state == "disabled"


def test_embedder_failure_is_isolated(db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")

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
        config=lenient_config(),
    )
    embed = next(s for s in outcome.trace.seams if s.name == "embed")
    assert embed.state == "failed"
    assert "embedder exploded" in embed.reason


def test_failing_seam_is_isolated(db, collection, tmp_path, monkeypatch):
    ingest_text(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")

    import app.retrieval.retrieve as retrieve_mod

    def boom(*args, **kwargs):
        raise RuntimeError("seam exploded")

    monkeypatch.setattr(retrieve_mod, "keyword_seam", boom)
    outcome = _retrieve(db, collection, "alpha", config=lenient_config())
    assert isinstance(outcome, Retrieved)
    keyword = next(s for s in outcome.trace.seams if s.name == "keyword")
    assert keyword.state == "failed"
    assert "seam exploded" in keyword.reason


def test_sql_statement_error_in_one_seam_is_isolated(db, collection, tmp_path, monkeypatch):
    # A real PostgreSQL statement error must roll back only the failing seam.
    # Before savepoint isolation the shared transaction stayed aborted, so the
    # remaining seams, the chunk-detail read, and the trace write all failed.
    ingest_text(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")

    import app.retrieval.retrieve as retrieve_mod

    def bad_sql(conn, *args, **kwargs):
        conn.execute("SELECT 1 / 0")  # DivisionByZero at the database
        raise AssertionError("the failing statement must abort this seam")

    monkeypatch.setattr(retrieve_mod, "keyword_seam", bad_sql)
    outcome = _retrieve(db, collection, "alpha", config=lenient_config())

    keyword = next(s for s in outcome.trace.seams if s.name == "keyword")
    assert keyword.state == "failed"
    assert "division by zero" in keyword.reason
    assert isinstance(outcome, Retrieved)  # the embed seam still supplied evidence
    # The connection stayed usable and the trace was committed.
    assert db.execute("SELECT 1 AS one").fetchone()["one"] == 1
    row = db.execute(
        "SELECT COUNT(*) AS n FROM retrieval_traces WHERE trace_id = %(t)s",
        {"t": outcome.trace.trace_id},
    ).fetchone()
    assert row["n"] == 1


def test_non_indexed_source_is_excluded(db, collection, tmp_path):
    source_id = ingest_text(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    db.execute(
        "UPDATE sources SET status = 'chunked' WHERE source_id = %(s)s",
        {"s": source_id},
    )
    db.commit()
    outcome = _retrieve(db, collection, "alpha", config=_no_embed_config())
    assert isinstance(outcome, Refusal)


def test_soft_source_selection_never_expands_exact_source_scope(db, collection, tmp_path):
    allowed = ingest_text(db, collection, tmp_path, "allowed.txt", "alpha eligible evidence")
    ingest_text(
        db,
        collection,
        tmp_path,
        "out_of_scope.txt",
        "alpha alpha alpha alpha alpha unrelated stronger keyword evidence",
    )

    outcome = _retrieve(
        db,
        collection,
        "alpha",
        config=_no_embed_config(),
        source_ids=[allowed],
    )

    assert isinstance(outcome, Retrieved)
    assert outcome.chunks
    assert {chunk.source_id for chunk in outcome.chunks} == {allowed}


def test_cited_chunks_have_locators(db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    outcome = _retrieve(db, collection, "alpha", config=lenient_config())
    assert isinstance(outcome, Retrieved)
    for chunk in outcome.chunks:
        assert chunk.locator_label
        assert chunk.source_name == "a.txt"
        row = db.execute(
            queries.get("cited_chunk_details"), {
                "chunk_id": chunk.chunk_id, "workspace_key": None,
            }
        ).fetchone()
        assert row is not None


def test_contract_violation_raises_for_missing_chunk(db, collection, tmp_path, monkeypatch):
    from uuid import uuid4

    from app.retrieval import retrieve as retrieve_mod
    from app.retrieval.fusion import FusedChunk

    ingest_text(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")

    def fake_fuse(results, cited_set_size, embed_only_quota, **kwargs):
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
    ingest_text(db, collection, tmp_path, "strong.txt", "alpha alpha alpha\n")
    ingest_text(db, collection, tmp_path, "weak.txt", "alpha omega\n")
    outcome = _retrieve(db, collection, "alpha", config=_min_score_config(0.5))
    assert isinstance(outcome, Retrieved)
    assert {c.source_name for c in outcome.chunks} == {"strong.txt"}


def test_duplicate_passages_are_cited_once(db, collection, tmp_path):
    # Two files with different bytes but the same chunk text (trailing
    # whitespace is stripped), both matching. Dedupe cites the passage once.
    ingest_text(db, collection, tmp_path, "one.txt", "shared passage words here\n")
    ingest_text(db, collection, tmp_path, "two.txt", "shared passage words here\n\n\n")
    deduped = _retrieve(db, collection, "shared passage", config=_min_score_config(0.0))
    assert isinstance(deduped, Retrieved)
    texts = [c.text.strip() for c in deduped.chunks]
    assert len(texts) == len(set(texts))

    kept = _retrieve(
        db, collection, "shared passage", config=_min_score_config(0.0, dedupe=False)
    )
    assert isinstance(kept, Retrieved)
    assert len([c for c in kept.chunks if c.text.strip() == "shared passage words here"]) == 2


def test_duplicate_passages_leave_room_for_other_relevant_evidence(db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "one.txt", "alpha alpha alpha repeated evidence\n")
    ingest_text(db, collection, tmp_path, "two.txt", "alpha alpha alpha repeated evidence\n\n")
    ingest_text(db, collection, tmp_path, "other.txt", "alpha distinct supporting material")
    config = _no_embed_config()
    config = replace(config, fusion=replace(config.fusion, cited_set_size=2, min_score=0.0))
    outcome = _retrieve(db, collection, "alpha", config=config)
    assert isinstance(outcome, Retrieved)
    assert len(outcome.chunks) == 2
    assert any("distinct supporting" in c.text for c in outcome.chunks)
    assert {c.rank for c in outcome.chunks} == {1, 2}
    contributions = db.execute(
        "SELECT chunk_id, rank FROM trace_contributions WHERE trace_id = %(t)s ORDER BY rank",
        {"t": outcome.trace.trace_id},
    ).fetchall()
    assert [(row["chunk_id"], row["rank"]) for row in contributions] == [
        (c.chunk_id, c.rank) for c in outcome.chunks
    ]

def test_contract_rejects_a_foreign_collection_chunk(db, collection, tmp_path, monkeypatch):
    from uuid import uuid4

    from app.db import queries
    from app.retrieval import retrieve as retrieve_mod
    from app.retrieval.fusion import FusedChunk
    foreign = uuid4()
    db.execute(queries.get("insert_collection"), {"collection_id": foreign, "name": str(foreign)})
    db.commit()
    source = ingest_text(db, foreign, tmp_path, "foreign.txt", "alpha foreign material")
    row = db.execute(queries.get("list_chunks_for_source"), {"source_id": source}).fetchone()
    monkeypatch.setattr(retrieve_mod, "fuse", lambda *args, **kwargs: [
        FusedChunk(chunk_id=row["chunk_id"], source_id=source, chunk_index=row["chunk_index"],
                   seams=("keyword",), normalized_score=1.0, source_slot=1, rank=1)])
    with pytest.raises(ContractViolation):
        _retrieve(db, collection, "alpha")


def test_contract_rejects_a_mismatched_source_identity(db, collection, tmp_path, monkeypatch):
    from uuid import uuid4

    from app.db import queries
    from app.retrieval import retrieve as retrieve_mod
    from app.retrieval.fusion import FusedChunk
    source = ingest_text(db, collection, tmp_path, "local.txt", "alpha local material")
    row = db.execute(queries.get("list_chunks_for_source"), {"source_id": source}).fetchone()
    monkeypatch.setattr(retrieve_mod, "fuse", lambda *args, **kwargs: [
        FusedChunk(chunk_id=row["chunk_id"], source_id=uuid4(), chunk_index=row["chunk_index"],
                   seams=("keyword",), normalized_score=1.0, source_slot=1, rank=1)])
    with pytest.raises(ContractViolation):
        _retrieve(db, collection, "alpha")
