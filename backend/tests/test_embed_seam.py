"""Embedding pipeline + seam tests against a real Postgres. No models load."""

from uuid import uuid4

import numpy as np
import pytest

from app.db import queries
from app.ingest.pipeline import EmbeddingError, ingest_file
from app.retrieval.config import EmbedSeamConfig, load_retrieval_config
from app.retrieval.seams.embed import embed_seam
from tests.fakes import FakeEmbedder, embed_other_model, whitespace_tokens
from tests.helpers import ingest_text

_MODEL = "fake-model"


class _BadDim:
    def embed_documents(self, texts):
        return np.ones((len(texts), 4), dtype=np.float32)

    def embed_query(self, text):
        return np.ones(4, dtype=np.float32)


class _NotNormalized:
    def embed_documents(self, texts):
        return np.full((len(texts), 8), 2.0, dtype=np.float32)

    def embed_query(self, text):
        return np.full(8, 2.0, dtype=np.float32)


def _config(**overrides):
    # min_similarity 0.0 in tests: FakeEmbedder's dots are arbitrary, so the
    # floor is exercised deliberately in its own test, not by accident.
    base = {"enabled": True, "limit": 40, "only_quota": 4, "min_similarity": 0.0}
    base.update(overrides)
    return EmbedSeamConfig(**base)


def test_embedding_results_order_by_score_then_source_and_chunk_with_a_stable_limit(
    db, collection, tmp_path,
):
    query = np.array([1.0] + [0.0] * 7, dtype=np.float32)
    expected = []
    for index, name in enumerate(("weak.txt", "tie-b.txt", "tie-a.txt")):
        source = ingest_text(db, collection, tmp_path, name, f"Evidence {name}")
        row = db.execute(queries.get("list_chunks_for_source"), {"source_id": source}).fetchone()
        vector = query if index else np.array([0.6, 0.8] + [0.0] * 6, dtype=np.float32)
        chunks = [(row["chunk_id"], 0)]
        if index == 2:
            extra = uuid4()
            db.execute(queries.get("insert_chunk"), {
                "chunk_id": extra, "source_id": source, "locator_id": row["locator_id"],
                "chunk_index": 1, "start_char": 0, "end_char": 3,
                "token_count": 1, "text": "Extra tied evidence",
            })
            chunks.append((extra, 1))
        for chunk_id, chunk_index in chunks:
            db.execute(queries.get("upsert_chunk_embedding"), {
                "chunk_id": chunk_id, "model": _MODEL, "dim": 8, "embedding": vector,
                "text_sha256": None,
            })
            expected.append((float(vector[0]), str(source), chunk_index, chunk_id))
    expected.sort(key=lambda item: (-item[0], item[1], item[2]))
    for limit in (4, 2, 1):
        for _ in range(2):
            result = embed_seam(db, collection, "query", _MODEL, _config(limit=limit), query_vec=query)
            assert result.state == "active"
            assert [c.chunk_id for c in result.candidates] == [item[3] for item in expected[:limit]]
            assert [c.raw_score for c in result.candidates] == pytest.approx(
                [item[0] for item in expected[:limit]],
            )


def test_ingest_writes_one_row_per_chunk(db, collection, tmp_path):
    source_id = ingest_text(db, collection, tmp_path, "a.txt", "alpha beta gamma delta\n")
    chunks = db.execute(
        queries.get("list_chunks_for_source"), {"source_id": source_id}
    ).fetchall()
    rows = db.execute(
        "SELECT chunk_id, model, dim FROM chunk_embeddings WHERE model = %(m)s",
        {"m": _MODEL},
    ).fetchall()
    assert len(rows) == len(chunks)
    assert {r["chunk_id"] for r in rows} == {c["chunk_id"] for c in chunks}
    assert all(r["dim"] == 8 for r in rows)


