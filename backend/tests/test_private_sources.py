"""Workspace ownership and leakage tests against real Postgres."""

from dataclasses import replace

import pytest

from app.db import queries
from app.ingest.pipeline import ingest_file
from app.retrieval.config import load_retrieval_config
from app.retrieval.retrieve import retrieve
from app.retrieval.types import Refusal, Retrieved
from tests.fakes import FakeEmbedder, whitespace_tokens

MODEL = "private-test-model"


def _ingest(conn, collection, path, *, workspace_key=None, embedder=None):
    return ingest_file(conn, collection_id=collection, path=path,
        content_type="text/plain", count_tokens=whitespace_tokens,
        embedder=embedder or FakeEmbedder(dim=8), embed_model=MODEL,
        config=load_retrieval_config(), workspace_key=workspace_key)


def _keyword_only_config(*, limit=20):
    config = load_retrieval_config()
    return replace(config,
        seams=replace(config.seams,
            keyword=replace(config.seams.keyword, limit=limit),
            embed=replace(config.seams.embed, enabled=False),
            prereq=replace(config.seams.prereq, enabled=False)),
        fusion=replace(config.fusion, min_score=0.0, dedupe=False))


def _retrieve(conn, collection, *, workspace_key=None, query="alpha", config=None,
              source_ids=None):
    return retrieve(conn, collection_id=collection, query=query,
        embedder=FakeEmbedder(dim=8), embed_model=MODEL,
        config=config or _keyword_only_config(), workspace_key=workspace_key,
        source_ids=source_ids)


def test_same_content_has_stable_but_distinct_shared_and_private_ids(db, collection, tmp_path):
    source = tmp_path / "same.txt"
    source.write_text("alpha beta private or shared\n", encoding="utf-8")
    shared = _ingest(db, collection, source)
    owner_a = _ingest(db, collection, source, workspace_key="workspace-a")
    owner_b = _ingest(db, collection, source, workspace_key="workspace-b")
    owner_a_again = _ingest(db, collection, source, workspace_key="workspace-a")
    assert len({shared, owner_a, owner_b}) == 3
    assert owner_a_again == owner_a

    rows = db.execute("""
        SELECT source_id, workspace_key FROM sources
        WHERE collection_id = %(collection_id)s ORDER BY workspace_key NULLS FIRST
    """, {"collection_id": collection}).fetchall()
    assert [(row["source_id"], row["workspace_key"]) for row in rows] == [
        (shared, None), (owner_a, "workspace-a"), (owner_b, "workspace-b")]
    paths = db.execute("SELECT source_id, raw_path FROM sources WHERE collection_id = %(c)s",
                       {"c": collection}).fetchall()
    assert len({row["raw_path"] for row in paths}) == 3


def test_retrieval_cites_shared_and_own_sources_never_other_workspace(db, collection, tmp_path):
    source = tmp_path / "common.txt"
    source.write_text("alpha shared phrase\n", encoding="utf-8")
    shared = _ingest(db, collection, source)
    own = _ingest(db, collection, source, workspace_key="workspace-b")
    other = _ingest(db, collection, source, workspace_key="workspace-a")

    outcome = _retrieve(db, collection, workspace_key="workspace-b")
    assert isinstance(outcome, Retrieved)
    owners = db.execute(
        "SELECT source_id, workspace_key FROM sources WHERE collection_id = %(c)s",
        {"c": collection},
    ).fetchall()
    owner_by_id = {row["source_id"]: row["workspace_key"] for row in owners}
    assert {owner_by_id[item.source_id] for item in outcome.chunks} <= {None, "workspace-b"}
    assert {item.source_id for item in outcome.chunks} <= {shared, own}

    denied = _retrieve(db, collection, workspace_key="workspace-b", source_ids=[other])
    assert isinstance(denied, Refusal)


def test_workspace_filter_runs_before_sql_limit(db, collection, tmp_path):
    private = tmp_path / "private.txt"
    private.write_text("alpha " * 80, encoding="utf-8")
    shared = tmp_path / "shared.txt"
    shared.write_text("alpha shared fact\n", encoding="utf-8")
    _ingest(db, collection, private, workspace_key="workspace-a")
    shared_id = _ingest(db, collection, shared)

    outcome = _retrieve(db, collection, workspace_key="workspace-b",
                        config=_keyword_only_config(limit=1))
    assert isinstance(outcome, Retrieved)
    assert outcome.chunks[0].source_id == shared_id


def test_embedding_failure_keeps_previous_chunk_rows(db, collection, tmp_path):
    source = tmp_path / "keep.txt"
    source.write_text("alpha known passage\n", encoding="utf-8")
    source_id = _ingest(db, collection, source)
    before = db.execute(queries.get("list_chunks_for_source"),
                        {"source_id": source_id}).fetchall()
    db.execute("UPDATE sources SET status = 'failed' WHERE source_id = %(id)s",
               {"id": source_id})
    db.commit()

    class BrokenEmbedder:
        def embed_documents(self, texts):
            raise RuntimeError("offline embedding failure")

    with pytest.raises(RuntimeError, match="offline embedding failure"):
        _ingest(db, collection, source, embedder=BrokenEmbedder())
    after = db.execute(queries.get("list_chunks_for_source"),
                       {"source_id": source_id}).fetchall()
    assert [(row["chunk_id"], row["text"]) for row in after] == [
        (row["chunk_id"], row["text"]) for row in before]

