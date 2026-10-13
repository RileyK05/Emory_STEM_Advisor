"""Real database proofs for scope, numeric references, and durable generation evidence."""

from uuid import uuid4

from app.db import queries
from app.retrieval.config import KeywordSeamConfig
from app.retrieval.seams.keyword import keyword_seam
from tests.fakes import FakeProvider
from tests.helpers import api_client, collection_name, ingest_text


def test_selected_source_filters_before_keyword_limit(db, collection, tmp_path):
    wanted = ingest_text(db, collection, tmp_path, "wanted.txt", "alpha lower-ranked target")
    other = ingest_text(db, collection, tmp_path, "other.txt", "alpha alpha alpha alpha unrelated")
    result = keyword_seam(db, collection, "alpha", KeywordSeamConfig(True, 1, 2),
                          source_ids=[wanted])
    assert [c.source_id for c in result.candidates] == [wanted]
    assert other not in {c.source_id for c in result.candidates}


def test_numeric_reference_matches_exact_table(db, collection, tmp_path):
    wanted = ingest_text(db, collection, tmp_path, "table.txt", "Table 2.2 describes enrollment.")
    ingest_text(db, collection, tmp_path, "other.txt", "Table 12.2 describes something else.")
    result = keyword_seam(db, collection, "2.2", KeywordSeamConfig(True, 1, 2))
    assert [c.source_id for c in result.candidates] == [wanted]


def test_explicit_empty_scope_never_calls_model(db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "a.txt", "alpha beta")
    provider = FakeProvider()
    resp = api_client(db, collection, provider=provider).post("/api/query", json={
        "query": "alpha", "collectionId": collection_name(db, collection), "sourceIds": [],
    })
    assert resp.json()["type"] == "refusal"
    assert provider.calls == 0


def test_invalid_source_scope_is_not_widened(db, collection):
    resp = api_client(db, collection).post("/api/query", json={
        "query": "alpha", "collectionId": collection_name(db, collection),
        "sourceIds": [str(uuid4())],
    })
    assert resp.status_code == 404


def test_invalid_output_accounted_and_withheld(db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "a.txt", "alpha beta")
    resp = api_client(db, collection, provider=FakeProvider("Uncited answer")).post("/api/query", json={
        "query": "alpha", "collectionId": collection_name(db, collection),
    })
    assert resp.json()["type"] == "refusal"
    assert db.execute("SELECT count(*) AS n FROM model_calls").fetchone()["n"] == 1
    assert db.execute("SELECT status FROM responses").fetchone()["status"] == "refusal"
    assert db.execute("SELECT count(*) AS n FROM citations").fetchone()["n"] == 0


def test_saved_citation_snapshot_survives_source_deletion(db, collection, tmp_path):
    source = ingest_text(db, collection, tmp_path, "a.txt", "alpha beta")
    resp = api_client(db, collection, provider=FakeProvider("Alpha [1].")).post("/api/query", json={
        "query": "alpha", "collectionId": collection_name(db, collection),
    })
    assert resp.json()["type"] == "answer"
    db.execute(queries.get("delete_source"), {"source_id": source})
    db.commit()
    snapshot = db.execute("SELECT source_name, text_snapshot FROM citations").fetchone()
    assert snapshot["source_name"] == "a.txt"
    assert snapshot["text_snapshot"] == "alpha beta"


def test_upload_rejects_path_filename_before_ingest(db, collection):
    resp = api_client(db, collection).post("/api/sources",
        files={"file": ("../escape.txt", b"alpha", "text/plain")},
        data={"collection": collection_name(db, collection)})
    assert resp.status_code == 400


def test_failed_source_reason_is_visible(db, collection, tmp_path):
    source = ingest_text(db, collection, tmp_path, "a.txt", "alpha beta")
    revision = db.execute("SELECT index_revision FROM sources WHERE source_id=%(id)s",
                          {"id": source}).fetchone()["index_revision"]
    db.execute(queries.get("set_source_failed"),
               {"source_id": source, "index_revision": revision,
                "error_message": "embedding failed"})
    db.commit()
    resp = api_client(db, collection).get("/api/sources",
                params={"collection": collection_name(db, collection)})
    assert resp.json()[0]["errorMessage"] == "embedding failed"
