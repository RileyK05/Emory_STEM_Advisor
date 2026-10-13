import time
from datetime import UTC, datetime

import pytest

from app.db.connection import connect
from app.jobs import JobCancelled, JobStatus, JobStore, LeaseLost, run_once
from tests.fakes import FakeClock


def make_store(dsn, clock=None):
    return JobStore(lambda: connect(dsn), clock=clock or (lambda: datetime.now(UTC)))


def test_idempotent_create_returns_same_record_once(migrated_dsn, db):
    store = make_store(migrated_dsn)
    first = store.create(
        kind="answer", scope_key="user:17", idempotency_key="req-1", payload={"q": "a"}
    )
    again = store.create(
        kind="answer", scope_key="user:17", idempotency_key="req-1", payload={"q": "changed"}
    )
    assert again.job_id == first.job_id
    assert again.payload == {"q": "a"}
    assert [event.event_type for event in store.events(first.job_id)] == ["queued"]


def test_claim_progress_heartbeat_and_completion_are_ordered(migrated_dsn, db):
    clock = FakeClock()
    store = make_store(migrated_dsn, clock)
    created = store.create(
        kind="render", scope_key="user:1", idempotency_key="render-1", payload={"format": "md"}
    )
    claimed = store.claim("worker-a", lease_seconds=10)
    assert claimed and claimed.job_id == created.job_id
    assert claimed.status is JobStatus.RUNNING and claimed.attempt_count == 1
    store.progress(claimed.job_id, claimed.lease_token, {"step": "parse", "percent": 25})
    store.heartbeat(claimed.job_id, claimed.lease_token, lease_seconds=15)
    assert store._finish_success(claimed, claimed.lease_token, {"artifact_id": "a1"}).status is JobStatus.SUCCEEDED
    events = store.events(created.job_id)
    assert [event.event_type for event in events] == ["queued", "claimed", "progress", "succeeded"]
    assert [event.event_id for event in events] == sorted(event.event_id for event in events)


def test_cancellation_wins_against_late_executor_success(migrated_dsn, db):
    store = make_store(migrated_dsn)
    created = store.create(
        kind="generate", scope_key="user:2", idempotency_key="g-1", payload={"prompt": "x"}
    )

    def slow_executor(job, context):
        assert store.cancel(job.job_id).status is JobStatus.RUNNING
        return {"late": True}

    finished = run_once(store, "worker-a", slow_executor)
    assert finished and finished.status is JobStatus.CANCELLED
    assert finished.result is None
    assert [event.event_type for event in store.events(created.job_id)] == [
        "queued", "claimed", "cancel_requested", "cancelled"
    ]


def test_cooperative_cancellation_signal(migrated_dsn, db):
    store = make_store(migrated_dsn)
    store.create(
        kind="work", scope_key="user:3", idempotency_key="g-2", payload={}
    )

    def executor(job, context):
        store.cancel(job.job_id)
        with pytest.raises(JobCancelled):
            context.check_cancelled()
        raise JobCancelled("requested")

    assert run_once(store, "worker-a", executor).status is JobStatus.CANCELLED


def test_failure_retries_then_dead_letters_at_attempt_limit(migrated_dsn, db):
    clock = FakeClock()
    store = make_store(migrated_dsn, clock)
    created = store.create(
        kind="batch", scope_key="team:a", idempotency_key="batch-1", payload={}, max_attempts=2
    )
    first = store.claim("worker-a", lease_seconds=30)
    queued = store._finish_failure(first, first.lease_token, {"type": "temporary", "message": "retry"})
    assert queued.status is JobStatus.QUEUED and queued.finished_at is None
    clock.advance(1)
    second = store.claim("worker-b", lease_seconds=30)
    assert second.attempt_count == 2
    failed = store._finish_failure(second, second.lease_token, {"type": "permanent", "message": "stop"})
    assert failed.status is JobStatus.FAILED and failed.finished_at is not None
    assert [e.event_type for e in store.events(created.job_id)] == [
        "queued", "claimed", "retry_scheduled", "claimed", "failed"
    ]


