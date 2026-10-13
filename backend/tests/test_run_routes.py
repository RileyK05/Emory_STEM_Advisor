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

    def create(self, *, kind, scope_key, idempotency_key, payload):
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


def client_for(*, scope="local-development"):
    app = FastAPI()
    app.state.job_store = MemoryStore()
    app.state.settings = SimpleNamespace(
        operation_max_calls=2, operation_token_budget=16_000, llm_max_output_tokens=1024,
    )
    app.dependency_overrides[get_workspace_key] = lambda: scope
    app.dependency_overrides[get_conn] = lambda: None
    app.include_router(router)
    return TestClient(app), app.state.job_store


def test_run_create_idempotency_status_cursor_cancel_and_scope():
    client, store = client_for()
    body = {
        "kind": "document",
        "inputs": {"prompt": "Create a study guide"},
        "idempotencyKey": "same-request",
    }
    first = client.post("/api/runs", json=body)
    second = client.post("/api/runs", json=body)
    assert first.status_code == second.status_code == 200
    run_id = first.json()["runId"]
    assert second.json()["runId"] == run_id and first.json()["state"] == "queued"
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


def test_run_request_is_bounded_and_rejects_unknown_fields():
    client, _ = client_for()
    base = {"kind": "document", "inputs": {}, "idempotencyKey": "key"}
    assert client.post("/api/runs", json={**base, "unexpected": True}).status_code == 422
    assert (
        client.post("/api/runs", json={**base, "inputs": {"large": "x" * 70_000}}).status_code
        == 422
    )
    assert client.post("/api/runs", json={**base, "kind": "shell"}).status_code == 422


def test_impossible_output_plan_is_rejected_before_queuing():
    client, store = client_for()
    sections = [{
        "key": f"section-{index}", "title": f"Section {index}",
        "min_words": 1, "target_words": 1, "max_words": 1,
        "min_blocks": 1, "target_blocks": 1, "max_blocks": 1,
    } for index in range(2)]
    response = client.post("/api/runs", json={
        "kind": "document", "idempotencyKey": "over-budget-plan", "inputs": {
            "prompt": "Generate sections", "mode": "authoring", "outputPlan": {
                "kind": "document", "title": "Too many calls", "sections": sections,
                "max_total_blocks": 4, "max_output_tokens_per_unit": 64,
                "max_repairs_per_unit": 1,
            },
        },
    })
    assert response.status_code == 422
    assert store.jobs == {}


@pytest.mark.parametrize("sheet", [
    {"key": "data", "name": "Bad/Name", "columns": [
        {"key": "value", "label": "Value", "value_type": "text"}],
     "min_rows": 1, "target_rows": 1, "max_rows": 1},
    {"key": "s" * 120, "name": "Data", "columns": [
        {"key": "c" * 64, "label": "Value", "value_type": "text"}],
     "min_rows": 1, "target_rows": 1, "max_rows": 1},
])
def test_invalid_workbook_name_or_generated_cell_id_is_rejected_before_queuing(sheet):
    client, store = client_for()
    response = client.post("/api/runs", json={
        "kind": "workbook", "idempotencyKey": "invalid-workbook-plan", "inputs": {
            "prompt": "Generate a table", "mode": "authoring", "outputPlan": {
                "kind": "workbook", "title": "Invalid workbook", "sheets": [sheet],
                "max_total_cells": 100, "max_output_tokens_per_unit": 64,
                "max_repairs_per_unit": 0,
            },
        },
    })
    assert response.status_code == 422
    assert store.jobs == {}


def test_scope_resolver_is_trusted_and_not_request_body():
    client, _ = client_for(scope="alice")
    response = client.post(
        "/api/runs", json={"kind": "html", "inputs": {"scope_key": "bob"}, "idempotencyKey": "a"}
    )
    assert response.status_code == 200
    run_id = response.json()["runId"]
    client2, _ = client_for(scope="bob")
    assert client2.get(f"/api/runs/{run_id}").status_code == 404


def test_public_run_errors_are_sanitized():
    client, store = client_for()
    created = client.post(
        "/api/runs", json={"kind": "document", "inputs": {}, "idempotencyKey": "error"}
    ).json()
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


def test_run_limits_are_read_only_and_use_runtime_configuration():
    client, _ = client_for()
    client.app.state.settings = SimpleNamespace(
        operation_max_calls=7, operation_token_budget=91_000, llm_max_output_tokens=1536,
    )
    client.app.dependency_overrides[get_conn] = lambda: pytest.fail("limits must not open a DB")
    response = client.get("/api/runs/limits")
    assert response.status_code == 200
    assert response.json() == {
        "maxCalls": 7, "totalTokenBudget": 91_000, "perCallOutputCap": 1536,
    }


def test_run_and_event_drop_malformed_artifact_revision_pairs():
    client, store = client_for()
    created = client.post(
        "/api/runs", json={"kind": "document", "inputs": {}, "idempotencyKey": "bad-revision"}
    ).json()
    run_id = UUID(created["runId"])
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
    made = client.post("/api/runs", json={
        "kind": "document", "inputs": {}, "idempotencyKey": "progress"}).json()
    run_id = UUID(made["runId"])
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
    body = {
        "kind": "workbook",
        "inputs": {"prompt": "Build a grade tracker"},
        "idempotencyKey": "db-run-1",
    }
    created = client.post("/api/runs", json=body)
    assert created.status_code == 200
    run_id = created.json()["runId"]
    assert client.get(f"/api/runs/{run_id}").json()["state"] == "queued"
    assert client.get(f"/api/runs/{run_id}/events").json()[0]["state"] == "queued"
    assert client.post(f"/api/runs/{run_id}/cancel").json()["state"] == "cancelled"


def test_response_models_keep_the_exact_wire_shape_absent_keys_stay_absent():
    client, _ = client_for()
    created = client.post("/api/runs", json={
        "kind": "document", "inputs": {"prompt": "shape"}, "idempotencyKey": "wire-shape"}).json()
    # No artifact ids, revisions, error, or other optional fields appear as null.
    assert set(created) == {"runId", "state"}
    event = client.get(f"/api/runs/{created['runId']}/events").json()[0]
    assert set(event) == {"eventId", "runId", "state"}
    # A cancelled run carries an explicit null error: set-to-null is not "unset".
    cancelled = client.post(f"/api/runs/{created['runId']}/cancel").json()
    assert cancelled == {"runId": created["runId"], "state": "cancelled", "error": None}
    assert client.get("/api/runs/limits").json() == {
        "maxCalls": 2, "totalTokenBudget": 16_000, "perCallOutputCap": 1024}
