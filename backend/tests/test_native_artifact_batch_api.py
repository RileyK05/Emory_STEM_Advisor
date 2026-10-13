import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.batches import _public, router
from app.auth.dependencies import get_workspace_key
from app.jobs.native_artifact_batches import ArtifactBatchInput


def test_public_batch_status_never_contains_model_text_or_provider_handle():
    secret_text = "private generated candidate that must not be returned"
    item = SimpleNamespace(
        correlation_id="item-1", state="completed", publication_state="withheld",
        artifact_id=None, revision_id=None, error_code=None,
        result=SimpleNamespace(text=secret_text), usage=SimpleNamespace(prompt_tokens=8),
    )
    snapshot = SimpleNamespace(
        batch_id=uuid4(), state="completed", item_count=1, total_count=1,
        completed_count=1, failed_count=0, provider_batch_id="private-remote-id",
        items=(item,),
    )
    public = _public(snapshot)
    assert secret_text not in repr(public)
    assert "private-remote-id" not in repr(public)
    assert public["items"][0]["publicationState"] == "withheld"


def test_batch_rejects_dependent_plan_before_database_or_connection_setup():
    app = FastAPI()
    app.include_router(router)
    app.state.settings = SimpleNamespace(database_url="unused", routing_enabled=False)
    setup_calls = []

    def forbidden_setup():
        setup_calls.append("database")
        raise AssertionError("batch plan rejection must happen before database setup")

    app.state.batch_connect_factory = forbidden_setup
    app.state.config = object()
    app.state.embedder = object()
    app.state.connection_profiles = object()
    app.state.connection_runtime = object()
    app.dependency_overrides[get_workspace_key] = lambda: "workspace-test"
    client = TestClient(app)
    response = client.post("/api/artifact-batches", json={
        "batchId": str(uuid4()), "kind": "document", "items": [{
            "itemId": str(uuid4()), "inputs": {
                "prompt": "Write a guide", "mode": "authoring", "outputPlan": {
                    "kind": "document", "title": "Guide", "sections": [{
                        "key": "intro", "title": "Introduction",
                        "min_words": 1, "target_words": 1, "max_words": 1,
                        "min_blocks": 1, "target_blocks": 1, "max_blocks": 1,
                    }], "max_total_blocks": 2, "max_output_tokens_per_unit": 64,
                    "max_repairs_per_unit": 0,
                },
            },
        }],
    })
    assert response.status_code == 422
    assert response.json()["detail"] == "dependent output plans are only supported by /api/runs"
    assert setup_calls == []


def test_independent_batch_keeps_historical_fingerprint_for_null_output_plan(monkeypatch):
    import app.jobs.native_artifact_batches as batches

    item_id, batch_id = uuid4(), uuid4()
    body = ArtifactBatchInput.model_validate({
        "batchId": str(batch_id), "kind": "document", "items": [{
            "itemId": str(item_id), "inputs": {"prompt": "make a guide", "mode": "authoring"},
        }],
    })
    historical = body.model_dump(mode="json")
    historical["items"][0]["inputs"].pop("outputPlan", None)
    expected = hashlib.sha256(json.dumps(
        historical, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    observed = []
    replay = object()

    def idempotent_replay(self, seen_batch_id, workspace_key, fingerprint):
        observed.append((seen_batch_id, workspace_key, fingerprint))
        return replay

    monkeypatch.setattr(batches.ProviderBatchService, "idempotent_replay", idempotent_replay)
    setup_calls = []

    def forbidden_connect():
        setup_calls.append("connect")
        raise AssertionError("a historical replay should not set up submission")

    result = batches.create_artifact_batch(
        body, settings=SimpleNamespace(), config=None, embedder=None, profiles=None,
        runtime=None, connect=forbidden_connect, workspace_key="workspace-a",
    )
    assert result is replay
    assert observed == [(batch_id, "workspace-a", expected)]
    assert setup_calls == []
