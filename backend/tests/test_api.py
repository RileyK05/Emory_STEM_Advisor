"""HTTP API tests. Fake provider/embedder; real Postgres. No models load."""



import pytest
from pydantic import ValidationError

from app.api.routes.conversations import ConversationQuery
from app.api.routes.corpus import ReindexRequest
from app.api.schemas import (
    AcademicRecordImportPreviewRequest,
    ExportSummaryRequest,
    PrivacyDeletionRequest,
    QueryRequest,
)
from app.config import load_settings
from app.db import queries
from app.db.bootstrap import prepare_database
from app.db.collections import UnknownCollection, resolve_collection
from tests.fakes import FakeProvider
from tests.helpers import api_client, collection_name, ingest_text


@pytest.mark.parametrize("build", [
    lambda: QueryRequest(query="x" * 8001),
    lambda: QueryRequest(query="q", studentCohort="x" * 201),
    lambda: QueryRequest(query="q", collectionId="x" * 201),
    lambda: ExportSummaryRequest(title="x" * 501),
    lambda: ExportSummaryRequest(throughSequence=-1),
    lambda: PrivacyDeletionRequest(confirmation="x" * 201),
    lambda: AcademicRecordImportPreviewRequest(
        filename="transcript.csv", rawContent="x" * 2_000_001, expectedRecordRevision=1),
    lambda: ExportSummaryRequest(throughSequence=10**100),
    lambda: AcademicRecordImportPreviewRequest(
        filename="transcript.csv", rawContent="x", expectedRecordRevision=10**100),
    lambda: ReindexRequest(expectedIndexRevision=10**100),
    lambda: ConversationQuery(
        query="q", requestId="00000000-0000-0000-0000-000000000001",
        expectedSequence=10**100),
])
def test_request_values_are_bounded(build):
    with pytest.raises(ValidationError):
        build()


