"""Workspace-private learning-loop gap queue tests against PostgreSQL."""

from dataclasses import replace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.api.routes.gaps import router as gap_router
from app.auth.dependencies import get_principal
from app.auth.models import Principal
from app.db import queries
from app.gaps.store import GapConflict, GapNotFound, GapStore, normalize_gap_query
from app.ingest.pipeline import ingest_file
from app.retrieval.config import load_retrieval_config
from app.retrieval.retrieve import retrieve
from app.retrieval.types import Refusal, Retrieved
from tests.fakes import FakeEmbedder, whitespace_tokens

WORKSPACE_A = "oidc:test-workspace-a"
WORKSPACE_B = "oidc:test-workspace-b"


def _config():
    config = load_retrieval_config()
    return replace(
        config,
        seams=replace(config.seams, embed=replace(config.seams.embed, enabled=False)),
    )


def _ingest(db, collection, tmp_path, name, text, workspace_key=None):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    source_id = ingest_file(
        db,
        collection_id=collection,
        path=path,
        content_type="text/plain",
        count_tokens=whitespace_tokens,
        embedder=FakeEmbedder(dim=8),
        embed_model="fake-model",
        config=load_retrieval_config(),
        workspace_key=workspace_key,
    )
    db.commit()
    return source_id


def _retrieve(db, collection, query, source_ids=None, workspace_key=None):
    outcome = retrieve(
        db,
        collection_id=collection,
        query=query,
        embedder=FakeEmbedder(dim=8),
        embed_model="fake-model",
        config=_config(),
        source_ids=source_ids,
        workspace_key=workspace_key,
    )
    db.commit()
    return outcome


def _observe(db, workspace, collection, query, outcome, source_ids, kind):
    return GapStore(db, workspace).observe(
        collection_id=collection,
        query=query,
        source_ids=source_ids,
        trace_id=outcome.trace.trace_id,
        event_kind=kind,
    )


def test_gap_identity_preserves_numbers_punctuation_and_scope(db, collection, tmp_path):
    assert normalize_gap_query("  Table 2.2?!  ") == normalize_gap_query("table 2.2?!")
    assert normalize_gap_query("Table 2.2?!") != normalize_gap_query("Table 12.2?!")
    assert normalize_gap_query("Table 2.2?!") != normalize_gap_query("Table 2 2?!")

    source_a = _ingest(db, collection, tmp_path, "source-a.txt", "alpha one")
    source_b = _ingest(db, collection, tmp_path, "source-b.txt", "beta two")
    refusal = _retrieve(db, collection, "no evidence at all")
    assert isinstance(refusal, Refusal)
    store = GapStore(db, WORKSPACE_A)
    all_sources = _observe(db, WORKSPACE_A, collection, " Table 2.2?! ", refusal, None,
                           "retrieval_refusal")
    with pytest.raises(GapConflict):
        _observe(db, WORKSPACE_A, collection, "Table 2.2?!", refusal, [],
                 "retrieval_refusal")
    no_sources_trace = _retrieve(db, collection, "Table 2.2?!", source_ids=[])
    no_sources = _observe(db, WORKSPACE_A, collection, "Table 2.2?!", no_sources_trace,
                          [], "retrieval_refusal")
    other_number_trace = _retrieve(db, collection, "Table 12.2?!")
    other_number = _observe(db, WORKSPACE_A, collection, "Table 12.2?!",
                            other_number_trace, None, "retrieval_refusal")
    assert len({all_sources["gapId"], no_sources["gapId"], other_number["gapId"]}) == 3

    scoped_trace = _retrieve(db, collection, "Table 2.2?!", source_ids=[source_b, source_a])
    scoped1 = _observe(db, WORKSPACE_A, collection, "Table 2.2?!", scoped_trace,
                       [source_b, source_a], "generation_withheld")
    scoped2 = _observe(db, WORKSPACE_A, collection, "table 2.2?!", scoped_trace,
                       [source_a, source_b], "generation_withheld")
    assert scoped1["gapId"] == scoped2["gapId"]
    assert set(scoped1["sourceIds"]) == {source_a, source_b}
    assert store.list()["metrics"]["repeatedGapObservations"] == 0


