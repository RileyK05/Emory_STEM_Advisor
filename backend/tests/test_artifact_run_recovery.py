from datetime import UTC, datetime

import pytest

from app.db.connection import connect
from app.jobs import JobStatus, JobStore, LeaseLost, run_once
from app.jobs.artifact_plans import (
    ArtifactRunPlanInput,
    ArtifactRunPlanStore,
    ArtifactRunQuotas,
    ArtifactRunUnitInput,
)
from app.llm.base import Usage
from app.operations.budget import OperationBudgetController, OperationBudgetError, OperationLimits
from tests.fakes import FakeClock


def make_store(dsn, clock):
    return JobStore(lambda: connect(dsn), clock=clock)


def add_call(dsn, job, *, usage=None):
    """Persist a provider-attempt record under the artifact run's actual job ID."""
    budget = OperationBudgetController()
    with connect(dsn) as conn:
        budget.ensure(conn, job.job_id, job.scope_key, OperationLimits())
        reservation = budget.reserve_before_call(
            conn, job.job_id, prompt_reserved_tokens=20, requested_output_tokens=40
        )
        if usage is not None:
            budget.account_usage(conn, reservation, usage=usage)
    return reservation


def artifact_job(store, suffix, *, max_attempts=3):
    return store.create(
        kind="artifact.run", scope_key="workspace:test", idempotency_key=suffix,
        payload={"kind": "document"}, max_attempts=max_attempts,
    )


def test_expired_reserved_provider_call_is_unknown_terminal_and_never_reclaimed(
    migrated_dsn, db
):
    clock = FakeClock()
    store = make_store(migrated_dsn, clock)
    job = artifact_job(store, "reserved-crash")
    claimed = store.claim("worker-a", lease_seconds=1)
    reservation = add_call(migrated_dsn, claimed)
    assert reservation.operation_id == job.job_id

    clock.advance(2)
    assert store.recover_expired() == 1
    failed = store.get(job.job_id)
    assert failed.status is JobStatus.FAILED
    assert failed.error == {
        "type": "unknown_outcome",
        "message": "A model call may have completed; output was withheld to prevent duplicate execution.",
    }
    assert store.claim("worker-b") is None

    row = db.execute(
        "SELECT status FROM operation_calls WHERE operation_id = %s AND attempt_no = %s",
        (job.job_id, reservation.attempt_no),
    ).fetchone()
    budget = db.execute(
        "SELECT reserved_tokens, accounted_tokens FROM operations WHERE operation_id = %s",
        (job.job_id,),
    ).fetchone()
    assert row["status"] == "unknown"
    assert budget["reserved_tokens"] == 60
    assert budget["accounted_tokens"] == 0
    event = store.events(job.job_id)[-1]
    assert event.payload["error"] == failed.error


def test_expired_accounted_call_without_validated_checkpoint_is_not_repeated(
    migrated_dsn, db
):
    clock = FakeClock()
    store = make_store(migrated_dsn, clock)
    job = artifact_job(store, "returned-output-crash")
    claimed = store.claim("worker-a", lease_seconds=1)
    reservation = add_call(
        migrated_dsn,
        claimed,
        usage=Usage(prompt_tokens=22, completion_tokens=11, total_tokens=33, source="provider"),
    )
    clock.advance(2)

    assert store.recover_expired() == 1
    assert store.get(job.job_id).status is JobStatus.FAILED
    assert store.get(job.job_id).error["type"] == "lease_expired"
    assert store.claim("worker-b") is None
    call = db.execute(
        "SELECT status, measured_prompt_tokens, measured_completion_tokens "
        "FROM operation_calls WHERE operation_id = %s AND attempt_no = %s",
        (job.job_id, reservation.attempt_no),
    ).fetchone()
    budget = db.execute(
        "SELECT reserved_tokens, accounted_tokens FROM operations WHERE operation_id = %s",
        (job.job_id,),
    ).fetchone()
    assert call["status"] == "completed"
    assert (call["measured_prompt_tokens"], call["measured_completion_tokens"]) == (22, 11)
    assert budget["reserved_tokens"] == 0
    assert budget["accounted_tokens"] == 33