def test_read_endpoints_and_basic_error_statuses(db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    client = api_client(db, collection)
    name = collection_name(db, collection)
    assert client.get("/health").json() == {"status": "ok"}
    assert name in {item["name"] for item in client.get("/api/collections").json()}
    sources = client.get("/api/sources", params={"collection": name}).json()
    assert [s["name"] for s in sources] == ["a.txt"]
    assert sources[0]["status"] == "indexed" and sources[0]["chunkCount"] == 1
    assert client.get("/api/sources", params={"collection": "no-such"}).status_code == 404
    assert client.post("/api/query", json={"query": ""}).status_code == 422
    invalid = client.post("/api/query", json={"query": {"api_key": "sk-test-never-echo"}})
    assert invalid.status_code == 422
    assert "sk-test-never-echo" not in invalid.text
    assert "input" not in invalid.json()["detail"][0]


@pytest.mark.parametrize(("mode", "role"), [
    ("development", "admin"), ("oidc", "student"), ("oidc", "admin"),
])
def test_query_returns_grounded_answer(db, collection, tmp_path, mode, role):
    from app.auth.dependencies import get_principal
    from app.auth.models import Principal

    ingest_text(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    provider = FakeProvider(text="The answer is alpha [1].")
    client = api_client(db, collection, provider=provider)
    client.app.dependency_overrides[get_principal] = lambda: Principal(
        workspace_key="development", issuer=None, subject="test", mode=mode, role=role)
    resp = client.post(
        "/api/query",
        json={"query": "alpha", "collectionId": collection_name(db, collection)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "answer"
    assert body["answer"] == "The answer is alpha [1]."
    assert body["citations"][0]["sourceName"] == "a.txt"
    assert provider.calls == 1
    # The prompt fences the context as untrusted data.
    if mode == "oidc" and role == "student":
        assert body["trace"]["prompt"] == {"system": "", "user": ""}
        assert body["trace"]["seams"] == body["trace"]["contributions"] == []
        assert body["trace"]["warnings"] == body["trace"]["errors"] == []
    else:
        assert "untrusted" in body["trace"]["prompt"]["user"]
    assert body["trace"]["contextBlock"][0]["chunkId"]
    assert body["trace"]["contextBlock"][0]["text"] == "alpha beta gamma"


def test_query_refusal_does_not_call_model(db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    provider = FakeProvider()
    client = api_client(db, collection, provider=provider)
    resp = client.post(
        "/api/query",
        json={"query": "zzzzz qqqqq nothing", "collectionId": collection_name(db, collection)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "refusal"
    assert provider.calls == 0
    gaps = client.get("/api/gaps").json()["gaps"]
    assert len(gaps) == 1 and gaps[0]["query"] == "zzzzz qqqqq nothing"
    assert gaps[0]["occurrenceCount"] == 1 and gaps[0]["sourceIds"] is None


def test_generation_gap_recurrence_and_provider_outage_are_distinct(db, collection, tmp_path):
    from app.llm.base import ProviderTimeoutError

    ingest_text(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    client = api_client(db, collection, provider=FakeProvider("Uncited assertion."))
    request = {"query": "alpha", "collectionId": collection_name(db, collection)}
    withheld = client.post("/api/query", json=request)
    assert withheld.status_code == 200 and withheld.json()["type"] == "refusal"
    gap = client.get("/api/gaps").json()["gaps"][0]
    assert gap["state"] == "open" and gap["occurrenceCount"] == 1

    client.app.state.provider = FakeProvider("Alpha [1].")
    answered = client.post("/api/query", json=request)
    assert answered.status_code == 200 and answered.json()["type"] == "answer"
    observed = client.get("/api/gaps").json()
    assert observed["gaps"][0]["gapId"] == gap["gapId"]
    assert observed["gaps"][0]["state"] == "open"
    assert observed["gaps"][0]["successfulRecurrenceCount"] == 1

    class OfflineProvider:
        def generate(self, _request):
            raise ProviderTimeoutError("provider unavailable", provider="fake")

    client.app.state.provider = OfflineProvider()
    assert client.post("/api/query", json=request).status_code == 503
    assert client.get("/api/gaps").json() == observed


@pytest.mark.parametrize(("raw", "limits", "expected"), [
    (b"alpha beta gamma\n", {}, 200),
    (b"alpha beta gamma\n", {"UPLOAD_MAX_EXTRACTED_CHARS": "5"}, 422),
    (b"a" * 100, {"UPLOAD_MAX_SEGMENT_CHARS": "50"}, 422),
    (b"alpha beta.\n\n" * 1000, {"UPLOAD_MAX_CHUNKS": "1"}, 422),
])
def test_upload_source(db, collection, monkeypatch, raw, limits, expected):
    for name, value in limits.items():
        monkeypatch.setenv(name, value)
    client = api_client(db, collection)
    resp = client.post(
        "/api/sources",
        files={"file": ("notes.txt", raw, "text/plain")},
        data={"collection": collection_name(db, collection)},
    )
    assert resp.status_code == expected, resp.text
    if expected == 422:
        listed = client.get(
            "/api/sources", params={"collection": collection_name(db, collection)}).json()
        assert listed[0]["status"] == "failed"
        assert "limit" in listed[0]["errorMessage"]
        assert listed[0]["chunkCount"] == 0
        return
    body = resp.json()
    assert body["name"] == "notes.txt"
    assert body["status"] == "indexed"
    assert body["chunkCount"] == 1
    listed = client.get(
        "/api/sources", params={"collection": collection_name(db, collection)}
    ).json()
    assert any(s["name"] == "notes.txt" for s in listed)
    duplicate = client.post("/api/sources", data={"collection": collection_name(db, collection)},
                            files={"file": ("renamed.txt", raw, "text/plain")}).json()
    assert duplicate["id"] == body["id"]
    assert "already indexed as notes.txt" in duplicate["warnings"][-1]
    disguised = client.post("/api/sources", data={"collection": collection_name(db, collection)},
                            files={"file": ("disguised.txt", b"%PDF-1.4\n", "text/plain")})
    assert disguised.status_code == 415


@pytest.mark.parametrize("filename", ["CON.txt", "notes. ", "../notes.txt", "bad\\name.txt"])
def test_upload_rejects_unsafe_filenames(filename):
    from app.api.routes.corpus import _valid_upload_filename

    assert not _valid_upload_filename(filename)


def test_deep_json_body_is_a_client_error(db, collection):
    client = api_client(db, collection)
    body = '{"query":"q","extra":' + "[" * 2000 + "0" + "]" * 2000 + "}"
    response = client.post("/api/query", content=body,
                           headers={"content-type": "application/json"})
    assert response.status_code < 500


def test_collection_lookup_treats_sql_metacharacters_as_data(db, collection):
    client = api_client(db, collection)
    response = client.post(
        "/api/query",
        json={"query": "alpha", "collectionId": "course-docs' OR 1=1 --"},
    )
    assert response.status_code == 404
    assert "course-docs' OR 1=1 --" not in response.text


def test_metrics_are_loopback_only_and_report_pool_and_queue_state(db, collection):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from app.api import create_app
    from app.config import load_settings
    from tests.fakes import FakeEmbedder

    settings = replace(load_settings(), admin_api_enabled=True)
    app = create_app(settings=settings, allow_test_client=True,
                     embedder=FakeEmbedder(dim=8), provider=FakeProvider())
    app.state.override_conn = db
    client = TestClient(app, client=("127.0.0.1", 1234), base_url="http://127.0.0.1")
    response = client.get("/metrics")
    assert response.status_code == 200, response.text
    assert "advisor_db_pool_max_connections" in response.text
    assert 'advisor_jobs{status="queued"}' in response.text
    assert "advisor_job_oldest_queued_seconds" in response.text

    remote = TestClient(app, client=("192.0.2.8", 1234), base_url="http://127.0.0.1")
    assert remote.get("/metrics").status_code == 403

    disabled = create_app(settings=replace(settings, admin_api_enabled=False),
                          allow_test_client=True, embedder=FakeEmbedder(dim=8),
                          provider=FakeProvider())
    disabled.state.override_conn = db
    hidden = TestClient(disabled, client=("127.0.0.1", 1234), base_url="http://127.0.0.1")
    assert hidden.get("/metrics").status_code == 404


def test_bootstrap_creates_default_collection_once(db):
    prepare_database(db, "serving-default")
    first = db.execute(queries.get("get_collection_by_name"), {"name": "serving-default"}).fetchone()
    db.commit()
    prepare_database(db, "serving-default")
    second = db.execute(queries.get("get_collection_by_name"), {"name": "serving-default"}).fetchone()
    assert first["collection_id"] == second["collection_id"]


def test_named_collection_resolves(db, collection):
    name = collection_name(db, collection)
    assert resolve_collection(db, load_settings(), name) == (collection, name)


def test_unknown_collection_is_a_domain_error_that_job_routes_map_as_invalid_input(db, collection):
    with pytest.raises(UnknownCollection, match="no-such-collection"):
        resolve_collection(db, load_settings(), "no-such-collection")


def test_student_trace_hides_non_cited_chunk_and_source_ids():
    from types import SimpleNamespace

    from app.api.serialize import public_payload

    value = {
        "citations": [{"chunkId": "cited"}],
        "trace": {
            "traceId": "trace",
            "contextBlock": [
                {"chunkId": "cited", "sourceId": "source-a", "sourceName": "a",
                 "locatorLabel": "page 1", "text": "cited text"},
                {"chunkId": "uncited", "sourceId": "source-b", "sourceName": "b",
                 "locatorLabel": "page 2", "text": "uncited text"},
            ],
        },
    }
    public = public_payload(value, SimpleNamespace(mode="oidc", role="student"))
    assert public["trace"]["contextBlock"][0]["chunkId"] == "cited"
    assert public["trace"]["contextBlock"][0]["text"] == "cited text"
    assert public["trace"]["contextBlock"][1] == {
        "chunkId": "", "sourceId": "", "sourceName": "", "locatorLabel": "", "text": "",
    }
    assert public["trace"]["timings"] == {}