def test_reingest_is_idempotent(db, collection, tmp_path):
    first = ingest_text(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    second = ingest_text(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    assert first == second
    count = db.execute(
        "SELECT COUNT(*) AS n FROM chunk_embeddings WHERE model = %(m)s",
        {"m": _MODEL},
    ).fetchone()
    assert count["n"] == 1


def test_second_model_rows_never_appear_for_first(db, collection, tmp_path):
    source_id = ingest_text(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    chunk = db.execute(
        queries.get("list_chunks_for_source"), {"source_id": source_id}
    ).fetchone()
    db.execute(
        queries.get("upsert_chunk_embedding"),
        {
            "chunk_id": chunk["chunk_id"],
            "model": "other-model",
            "dim": 4,
            "embedding": embed_other_model("alpha beta gamma", 4),
            "text_sha256": None,
        },
    )
    db.commit()

    result = embed_seam(
        db, collection, "alpha", _MODEL, _config(),
        query_vec=FakeEmbedder(dim=8).embed_query("alpha")
    )
    assert result.state == "active"
    assert all(c.chunk_id == chunk["chunk_id"] for c in result.candidates)
    assert len(result.candidates) == 1


def test_query_dimension_mismatch_excluded(db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    # A 16-dim query against 8-dim rows must match nothing.
    result = embed_seam(
        db, collection, "q", _MODEL, _config(), query_vec=np.ones(16, dtype=np.float32)
    )
    assert result.state == "dormant"


def test_no_rows_for_model_is_dormant(db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    result = embed_seam(
        db, collection, "q", "missing-model", _config(), query_vec=np.ones(8, dtype=np.float32)
    )
    assert result.state == "dormant"


def test_failed_source_excluded(db, collection, tmp_path):
    source_id = ingest_text(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    db.execute(
        "UPDATE sources SET status = 'failed' WHERE source_id = %(s)s",
        {"s": source_id},
    )
    db.commit()
    result = embed_seam(
        db, collection, "alpha", _MODEL, _config(),
        query_vec=FakeEmbedder(dim=8).embed_query("alpha")
    )
    assert result.state == "dormant"


def _axis(weight: float) -> np.ndarray:
    vector = np.zeros(768, dtype=np.float32)
    vector[0], vector[1] = weight, np.sqrt(1.0 - weight * weight)
    return vector


_SUPERSEDE = "UPDATE sources SET is_current = false WHERE source_id = %(s)s"


@pytest.mark.parametrize("changes, crowd_visible", [
    ([_SUPERSEDE], False),
    (["UPDATE chunks SET search_hidden = true WHERE source_id = %(s)s"], False),
    (["UPDATE sources SET status = 'failed' WHERE source_id = %(s)s"], False),
    ([_SUPERSEDE, "UPDATE sources SET is_current = true WHERE source_id = %(s)s"], True),
], ids=["superseded", "hidden", "failed", "made-current-again"])
def test_unsearchable_vectors_never_crowd_out_searchable_ones(
    db, collection, tmp_path, changes, crowd_visible,
):
    # The 768-dim search takes its nearest rows before the source filters run.
    # Forty-one crowd chunks sit exactly on the query, more than the 40 rows a
    # limit-1 search takes, so if they count, the wanted chunk is never seen.
    query = _axis(1.0)
    wanted = ingest_text(db, collection, tmp_path, "wanted.txt", "wanted passage",
                         embedder=FakeEmbedder(dim=768))
    crowd = ingest_text(db, collection, tmp_path, "crowd.txt", "crowd passage",
                        embedder=FakeEmbedder(dim=768))
    wanted_chunk = db.execute(queries.get("list_chunks_for_source"),
                              {"source_id": wanted}).fetchone()
    db.execute("UPDATE chunk_embeddings SET embedding = %(v)s WHERE chunk_id = %(c)s",
               {"v": _axis(0.6), "c": wanted_chunk["chunk_id"]})
    first = db.execute(queries.get("list_chunks_for_source"), {"source_id": crowd}).fetchone()
    db.execute("UPDATE chunk_embeddings SET embedding = %(v)s WHERE chunk_id = %(c)s",
               {"v": query, "c": first["chunk_id"]})
    for index in range(1, 41):
        chunk_id = uuid4()
        db.execute(queries.get("insert_chunk"), {
            "chunk_id": chunk_id, "source_id": crowd, "locator_id": first["locator_id"],
            "chunk_index": index, "start_char": 0, "end_char": 5, "token_count": 2,
            "text": f"crowd passage {index}",
        })
        db.execute(queries.get("upsert_chunk_embedding"), {
            "chunk_id": chunk_id, "model": _MODEL, "dim": 768, "embedding": query,
            "text_sha256": None,
        })
    for change in changes:
        db.execute(change, {"s": crowd})
    db.commit()

    result = embed_seam(db, collection, "q", _MODEL, _config(limit=1), query_vec=query)
    assert result.state == "active"
    assert result.candidates[0].source_id == (crowd if crowd_visible else wanted)


def test_disabled_is_disabled(db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    result = embed_seam(
        db, collection, "q", _MODEL, _config(enabled=False), query_vec=np.ones(8, dtype=np.float32)
    )
    assert result.state == "disabled"


def test_similarity_floor_drops_unrelated_chunks(db, collection, tmp_path):
    # The query vector equals a.txt's own chunk text (dot 1.0); b.txt is
    # unrelated. A floor at 0.9 keeps only the related chunk, at 1.0 too.
    ingest_text(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    ingest_text(db, collection, tmp_path, "b.txt", "zzzz qqqq wwww\n")
    query = FakeEmbedder(dim=8).embed_query("alpha beta gamma")
    keep_source = db.execute(
        "SELECT source_id FROM sources WHERE filename = 'a.txt'"
    ).fetchone()["source_id"]

    kept = embed_seam(
        db, collection, "alpha beta gamma", _MODEL, _config(min_similarity=0.9),
        query_vec=query,
    )
    assert kept.state == "active"
    assert {c.source_id for c in kept.candidates} == {keep_source}

    # A query near nothing in the corpus is below the floor for every chunk.
    unrelated = FakeEmbedder(dim=8).embed_query("entirely unrelated phrasing")
    none = embed_seam(
        db, collection, "entirely unrelated phrasing", _MODEL,
        _config(min_similarity=0.9), query_vec=unrelated,
    )
    assert none.state == "dormant"


@pytest.mark.parametrize("bad_embedder", [_BadDim, _NotNormalized])
def test_embedder_returning_wrong_dimension_or_unnormalized_vectors_fails_ingest(
    db, collection, tmp_path, bad_embedder
):
    path = tmp_path / "a.txt"
    path.write_text("alpha beta\n", encoding="utf-8")
    with pytest.raises(EmbeddingError):
        ingest_file(
            db,
            collection_id=collection,
            path=path,
            content_type="text/plain",
            count_tokens=whitespace_tokens,
            embedder=bad_embedder(),
            embed_model="bad",
            config=load_retrieval_config(),
        )
    row = db.execute(
        "SELECT status FROM sources WHERE collection_id = %(c)s", {"c": collection}
    ).fetchone()
    assert row["status"] == "failed"
