from uuid import uuid4

import pytest

from app.db.connection import connect
from app.jobs.provider_batches import (
    ProviderBatchConflict,
    ProviderBatchNotFound,
    ProviderBatchNotPublishable,
    ProviderBatchService,
    ProviderBatchUnknown,
)
from app.llm.base import GenerationRequest, GenerationResult, Usage
from app.llm.batch import BatchItem, BatchItemResult, BatchProgress, BatchSubmission
from app.operations.budget import OperationLimits


class FakeBatchProvider:
    provider = "openai"
    model = "test-model"

    def __init__(self, *, states=None, results=None, submit_error=None):
        self.states = list(states or ["in_progress", "completed"])
        self.items = None
        self.submit_calls = 0
        self.result_calls = 0
        self.results_value = results
        self.submit_error = submit_error

    def submit(self, items):
        self.submit_calls += 1
        self.items = tuple(items)
        if self.submit_error:
            raise self.submit_error
        self.remote_id = f"remote-{uuid4()}"
        return BatchSubmission(self.remote_id, self.provider, "in_progress", len(items), "file-1")

    def status(self, batch_id):
        state = self.states.pop(0) if self.states else "completed"
        return BatchProgress(batch_id, self.provider, state, len(self.items),
                             len(self.items) if state == "completed" else 0, 0)

    def results(self, batch_id, *, expected_ids=None):
        self.result_calls += 1
        if self.results_value is not None:
            return self.results_value
        return tuple(BatchItemResult(key, result=GenerationResult(
            text=f"answer {key}", model=self.model,
            usage=Usage(prompt_tokens=7, completion_tokens=3, source="provider"),
        )) for key in expected_ids)

    def cancel(self, batch_id):
        return BatchProgress(batch_id, self.provider, "cancelled", len(self.items), 0, 0)


def _items(*ids):
    return [BatchItem(key, GenerationRequest(({"role": "user", "content": f"question {key}"},),
                                             model="test-model", max_output_tokens=32))
            for key in ids]


def _limits(_item):
    return OperationLimits(active_budget_seconds=3600, max_calls=1,
                           total_token_budget=5000, per_call_output_cap=64,
                           per_call_timeout_seconds=60)


def test_batch_submission_survives_service_restart_and_accounts_each_item(db, migrated_dsn):
    service = ProviderBatchService(lambda: connect(migrated_dsn))
    provider = FakeBatchProvider(states=["completed"])
    batch_id = uuid4()
    submitted = service.submit(batch_id=batch_id, workspace_key="workspace-a",
                               provider=provider, items=_items("a", "b"), limits=_limits)
    assert submitted.state == "in_progress"
    assert provider.submit_calls == 1

    restarted = ProviderBatchService(lambda: connect(migrated_dsn))
    polled = restarted.poll(batch_id, "workspace-a", provider)
    assert polled.state == "completed"
    assert {item.state for item in polled.items} == {"completed"}
    assert len(restarted.publishable_results(batch_id, "workspace-a", require_all=True)) == 2
    # Re-reading a terminal batch does not account a second provider result.
    restarted.poll(batch_id, "workspace-a", provider)
    assert provider.result_calls == 1


def test_ambiguous_submission_is_never_retried(db, migrated_dsn):
    service = ProviderBatchService(lambda: connect(migrated_dsn))
    provider = FakeBatchProvider(submit_error=TimeoutError("ambiguous"))
    batch_id = uuid4()
    with pytest.raises(ProviderBatchUnknown):
        service.submit(batch_id=batch_id, workspace_key="workspace-a",
                       provider=provider, items=_items("a"), limits=_limits)
    with pytest.raises(ProviderBatchUnknown):
        service.submit(batch_id=batch_id, workspace_key="workspace-a",
                       provider=provider, items=_items("a"), limits=_limits)
    assert provider.submit_calls == 1


def test_workspace_and_immutable_input_binding(db, migrated_dsn):
    service = ProviderBatchService(lambda: connect(migrated_dsn))
    provider = FakeBatchProvider()
    batch_id = uuid4()
    service.submit(batch_id=batch_id, workspace_key="workspace-a", provider=provider,
                   items=_items("a"), limits=_limits)
    with pytest.raises(ProviderBatchNotFound):
        service.get(batch_id, "workspace-b")
    with pytest.raises(ProviderBatchConflict):
        service.submit(batch_id=batch_id, workspace_key="workspace-a", provider=provider,
                       items=_items("changed"), limits=_limits)


def test_artifact_client_retry_resolves_from_persisted_hash_without_remote_resubmit(db, migrated_dsn):
    service = ProviderBatchService(lambda: connect(migrated_dsn))
    provider = FakeBatchProvider(states=["completed"])
    batch_id = uuid4()
    fingerprint = "a" * 64
    service.submit(
        batch_id=batch_id, workspace_key="workspace-a", provider=provider,
        items=_items("a"), limits=_limits,
        consumer_data={"a": {"client_request_fingerprint": fingerprint,
                              "role": "fast_generation"}},
    )
    replay = service.idempotent_replay(batch_id, "workspace-a", fingerprint)
    assert replay is not None and replay.batch_id == batch_id
    assert provider.submit_calls == 1
    with pytest.raises(ProviderBatchConflict):
        service.idempotent_replay(batch_id, "workspace-a", "b" * 64)


def test_partial_cancelled_outputs_are_accounted_but_unknown_items_not_published(db, migrated_dsn):
    service = ProviderBatchService(lambda: connect(migrated_dsn))
    good = BatchItemResult("a", result=GenerationResult(
        text="answer", model="test-model",
        usage=Usage(prompt_tokens=5, completion_tokens=4, source="provider"),
    ))
    missing = BatchItemResult("b", error="provider did not return this item")
    provider = FakeBatchProvider(states=["in_progress", "cancelled"], results=(good, missing))
    batch_id = uuid4()
    service.submit(batch_id=batch_id, workspace_key="workspace-a", provider=provider,
                   items=_items("a", "b"), limits=_limits)
    snapshot = service.cancel(batch_id, "workspace-a", provider)
    assert snapshot.state == "cancelled"
    assert {item.correlation_id: item.state for item in snapshot.items} == {
        "a": "completed", "b": "unknown",
    }
    with pytest.raises(ProviderBatchNotPublishable, match="cancelled batch"):
        service.publishable_results(batch_id, "workspace-a")
    by_id = {item.correlation_id: item for item in snapshot.items}
    with connect(migrated_dsn) as conn:
        accounted = conn.execute(
            "SELECT status, measured_prompt_tokens, measured_completion_tokens "
            "FROM operation_calls WHERE operation_id=%s", (by_id["a"].operation_id,),
        ).fetchone()
        reserved = conn.execute(
            "SELECT c.status, o.reserved_tokens FROM operation_calls c "
            "JOIN operations o USING(operation_id) WHERE c.operation_id=%s",
            (by_id["b"].operation_id,),
        ).fetchone()
    assert (accounted["status"], accounted["measured_prompt_tokens"],
            accounted["measured_completion_tokens"]) == ("completed", 5, 4)
    assert reserved["status"] == "cancelled" and reserved["reserved_tokens"] > 0
    with pytest.raises(ProviderBatchNotPublishable, match="cancelled batch"):
        service.publishable_results(batch_id, "workspace-a", require_all=True)