def test_late_measured_usage_settles_unknown_attempt_once_without_reopening_job(
    migrated_dsn, db
):
    clock = FakeClock()
    store = make_store(migrated_dsn, clock)
    job = artifact_job(store, "late-measured-usage")
    stale_owner = store.claim("worker-a", lease_seconds=1)
    reservation = add_call(migrated_dsn, stale_owner)
    clock.advance(2)
    assert store.recover_expired() == 1
    assert store.get(job.job_id).status is JobStatus.FAILED

    controller = OperationBudgetController()
    measured = Usage(prompt_tokens=31, completion_tokens=19, total_tokens=50, source="provider")
    with connect(migrated_dsn) as conn:
        settled = controller.account_usage(conn, reservation, usage=measured)
        assert settled.reserved_tokens == 0
        assert settled.accounted_tokens == 50
        with pytest.raises(OperationBudgetError, match="already accounted"):
            controller.account_usage(conn, reservation, usage=measured)

    budget = db.execute(
        "SELECT reserved_tokens, accounted_tokens FROM operations WHERE operation_id = %s",
        (job.job_id,),
    ).fetchone()
    call = db.execute(
        "SELECT status, measured_prompt_tokens, measured_completion_tokens "
        "FROM operation_calls WHERE operation_id = %s AND attempt_no = %s",
        (job.job_id, reservation.attempt_no),
    ).fetchone()
    assert budget["reserved_tokens"] == 0
    assert budget["accounted_tokens"] == 50
    assert call["status"] == "completed"
    assert (call["measured_prompt_tokens"], call["measured_completion_tokens"]) == (31, 19)
    assert store.get(job.job_id).status is JobStatus.FAILED
    assert store.claim("worker-b") is None
    with pytest.raises(LeaseLost):
        store._finish_success(stale_owner, stale_owner.lease_token, {"artifact_id": "late"})


def test_unknown_or_estimated_late_usage_does_not_release_reservation(migrated_dsn, db):
    clock = FakeClock()
    store = make_store(migrated_dsn, clock)
    job = artifact_job(store, "late-unknown-usage")
    store.claim("worker-a", lease_seconds=1)
    reservation = add_call(migrated_dsn, job)
    clock.advance(2)
    assert store.recover_expired() == 1

    with connect(migrated_dsn) as conn, pytest.raises(
        OperationBudgetError, match="already accounted"
    ):
        OperationBudgetController().account_usage(conn, reservation, usage=None)
    call = db.execute(
        "SELECT status FROM operation_calls WHERE operation_id = %s AND attempt_no = %s",
        (job.job_id, reservation.attempt_no),
    ).fetchone()
    budget = db.execute(
        "SELECT reserved_tokens, accounted_tokens FROM operations WHERE operation_id = %s",
        (job.job_id,),
    ).fetchone()
    assert call["status"] == "unknown"
    assert budget["reserved_tokens"] == 60
    assert budget["accounted_tokens"] == 0


def test_artifact_run_without_provider_attempt_can_be_recovered(migrated_dsn, db):
    clock = FakeClock()
    store = make_store(migrated_dsn, clock)
    job = artifact_job(store, "pre-call-crash")
    store.claim("worker-a", lease_seconds=1)
    # An operation budget alone records planning, not a provider attempt.
    with connect(migrated_dsn) as conn:
        OperationBudgetController().ensure(conn, job.job_id, job.scope_key, OperationLimits())
    clock.advance(2)

    assert store.recover_expired() == 1
    recovered = store.get(job.job_id)
    assert recovered.status is JobStatus.QUEUED
    assert recovered.error["type"] == "lease_expired"
    claimed = store.claim("worker-b")
    assert claimed.job_id == job.job_id
    assert claimed.attempt_count == 2


def test_normal_failure_after_model_attempt_is_terminal_and_not_retried(migrated_dsn, db):
    clock = FakeClock()
    store = make_store(migrated_dsn, clock)
    job = artifact_job(store, "worker-failure")
    calls = []

    def executor(claimed, _context):
        calls.append(claimed.job_id)
        assert claimed.job_id == job.job_id
        add_call(migrated_dsn, claimed)
        raise RuntimeError("provider response contained sensitive details")

    failed = run_once(store, "worker-a", executor, lease_seconds=30)
    assert failed.status is JobStatus.FAILED
    assert failed.error["type"] == "unknown_outcome"
    assert "sensitive" not in str(failed.error)
    assert run_once(store, "worker-b", executor) is None
    assert calls == [job.job_id]


