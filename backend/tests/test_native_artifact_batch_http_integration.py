"""Real HTTP and PostgreSQL coverage for native artifact batch consumers."""
import json
from dataclasses import replace
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import load_settings
from app.connections.models import EvaluationEvidence, ProfileCreate
from app.db.connection import connect
from app.jobs.provider_batches import ProviderBatchConflict, ProviderBatchService
from app.llm.base import GenerationRequest, GenerationResult, Usage
from app.llm.batch import BatchItem, BatchItemResult, BatchProgress, BatchSubmission
from app.operations.budget import OperationLimits
from tests.fakes import FakeEmbedder


class FakeBatchAdapter:
    provider = "openai"
    model = "batch-http-test"

    def __init__(self, *, usage=True, finish="stop"):
        self.submissions = 0
        self.status_calls = 0
        self.result_calls = 0
        self.cancel_calls = 0
        self.usage = usage
        self.finish = finish
        self.keys = ()
        self.remote = f"remote-{uuid4()}"
        self.candidate = json.dumps({
            "spec_json": json.dumps({"kind": "document", "title": "Guide",
                                     "blocks": [{"id": "p1", "type": "paragraph",
                                                 "text": "A draft."}]}),
            "bindings": [],
        })

    def submit(self, items):
        self.submissions += 1
        self.remote = f"remote-{uuid4()}"
        self.keys = tuple(item.correlation_id for item in items)
        return BatchSubmission(self.remote, self.provider, "in_progress", len(self.keys))

    def status(self, batch_id):
        self.status_calls += 1
        return BatchProgress(batch_id, self.provider, "completed", len(self.keys), len(self.keys), 0)

    def results(self, batch_id, *, expected_ids=None):
        self.result_calls += 1
        usage = (Usage(prompt_tokens=10, completion_tokens=5, source="provider")
                 if self.usage else Usage())
        return tuple(BatchItemResult(key, result=GenerationResult(
            text=self.candidate, model=self.model, finish_reason=self.finish, usage=usage,
        )) for key in expected_ids)

    def cancel(self, batch_id):
        self.cancel_calls += 1
        return BatchProgress(batch_id, self.provider, "cancelled", len(self.keys), 0, 0)


class FakeRuntime:
    def __init__(self, adapter):
        self.adapter = adapter
        self.batch_builds = 0
        self.existing_builds = 0
        self.existing_error = False

    def generation_provider(self, *args, **kwargs):
        return object()

    def batch_provider(self, *args, **kwargs):
        self.batch_builds += 1
        return self.adapter

    def existing_batch_provider(self, profile, *, expected_provider):
        self.existing_builds += 1
        if self.existing_error:
            raise RuntimeError("simulated missing credential")
        assert expected_provider == profile.provider
        return self.adapter


def _app(db, migrated_dsn, tmp_path, adapter, collection):
    suite_hash = "a" * 64
    settings = replace(
        load_settings(), database_url=migrated_dsn, data_dir=tmp_path,
        default_collection=db.execute("SELECT name FROM collections WHERE collection_id=%s", (collection,)).fetchone()["name"], routing_enabled=True,
        routing_eval_suite_hash=suite_hash,
        profile_encryption_key=Fernet.generate_key().decode(),
        llm_max_output_tokens=100, llm_timeout_seconds=30,
    )
    app = create_app(settings=settings, allow_test_client=True,
                     embedder=FakeEmbedder(dim=8), provider=object())
    app.state.batch_connect_factory = lambda: connect(migrated_dsn)
    app.state.connection_runtime = FakeRuntime(adapter)
    with connect(migrated_dsn) as conn:
        profile = app.state.connection_profiles.create(conn, ProfileCreate(
            name="evaluated batch profile", provider="openai", model_id=adapter.model,
            roles=frozenset({"fast_generation", "complex"}),
            capabilities=frozenset({"batch", "structured_output"}),
            data_scopes=frozenset({"institutional_corpus", "user_supplied_data", "private_workspace"}),
            secret={"api_key": "test-only-secret"},
        ))
        app.state.connection_profiles.record_evaluation(conn, EvaluationEvidence(
            evaluation_id=uuid4(), profile_id=profile.profile_id, profile_revision=profile.revision,
            provider=profile.provider, model_id=profile.model_id, suite_hash=suite_hash,
            status="passed", metrics={"fixture": 1.0},
        ))
        conn.commit()
    return app, profile


