from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_conn
from app.api.routes.runs import router
from app.auth.dependencies import get_workspace_key
from app.db.connection import connect
from app.jobs import JobStore
from app.jobs.store import JobNotFound
from app.jobs.types import Job, JobEvent, JobStatus


class MemoryStore:
    def __init__(self):
        self.jobs = {}
        self.keys = {}
        self.event_rows = {}
        self.next_event = 1

    def create(self, *, kind, scope_key, idempotency_key, payload, **_kwargs):
        key = (scope_key, idempotency_key)
        if key in self.keys:
            return self.jobs[self.keys[key]]
        now = datetime.now(UTC)
        job = Job(
            uuid4(),
            kind,
            scope_key,
            idempotency_key,
            dict(payload),
            None,
            None,
            JobStatus.QUEUED,
            0,
            3,
            now,
            None,
            None,
            None,
            None,
            False,
            now,
            now,
            None,
        )
        self.jobs[job.job_id] = job
        self.keys[key] = job.job_id
        self.event_rows[job.job_id] = [
            JobEvent(self.next_event, job.job_id, "queued", {"status": "queued"}, now)
        ]
        self.next_event += 1
        return job

    def get(self, job_id):
        if job_id not in self.jobs:
            raise JobNotFound(str(job_id))
        return self.jobs[job_id]

    def events(self, job_id, *, after_event_id=0, limit=100):
        self.get(job_id)
        return [event for event in self.event_rows[job_id] if event.event_id > after_event_id][
            :limit
        ]

    def cancel(self, job_id):
        job = self.get(job_id)
        if job.status is JobStatus.QUEUED:
            changed = Job(
                **{**job.__dict__, "status": JobStatus.CANCELLED, "finished_at": datetime.now(UTC)}
            )
            self.jobs[job_id] = changed
            return changed
        return job


def _queue(store, key, inputs=None, scope="local-development"):
    return str(store.create(kind="artifact.run", scope_key=scope, idempotency_key=key,
                            payload={"kind": "document", "inputs": inputs or {}}).job_id)


class _FakeRevisionConn:
    def __init__(self, row):
        self.row = row

    def execute(self, query, params=None):
        return self

    def fetchone(self):
        return self.row


def client_for(*, scope="local-development", conn=None):
    app = FastAPI()
    app.state.job_store = MemoryStore()
    app.state.settings = SimpleNamespace(
        artifact_operation_max_calls=2,
        artifact_operation_token_budget=16_000,
        llm_max_output_tokens=1024,
        data_dir="data",
        workspace_storage_max_bytes=104857600,
    )
    app.dependency_overrides[get_workspace_key] = lambda: scope
    app.dependency_overrides[get_conn] = lambda: conn
    app.include_router(router)
    return TestClient(app), app.state.job_store


def test_run_create_idempotency_status_cursor_cancel_and_scope():
    client, store = client_for()
    inputs = {"prompt": "Create a study guide"}
    run_id = _queue(store, "same-request", inputs)
    assert _queue(store, "same-request", inputs) == run_id
    assert client.get(f"/api/runs/{run_id}").json()["state"] == "queued"
    events = client.get(f"/api/runs/{run_id}/events?afterEventId=0").json()
    assert len(events) == 1 and events[0]["state"] == "queued"
    assert client.get(f"/api/runs/{run_id}/events?afterEventId=1").json() == []
    assert client.post(f"/api/runs/{run_id}/cancel").json()["state"] == "cancelled"
    assert client.get(f"/api/runs/{run_id}").json()["state"] == "cancelled"
    other = store.create(
        kind="artifact.run",
        scope_key="other-workspace",
        idempotency_key="private",
        payload={"kind": "html", "inputs": {}},
    )
    assert client.get(f"/api/runs/{other.job_id}").status_code == 404
    assert client.get(f"/api/runs/{other.job_id}/events").status_code == 404
    assert client.post(f"/api/runs/{other.job_id}/cancel").status_code == 404


