import json
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from app.connections.models import ProfileCreate
from app.connections.repository import ConnectionProfiles
from app.db import queries
from app.db.connection import connect
from app.jobs.native_artifact_batches import _publish_one
from app.jobs.provider_batches import ProviderBatchService
from app.llm.base import GenerationRequest, GenerationResult, Usage
from app.llm.batch import BatchItem, BatchItemResult, BatchProgress, BatchSubmission
from app.operations.budget import OperationLimits


class BatchProvider:
    provider = "openai"
    model = "batch-test-model"

    def __init__(self):
        self.remote = f"batch-{uuid4()}"
        self.result_reads = 0

    def submit(self, items):
        self.items = tuple(items)
        return BatchSubmission(self.remote, self.provider, "in_progress", len(items))

    def status(self, batch_id):
        return BatchProgress(batch_id, self.provider, "completed", len(self.items), len(self.items), 0)

    def results(self, batch_id, *, expected_ids=None):
        self.result_reads += 1
        return tuple(BatchItemResult(key, result=GenerationResult(
            text='{"spec_json":"{}","bindings":[]}', model=self.model,
            usage=Usage(prompt_tokens=10, completion_tokens=5, source="provider"),
        )) for key in expected_ids)

    def cancel(self, batch_id):
        return BatchProgress(batch_id, self.provider, "cancelled", len(self.items), 0, 0)


def _request():
    return GenerationRequest(({"role": "user", "content": "make a worksheet"},),
                            model="batch-test-model", max_output_tokens=50)


def _limits(_item):
    return OperationLimits(active_budget_seconds=3600, max_calls=1,
                           total_token_budget=5000, per_call_output_cap=100,
                           per_call_timeout_seconds=60)


def test_batch_model_call_is_audited_once_with_usage(collection, db, migrated_dsn):
    trace_id = uuid4()
    db.execute(queries.get("insert_trace"), {
        "trace_id": trace_id, "collection_id": collection, "query": "make worksheet",
        "normalized_query": "make worksheet", "config_version": 1,
        "embed_model": None, "refused": False,
    })
    db.commit()
    provider = BatchProvider()
    batch_id = uuid4()
    service = ProviderBatchService(lambda: connect(migrated_dsn))
    service.submit(
        batch_id=batch_id, workspace_key="workspace-a", provider=provider,
        items=[BatchItem("one", _request())], limits=_limits,
        consumer_data={"one": {"trace_id": str(trace_id), "role": "fast_generation",
                                "data_scope": "institutional_corpus",
                                "request_policy": {"sampling_policy": "greedy",
                                                   "max_output_tokens": 50,
                                                   "timeout_seconds": 60,
                                                   "temperature": 0}}},
    )
    service.poll(batch_id, "workspace-a", provider)
    service.poll(batch_id, "workspace-a", provider)
    with connect(migrated_dsn) as conn:
        rows = conn.execute(
            "SELECT provider, model_id, prompt_tokens, completion_tokens, usage_source, operation_id "
            "FROM model_calls WHERE trace_id = %s", (trace_id,),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["provider"] == "openai"
    assert rows[0]["prompt_tokens"] == 10 and rows[0]["completion_tokens"] == 5
    assert rows[0]["usage_source"] == "provider"
    assert provider.result_reads == 1


def test_profile_rotation_withholds_output_but_keeps_measured_accounting(db, migrated_dsn):
    profiles = ConnectionProfiles(Fernet.generate_key())
    with connect(migrated_dsn) as conn:
        profile = profiles.create(conn, ProfileCreate(
            name="native-batch", provider="openai", model_id="batch-test-model",
            roles=frozenset({"fast_generation"}),
            capabilities=frozenset({"batch", "structured_output"}),
            data_scopes=frozenset({"institutional_corpus", "user_supplied_data"}),
            secret={"api_key": "not-a-real-key"},
        ))
        conn.commit()
    provider = BatchProvider()
    batch_id = uuid4()
    service = ProviderBatchService(lambda: connect(migrated_dsn))
    submitted = service.submit(
        batch_id=batch_id, workspace_key="workspace-a", provider=provider,
        items=[BatchItem("one", _request())], limits=_limits,
        profile_id=profile.profile_id, profile_revision=profile.revision,
    )
    assert submitted.state == "in_progress"
    service.poll(batch_id, "workspace-a", provider)
    with connect(migrated_dsn) as conn:
        profiles.clear_secret(conn, profile.profile_id, expected_revision=profile.revision)
        conn.commit()
    published = service.publish_artifacts(
        batch_id, "workspace-a", lambda *_args: pytest.fail("stale profile must not publish"),
    )
    assert published == ()
    snapshot = service.get(batch_id, "workspace-a")
    assert snapshot.items[0].state == "completed"
    assert snapshot.items[0].publication_state == "withheld"
    with connect(migrated_dsn) as conn:
        status = conn.execute(
            "SELECT status, measured_prompt_tokens, measured_completion_tokens "
            "FROM operation_calls WHERE operation_id = %s", (snapshot.items[0].operation_id,),
        ).fetchone()
    assert status["status"] == "completed"
    assert (status["measured_prompt_tokens"], status["measured_completion_tokens"]) == (10, 5)


def test_validated_artifact_is_created_once_under_batch_fence(collection, db, migrated_dsn, tmp_path):
    trace_id = uuid4()
    db.execute(queries.get("insert_trace"), {
        "trace_id": trace_id, "collection_id": collection, "query": "make worksheet",
        "normalized_query": "make worksheet", "config_version": 1,
        "embed_model": None, "refused": False,
    })
    db.commit()
    spec = {"kind": "document", "title": "Guide",
            "blocks": [{"id": "p1", "type": "paragraph", "text": "A draft."}]}
    output = json.dumps({"spec_json": json.dumps(spec), "bindings": []})
    provider = BatchProvider()
    service = ProviderBatchService(lambda: connect(migrated_dsn))
    batch_id = uuid4()
    service.submit(
        batch_id=batch_id, workspace_key="workspace-a", provider=provider,
        items=[BatchItem("one", _request())], limits=_limits,
        consumer_data={"one": {"kind": "document", "inputs": {
            "prompt": "make a worksheet", "mode": "authoring", "sourceIds": None,
            "collectionId": None, "artifactId": None, "expectedRevisionId": None,
        }, "chunks": [], "prior": None, "trace_id": str(trace_id)}},
    )
    # Replace the fake response with valid native artifact JSON.
    provider.results = lambda _batch_id, *, expected_ids=None: tuple(
        BatchItemResult(key, result=GenerationResult(
            text=output, model=provider.model,
            usage=Usage(prompt_tokens=10, completion_tokens=5, source="provider"),
        )) for key in expected_ids
    )
    service.poll(batch_id, "workspace-a", provider)
    errors = []
    def publish(conn, parent, item, result):
        try:
            return _publish_one(conn, parent, item, result,
                                workspace_key="workspace-a", data_dir=tmp_path)
        except Exception as exc:
            errors.append(repr(exc))
            raise
    published = service.publish_artifacts(
        batch_id, "workspace-a",
        publish,
    )
    assert not errors, errors
    assert len(published) == 1
    assert service.publish_artifacts(
        batch_id, "workspace-a",
        lambda *_args: pytest.fail("published artifacts cannot be published twice"),
    ) == ()
    snapshot = service.get(batch_id, "workspace-a")
    assert snapshot.items[0].publication_state == "published"
    assert snapshot.items[0].artifact_id == published[0]["artifact_id"]