def test_gap_observations_are_trace_idempotent_and_success_does_not_autoclose(
    db, collection, tmp_path
):
    source = _ingest(db, collection, tmp_path, "source.txt", "beta evidence only")
    query = "opalxylophone"
    first = _retrieve(db, collection, query)
    assert isinstance(first, Refusal)
    created = _observe(db, WORKSPACE_A, collection, query, first, None, "retrieval_refusal")
    duplicate = _observe(db, WORKSPACE_A, collection, query, first, None, "retrieval_refusal")
    assert duplicate["occurrenceCount"] == 1
    assert duplicate["recurrenceCount"] == 0

    second = _retrieve(db, collection, query)
    assert isinstance(second, Refusal)
    repeated = _observe(db, WORKSPACE_A, collection, query, second, None, "generation_withheld")
    assert repeated["occurrenceCount"] == 2
    assert repeated["recurrenceCount"] == 1

    resolved = GapStore(db, WORKSPACE_A).resolve(
        gap_id=created["gapId"], expected_revision=1, source_ids=[source],
        note="Human reviewed the indexed source.", resolved_by="reviewer-a",
    )
    assert resolved["state"] == "resolved"
    assert resolved["revision"] == 2

    answer_source = _ingest(
        db, collection, tmp_path, "answer.txt", "opalxylophone coverage appears here"
    )
    success = _retrieve(db, collection, query)
    assert isinstance(success, Retrieved)
    observed_success = _observe(
        db, WORKSPACE_A, collection, query, success, None, "retrieval_success"
    )
    assert observed_success["state"] == "resolved"
    assert observed_success["revision"] == 2
    assert observed_success["successfulRecurrenceCount"] == 1
    metrics = GapStore(db, WORKSPACE_A).list()["metrics"]
    assert metrics == {
        "repeatedGapObservations": 2,
        "successfulRepeatedObservations": 1,
        "recurrenceRate": 0.5,
    }

    db.execute(queries.get("delete_source"), {"source_id": source})
    db.execute(queries.get("delete_source"), {"source_id": answer_source})
    db.commit()
    later_refusal = _retrieve(db, collection, query)
    reopened = _observe(
        db, WORKSPACE_A, collection, query, later_refusal, None, "retrieval_refusal"
    )
    assert reopened["state"] == "open"
    assert reopened["revision"] == 3
    assert reopened["resolution"] is None


def test_success_without_existing_gap_does_not_create_work(db, collection, tmp_path):
    _ingest(db, collection, tmp_path, "source.txt", "alpha available evidence")
    success = _retrieve(db, collection, "alpha")
    assert isinstance(success, Retrieved)
    assert _observe(db, WORKSPACE_A, collection, "alpha", success, None,
                    "retrieval_success") is None
    with pytest.raises(ValueError):
        _observe(db, WORKSPACE_A, collection, "alpha", success, None, "provider_outage")
    assert GapStore(db, WORKSPACE_A).list()["gaps"] == []


def test_human_resolution_requires_indexed_authorized_same_scope_sources(
    db, collection, tmp_path
):
    allowed = _ingest(db, collection, tmp_path, "allowed.txt", "evidence alpha")
    other = _ingest(db, collection, tmp_path, "other.txt", "evidence beta")
    refusal = _retrieve(db, collection, "unmatched", source_ids=[allowed])
    gap = _observe(db, WORKSPACE_A, collection, "unmatched", refusal, [allowed],
                   "retrieval_refusal")
    store = GapStore(db, WORKSPACE_A)

    with pytest.raises(GapNotFound):
        store.resolve(gap_id=gap["gapId"], expected_revision=1, source_ids=[other],
                      note="", resolved_by="reviewer-a")
    with pytest.raises(GapConflict):
        store.resolve(gap_id=gap["gapId"], expected_revision=2, source_ids=[allowed],
                      note="", resolved_by="reviewer-a")
    resolved = store.resolve(gap_id=gap["gapId"], expected_revision=1,
                             source_ids=[allowed], note="Reviewed.", resolved_by="reviewer-a")
    assert resolved["resolution"]["sourceIds"] == [allowed]


def test_gaps_are_private_and_api_resolution_is_revision_checked(db, collection, tmp_path):
    source = _ingest(db, collection, tmp_path, "private.txt", "some indexed words", WORKSPACE_A)
    refusal = _retrieve(db, collection, "missing query", workspace_key=WORKSPACE_A)
    gap = _observe(db, WORKSPACE_A, collection, "missing query", refusal, None,
                   "retrieval_refusal")

    app = FastAPI()
    app.include_router(gap_router)
    app.dependency_overrides[deps.get_conn] = lambda: db
    owner = Principal(WORKSPACE_A, "test-issuer", "subject-a")
    other = Principal(WORKSPACE_B, "test-issuer", "subject-b")
    active = [owner]
    app.dependency_overrides[get_principal] = lambda: active[0]
    client = TestClient(app)

    assert client.get("/api/gaps").json()["gaps"][0]["gapId"] == str(gap["gapId"])
    active[0] = other
    assert client.get("/api/gaps").json()["gaps"] == []
    response = client.post(
        f"/api/gaps/{gap['gapId']}/resolve",
        json={"expectedRevision": 1, "sourceIds": [str(source)], "note": "reviewed"},
    )
    assert response.status_code == 404

    active[0] = owner
    response = client.post(
        f"/api/gaps/{gap['gapId']}/resolve",
        json={"expectedRevision": 1, "sourceIds": [str(source)], "note": "reviewed"},
    )
    assert response.status_code == 200
    assert response.json()["state"] == "resolved"
    assert response.json()["revision"] == 2