def test_cancelled_late_accounting_is_preserved_and_never_requeued(migrated_dsn, db):
    clock = FakeClock()
    store = make_store(migrated_dsn, clock)
    job = artifact_job(store, "cancel-late-usage")
    claimed = store.claim("worker-a", lease_seconds=30)
    reservation = add_call(migrated_dsn, claimed)
    store.cancel(job.job_id)
    with connect(migrated_dsn) as conn:
        OperationBudgetController().account_usage(
            conn,
            reservation,
            usage=Usage(prompt_tokens=13, completion_tokens=7, total_tokens=20, source="provider"),
        )
    cancelled = store._finish_failure(
        claimed, claimed.lease_token, {"type": "execution_failed", "message": "Job execution failed."}
    )

    assert cancelled.status is JobStatus.CANCELLED
    assert cancelled.error is None
    assert store.claim("worker-b") is None
    budget = db.execute(
        "SELECT reserved_tokens, accounted_tokens FROM operations WHERE operation_id = %s",
        (job.job_id,),
    ).fetchone()
    assert budget["reserved_tokens"] == 0
    assert budget["accounted_tokens"] == 20


def test_recovery_requeues_only_plan_with_persisted_known_invalid_checkpoint(
    migrated_dsn, db, collection
):
    clock = FakeClock()
    clock.value = datetime.now(UTC)
    store = make_store(migrated_dsn, clock)
    job = artifact_job(store, "known-invalid-plan")
    claimed = store.claim("worker-a", lease_seconds=60)
    plan_store = ArtifactRunPlanStore(lambda: connect(migrated_dsn))
    plan_store.create_plan(claimed.job_id, claimed.lease_token, ArtifactRunPlanInput(
        workspace_key=claimed.scope_key, collection_id=collection, artifact_kind="document",
        frozen_inputs={"prompt": "known response"},
        units=(ArtifactRunUnitInput("section", "section", {"target": "fixed"}),),
        quotas=ArtifactRunQuotas(max_repairs_per_unit=1),
    ))
    reservation = add_call(migrated_dsn, claimed)
    plan_store.mark_calling(
        claimed.job_id, claimed.scope_key, claimed.lease_token, "section", reservation.attempt_no
    )
    OperationBudgetController().account_usage(
        db, reservation, usage=Usage(prompt_tokens=22, completion_tokens=11,
                                     total_tokens=33, source="provider"),
    )
    plan_store.mark_invalid_attempt(
        claimed.job_id, claimed.scope_key, claimed.lease_token, "section",
        reservation.attempt_no, "known but invalid candidate", ["section was too short"],
    )
    db.execute("UPDATE jobs SET lease_expires_at=clock_timestamp()-interval '1 second' "
               "WHERE job_id=%s", (job.job_id,))
    db.commit()

    assert store.recover_expired() == 1
    assert store.get(job.job_id).status is JobStatus.QUEUED
    retry = store.claim("worker-b", lease_seconds=60)
    units = ArtifactRunPlanStore(lambda: connect(migrated_dsn)).list_units(
        job.job_id, claimed.scope_key
    )
    assert retry.job_id == job.job_id and retry.attempt_count == 2
    assert units[0].status == "needs_repair"


def test_plan_recovery_never_requeues_unknown_provider_intent(migrated_dsn, db, collection):
    clock = FakeClock()
    clock.value = datetime.now(UTC)
    store = make_store(migrated_dsn, clock)
    job = artifact_job(store, "unknown-plan")
    claimed = store.claim("worker-a", lease_seconds=60)
    plan_store = ArtifactRunPlanStore(lambda: connect(migrated_dsn))
    plan_store.create_plan(claimed.job_id, claimed.lease_token, ArtifactRunPlanInput(
        workspace_key=claimed.scope_key, collection_id=collection, artifact_kind="document",
        frozen_inputs={"prompt": "unknown response"},
        units=(ArtifactRunUnitInput("section", "section", {"target": "fixed"}),),
    ))
    reservation = add_call(migrated_dsn, claimed)
    plan_store.mark_calling(
        claimed.job_id, claimed.scope_key, claimed.lease_token, "section", reservation.attempt_no
    )
    db.execute("UPDATE jobs SET lease_expires_at=clock_timestamp()-interval '1 second' "
               "WHERE job_id=%s", (job.job_id,))
    db.commit()

    assert store.recover_expired() == 1
    failed = store.get(job.job_id)
    assert failed.status is JobStatus.FAILED and failed.error["type"] == "unknown_outcome"
    assert store.claim("worker-b") is None
    units = ArtifactRunPlanStore(lambda: connect(migrated_dsn)).list_units(
        job.job_id, claimed.scope_key
    )
    assert units[0].status == "unknown"