def test_public_run_errors_are_sanitized():
    client, store = client_for()
    created = {"runId": _queue(store, "error")}
    job = store.get(UUID(created["runId"]))
    store.jobs[job.job_id] = Job(
        **{
            **job.__dict__,
            "status": JobStatus.FAILED,
            "error": {"type": "execution_failed", "message": "Job execution failed."},
            "finished_at": datetime.now(UTC),
        }
    )
    assert client.get(f"/api/runs/{created['runId']}").json()["error"] == "Job execution failed."
    store.jobs[job.job_id] = Job(
        **{
            **job.__dict__,
            "status": JobStatus.FAILED,
            "error": {"type": "secret", "message": "Bearer abc123"},
            "finished_at": datetime.now(UTC),
        }
    )
    assert client.get(f"/api/runs/{created['runId']}").json()["error"] == "Run failed."


def test_run_and_event_drop_malformed_artifact_revision_pairs():
    client, store = client_for()
    run_id = UUID(_queue(store, "bad-revision"))
    job = store.get(run_id)
    malformed = [{"artifactId": str(uuid4()), "revisionId": "not-a-uuid", "secret": "private"}]
    store.jobs[run_id] = Job(**{**job.__dict__, "status": JobStatus.SUCCEEDED,
                                "result": {"artifactIds": [str(uuid4())],
                                           "artifactRevisions": malformed}})
    store.event_rows[run_id].append(JobEvent(
        2, run_id, "succeeded", {"artifactIds": [str(uuid4())],
                                 "artifactRevisions": malformed}, datetime.now(UTC)))
    public = client.get(f"/api/runs/{run_id}").json()
    event = client.get(f"/api/runs/{run_id}/events?afterEventId=1").json()[0]
    assert "artifactRevisions" not in public
    assert "artifactRevisions" not in event
    assert "secret" not in repr(public) + repr(event)
    assert "artifactIds" in public and "artifactIds" in event


def test_progress_reports_recognized_stage_without_tool_args_or_private_payloads():
    client, store = client_for()
    run_id = UUID(_queue(store, "progress"))
    store.event_rows[run_id].extend([
        JobEvent(2, run_id, "progress", {"stage": "tool_started", "arguments": {"private": "data"},
                                        "token": "secret"}, datetime.now(UTC)),
        JobEvent(3, run_id, "progress", {"stage": "unrecognized private text"}, datetime.now(UTC)),
    ])
    events = client.get(f"/api/runs/{run_id}/events?afterEventId=1").json()
    assert events[0] == {"eventId": 2, "runId": str(run_id), "state": "queued", "stage": "tool_started"}
    assert "stage" not in events[1]


def test_run_routes_integrate_with_postgres_job_store(migrated_dsn, db):
    app = FastAPI()
    app.state.job_store = JobStore(lambda: connect(migrated_dsn))
    app.dependency_overrides[get_workspace_key] = lambda: "development"
    app.dependency_overrides[get_conn] = lambda: None
    app.include_router(router)
    client = TestClient(app)
    run_id = str(app.state.job_store.create(
        kind="artifact.run", scope_key="development", idempotency_key="db-run-1",
        payload={"kind": "workbook", "inputs": {"prompt": "Build a grade tracker"}}).job_id)
    assert client.get(f"/api/runs/{run_id}").json()["state"] == "queued"
    assert client.get(f"/api/runs/{run_id}/events").json()[0]["state"] == "queued"

    # Retrying a queued run is rejected
    assert client.post(f"/api/runs/{run_id}/retry").status_code == 409

    claimed = app.state.job_store.claim("test-worker")
    assert claimed is not None and str(claimed.job_id) == run_id
    app.state.job_store._finish_failure(
        claimed, claimed.lease_token, {"message": "failed"}, retryable=False
    )
    assert client.get(f"/api/runs/{run_id}").json()["state"] == "failed"

    retried = client.post(f"/api/runs/{run_id}/retry")
    assert retried.status_code == 200
    retried_data = retried.json()
    assert retried_data["state"] == "queued"
    assert retried_data["runId"] != run_id