def _body(batch_id=None):
    return {"batchId": str(batch_id or uuid4()), "kind": "document", "items": [{
        "itemId": str(uuid4()),
        "inputs": {"prompt": "Write a short guide", "mode": "authoring"},
    }]}


def test_http_submit_status_poll_and_artifact_flow_with_idempotent_replay(
        db, collection, migrated_dsn, tmp_path):
    adapter = FakeBatchAdapter()
    app, profile = _app(db, migrated_dsn, tmp_path, adapter, collection)
    client = TestClient(app)
    payload = _body()
    submitted = client.post("/api/artifact-batches", json=payload)
    assert submitted.status_code == 200, submitted.text
    batch_id = submitted.json()["batchId"]
    assert adapter.submissions == 1

    status = client.get(f"/api/artifact-batches/{batch_id}")
    assert status.status_code == 200
    assert adapter.status_calls == adapter.result_calls == 0  # GET is storage-only.

    polled = client.post(f"/api/artifact-batches/{batch_id}/poll")
    assert polled.status_code == 200, polled.text
    assert adapter.status_calls == 1 and adapter.result_calls == 1
    assert polled.json()["items"][0]["publicationState"] == "published", polled.json()["items"]
    artifact_id = polled.json()["items"][0]["artifactId"]
    artifact = client.get(f"/api/artifacts/{artifact_id}")
    assert artifact.status_code == 200, artifact.text
    assert artifact.json()["kind"] == "document"

    # A retry resolves from its stored request hash before changed profile settings are read.
    with connect(migrated_dsn) as conn:
        app.state.connection_profiles.clear_secret(conn, profile.profile_id,
                                                   expected_revision=profile.revision)
        conn.commit()
    app.state.connection_runtime.existing_error = True
    existing_builds = app.state.connection_runtime.existing_builds
    replay = client.post("/api/artifact-batches", json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()["batchId"] == batch_id
    assert adapter.submissions == 1
    assert app.state.connection_runtime.batch_builds == 1
    assert app.state.connection_runtime.existing_builds == existing_builds

    # Authentication is enforced and workspace IDs do not reveal another workspace's batch.
    unauthenticated = TestClient(app, client=("192.0.2.8", 1234), base_url="http://127.0.0.1")
    assert unauthenticated.get(f"/api/artifact-batches/{batch_id}").status_code == 401
    from app.auth.dependencies import get_workspace_key
    app.dependency_overrides[get_workspace_key] = lambda: "different-workspace"
    try:
        assert client.get(f"/api/artifact-batches/{batch_id}").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_http_cancel_and_unknown_usage_withhold_outputs_but_account_measured_results(
        db, collection, migrated_dsn, tmp_path):
    adapter = FakeBatchAdapter()
    app, _ = _app(db, migrated_dsn, tmp_path, adapter, collection)
    client = TestClient(app)
    payload = _body()
    submitted = client.post("/api/artifact-batches", json=payload)
    assert submitted.status_code == 200, submitted.text
    batch_id = submitted.json()["batchId"]
    # Collect a completed remote response without invoking artifact publication, as a completed
    # provider batch can still be explicitly cancelled before its consumer publishes.
    ProviderBatchService(app.state.batch_connect_factory).poll(batch_id, "development", adapter)
    cancelled = client.post(f"/api/artifact-batches/{batch_id}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    item = cancelled.json()["items"][0]
    assert cancelled.json()["cancelRequested"] is True
    assert item["publicationState"] == "withheld"
    assert item["artifactId"] is None
    assert adapter.cancel_calls == 0 and adapter.result_calls == 1
    snapshot = client.get(f"/api/artifact-batches/{batch_id}").json()
    operation_id = snapshot["items"][0]["itemId"]
    # The generation operation is linked via the durable batch item, and measured usage survives cancellation.
    with connect(migrated_dsn) as conn:
        row = conn.execute(
            "SELECT oc.status, oc.measured_prompt_tokens, oc.measured_completion_tokens "
            "FROM provider_batch_items i JOIN operation_calls oc ON oc.operation_id=i.operation_id "
            "WHERE i.correlation_id=%s", (operation_id,),
        ).fetchone()
    assert row and row["measured_prompt_tokens"] == 10 and row["measured_completion_tokens"] == 5

    # Separate batch with malformed/incomplete provider result and unknown usage is never published.
    bad = FakeBatchAdapter(usage=False, finish="length")
    app.state.connection_runtime.adapter = bad
    payload2 = _body()
    submitted2 = client.post("/api/artifact-batches", json=payload2)
    assert submitted2.status_code == 200, submitted2.text
    batch2 = submitted2.json()["batchId"]
    result2 = client.post(f"/api/artifact-batches/{batch2}/poll")
    assert result2.status_code == 200, result2.text
    item2 = result2.json()["items"][0]
    assert item2["publicationState"] == "withheld"
    assert item2["artifactId"] is None


def test_http_cancel_intent_survives_missing_provider_credential(
        db, collection, migrated_dsn, tmp_path):
    adapter = FakeBatchAdapter()
    app, _ = _app(db, migrated_dsn, tmp_path, adapter, collection)
    client = TestClient(app)
    submitted = client.post("/api/artifact-batches", json=_body())
    assert submitted.status_code == 200, submitted.text
    batch_id = submitted.json()["batchId"]
    app.state.connection_runtime.existing_error = True
    cancelled = client.post(f"/api/artifact-batches/{batch_id}/cancel")
    assert cancelled.status_code == 409
    status = client.get(f"/api/artifact-batches/{batch_id}")
    assert status.status_code == 200 and status.json()["cancelRequested"] is True
    assert adapter.cancel_calls == adapter.result_calls == 0


def test_cancel_intent_before_submission_prevents_remote_submit(db, migrated_dsn):
    adapter = FakeBatchAdapter()
    batch_id = uuid4()
    service = ProviderBatchService(lambda: connect(migrated_dsn))
    reserve = service._ensure_reservations

    def reserve_then_cancel(*args, **kwargs):
        reserve(*args, **kwargs)
        service.request_cancel(batch_id, "workspace-cancel-before-submit")

    service._ensure_reservations = reserve_then_cancel
    with pytest.raises(ProviderBatchConflict):
        service.submit(
            batch_id=batch_id, workspace_key="workspace-cancel-before-submit",
            provider=adapter,
            items=[BatchItem("one", GenerationRequest(
                ({"role": "user", "content": "draft"},), model=adapter.model,
                max_output_tokens=50))],
            limits=OperationLimits(active_budget_seconds=60, max_calls=1,
                                  total_token_budget=1000, per_call_output_cap=50,
                                  per_call_timeout_seconds=30),
        )
    assert adapter.submissions == 0
    assert service.get(batch_id, "workspace-cancel-before-submit").cancel_requested


def test_http_batch_listing_is_workspace_scoped_storage_only(db, collection, migrated_dsn, tmp_path):
    adapter = FakeBatchAdapter()
    app, _ = _app(db, migrated_dsn, tmp_path, adapter, collection)
    client = TestClient(app)
    from app.auth.dependencies import get_workspace_key

    app.dependency_overrides[get_workspace_key] = lambda: "workspace-a"
    first = client.post("/api/artifact-batches", json=_body())
    second = client.post("/api/artifact-batches", json=_body())
    assert first.status_code == second.status_code == 200
    app.dependency_overrides[get_workspace_key] = lambda: "workspace-b"
    other = client.post("/api/artifact-batches", json=_body())
    assert other.status_code == 200

    submissions = adapter.submissions
    provider_io = (adapter.status_calls, adapter.result_calls, adapter.cancel_calls)
    app.dependency_overrides[get_workspace_key] = lambda: "workspace-a"
    listed = client.get("/api/artifact-batches")
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    assert {row["batchId"] for row in rows} == {
        first.json()["batchId"], second.json()["batchId"]}
    assert len(rows) == 2
    assert all(row["itemCount"] == 1 and row["state"] == "in_progress" for row in rows)
    assert set(rows[0]) == {
        "batchId", "state", "cancelRequested", "itemCount", "totalCount",
        "completedCount", "failedCount", "createdAt", "updatedAt",
    }
    assert not any("prompt" in key.lower() or "provider" in key.lower() for row in rows for key in row)
    assert adapter.submissions == submissions
    assert (adapter.status_calls, adapter.result_calls, adapter.cancel_calls) == provider_io

    app.dependency_overrides[get_workspace_key] = lambda: "workspace-b"
    own = client.get("/api/artifact-batches")
    assert [row["batchId"] for row in own.json()] == [other.json()["batchId"]]
    app.dependency_overrides.clear()