def test_expired_lease_requeues_and_fences_old_worker(migrated_dsn, db):
    clock = FakeClock()
    store = make_store(migrated_dsn, clock)
    created = store.create(
        kind="extract", scope_key="user:4", idempotency_key="extract-1", payload={}, max_attempts=2
    )
    stale = store.claim("worker-old", lease_seconds=2)
    clock.advance(3)
    assert store.recover_expired() == 1
    current = store.claim("worker-new", lease_seconds=10)
    assert current.job_id == created.job_id
    assert current.lease_token != stale.lease_token
    with pytest.raises(LeaseLost):
        store._finish_success(stale, stale.lease_token, {"wrong": True})
    assert store._finish_success(current, current.lease_token, {"right": True}).status is JobStatus.SUCCEEDED
    assert [e.event_type for e in store.events(created.job_id)] == [
        "queued", "claimed", "lease_recovered", "claimed", "succeeded"
    ]


def test_expired_final_attempt_is_failed(migrated_dsn, db):
    clock = FakeClock()
    store = make_store(migrated_dsn, clock)
    job = store.create(
        kind="extract", scope_key="user:5", idempotency_key="extract-2", payload={}, max_attempts=1
    )
    store.claim("worker", lease_seconds=1)
    clock.advance(2)
    assert store.recover_expired() == 1
    assert store.get(job.job_id).status is JobStatus.FAILED


def test_queued_cancellation_is_terminal_and_idempotent(migrated_dsn, db):
    store = make_store(migrated_dsn)
    job = store.create(
        kind="export", scope_key="user:6", idempotency_key="export-1", payload={}
    )
    assert store.cancel(job.job_id).status is JobStatus.CANCELLED
    assert store.cancel(job.job_id).status is JobStatus.CANCELLED
    assert store.claim("worker") is None


def test_executor_gets_automatic_heartbeats_during_long_work(migrated_dsn, db):
    store = make_store(migrated_dsn)
    created = store.create(
        kind="long", scope_key="user:7", idempotency_key="long-1", payload={}
    )

    def slow_executor(job, context):
        time.sleep(1.4)
        return {"done": True}

    finished = run_once(store, "worker-a", slow_executor, lease_seconds=1)
    assert finished.status is JobStatus.SUCCEEDED
    assert finished.job_id == created.job_id


def test_worker_never_persists_arbitrary_exception_text(migrated_dsn, db):
    store = make_store(migrated_dsn)
    created = store.create(
        kind="provider", scope_key="user:8", idempotency_key="provider-1", payload={}
    )

    def broken(job, context):
        raise RuntimeError("provider-key=very-secret request headers omitted")

    failed_attempt = run_once(store, "worker-a", broken)
    assert failed_attempt.status is JobStatus.QUEUED
    error = store.events(created.job_id)[-1].payload["error"]
    assert error == {"type": "execution_failed", "message": "Job execution failed."}
    assert "very-secret" not in str(error)


def test_event_cursor_returns_next_page(migrated_dsn, db):
    store = make_store(migrated_dsn)
    job = store.create(
        kind="steps", scope_key="user:9", idempotency_key="steps-1", payload={}
    )
    claimed = store.claim("worker-a")
    store.progress(claimed.job_id, claimed.lease_token, {"step": 1})
    first_page = store.events(job.job_id, limit=2)
    second_page = store.events(job.job_id, after_event_id=first_page[-1].event_id, limit=2)
    assert [e.event_type for e in first_page] == ["queued", "claimed"]
    assert [e.event_type for e in second_page] == ["progress"]


def test_lease_loss_prevents_late_worker_publication(migrated_dsn, db):
    clock = FakeClock()
    store = make_store(migrated_dsn, clock)
    created = store.create(
        kind="slow", scope_key="user:10", idempotency_key="slow-1", payload={}, max_attempts=2
    )
    reclaimed = []

    def executor(job, context):
        clock.advance(3)
        assert store.recover_expired() == 1
        new_owner = store.claim("worker-new", lease_seconds=30)
        reclaimed.append(new_owner)
        return {"stale": True}

    with pytest.raises(LeaseLost):
        run_once(store, "worker-old", executor, lease_seconds=2)
    current = store.get(created.job_id)
    assert current.status is JobStatus.RUNNING
    assert current.lease_token == reclaimed[0].lease_token
    assert current.result is None