@pytest.mark.parametrize(
    "case,status,payload,conn_row,expected_status,expected_state",
    [
        (
            "success_new_artifact",
            JobStatus.FAILED,
            {"kind": "document", "inputs": {"prompt": "Create guide"}},
            None,
            200,
            "queued",
        ),
        (
            "success_matching_revision",
            JobStatus.FAILED,
            {
                "kind": "document",
                "inputs": {
                    "prompt": "Revise guide",
                    "artifactId": "00000000-0000-0000-0000-000000000001",
                    "expectedRevisionId": "00000000-0000-0000-0000-000000000002",
                },
            },
            {
                "head_revision_id": "00000000-0000-0000-0000-000000000002",
                "revision_id": "00000000-0000-0000-0000-000000000002",
                "kind": "document",
            },
            200,
            "queued",
        ),
        (
            "reject_queued_job",
            JobStatus.QUEUED,
            {"kind": "document", "inputs": {"prompt": "Create guide"}},
            None,
            409,
            None,
        ),
        (
            "reject_running_job",
            JobStatus.RUNNING,
            {"kind": "document", "inputs": {"prompt": "Create guide"}},
            None,
            409,
            None,
        ),
        (
            "reject_succeeded_job",
            JobStatus.SUCCEEDED,
            {"kind": "document", "inputs": {"prompt": "Create guide"}},
            None,
            409,
            None,
        ),
        (
            "reject_stale_revision",
            JobStatus.FAILED,
            {
                "kind": "document",
                "inputs": {
                    "prompt": "Revise guide",
                    "artifactId": "00000000-0000-0000-0000-000000000001",
                    "expectedRevisionId": "00000000-0000-0000-0000-000000000002",
                },
            },
            {
                "head_revision_id": "00000000-0000-0000-0000-000000000003",
                "revision_id": "00000000-0000-0000-0000-000000000003",
                "kind": "document",
            },
            409,
            None,
        ),
        (
            "reject_missing_artifact",
            JobStatus.FAILED,
            {
                "kind": "document",
                "inputs": {
                    "prompt": "Revise guide",
                    "artifactId": "00000000-0000-0000-0000-000000000001",
                    "expectedRevisionId": "00000000-0000-0000-0000-000000000002",
                },
            },
            "not_found",
            404,
            None,
        ),
        (
            "reject_other_workspace",
            JobStatus.FAILED,
            {"kind": "document", "inputs": {"prompt": "Create guide"}},
            None,
            404,
            None,
        ),
    ],
)
def test_retry_run_table(case, status, payload, conn_row, expected_status, expected_state):
    scope = "local-development"
    job_scope = "other-workspace" if case == "reject_other_workspace" else scope
    fake_conn = (
        _FakeRevisionConn(None if conn_row == "not_found" else conn_row)
        if conn_row is not None
        else None
    )
    client, store = client_for(scope=scope, conn=fake_conn)
    run_id = _queue(store, f"key-{case}", payload.get("inputs"), scope=job_scope)
    job = store.get(UUID(run_id))
    store.jobs[job.job_id] = Job(**{**job.__dict__, "status": status, "payload": payload})

    res = client.post(f"/api/runs/{run_id}/retry")
    assert res.status_code == expected_status
    if expected_status == 200:
        body = res.json()
        assert body["state"] == expected_state
        assert body["runId"] != run_id
        new_job = store.get(UUID(body["runId"]))
        assert new_job.payload["inputs"] == payload["inputs"]


def test_response_models_keep_the_exact_wire_shape_absent_keys_stay_absent():
    client, store = client_for()
    run_id = _queue(store, "wire-shape", {"prompt": "shape"})
    created = client.get(f"/api/runs/{run_id}").json()
    # No artifact ids, revisions, error, or other optional fields appear as null.
    assert set(created) == {"runId", "state"}
    event = client.get(f"/api/runs/{created['runId']}/events").json()[0]
    assert set(event) == {"eventId", "runId", "state"}
    # A cancelled run carries an explicit null error: set-to-null is not "unset".
    cancelled = client.post(f"/api/runs/{created['runId']}/cancel").json()
    assert cancelled == {"runId": created["runId"], "state": "cancelled", "error": None}
