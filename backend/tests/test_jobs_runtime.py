import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.jobs.runtime import ArtifactPersistence, RunExecutor, StagedArtifact, StagedRunResult
from app.jobs.store import JobCancelled, JobStore, LeaseLost, SafeJobError
from app.jobs.types import Job, JobStatus


def job(*, kind="artifact.run", payload=None):
    now = datetime.now(UTC)
    return Job(
        job_id=uuid4(),
        kind=kind,
        scope_key="local",
        idempotency_key="key",
        payload=payload or {"kind": "document", "inputs": {"prompt": "x"}},
        result=None,
        error=None,
        status=JobStatus.RUNNING,
        attempt_count=1,
        max_attempts=3,
        available_at=now,
        lease_owner="worker",
        lease_token=uuid4(),
        lease_expires_at=now,
        heartbeat_at=now,
        cancel_requested=False,
        created_at=now,
        updated_at=now,
        finished_at=None,
    )


class Context:
    def __init__(self, *, cancelled=False):
        self.cancelled = cancelled
        self.progress_values = []

    def check_cancelled(self):
        if self.cancelled:
            raise JobCancelled("cancelled")

    def progress(self, payload):
        self.progress_values.append(payload)


def test_run_executor_uses_kind_handler_and_returns_staged_typed_spec():
    seen = []

    def handler(current_job, context):
        seen.append((current_job.payload["kind"], current_job.payload["inputs"]))
        return StagedRunResult(
            (
                StagedArtifact(
                    kind="document",
                    spec={"kind": "document", "title": "Draft", "blocks": []},
                ),
            )
        )

    context = Context()
    result = RunExecutor({"document": handler})(job(), context)
    assert seen == [("document", {"prompt": "x"})]
    assert isinstance(result, StagedRunResult)
    assert result.artifacts[0].spec["title"] == "Draft"
    assert context.progress_values == []


def test_run_executor_cancellation_and_bad_staged_results_are_safe():
    called = []
    with pytest.raises(JobCancelled):
        RunExecutor({"document": lambda *args: called.append(True)})(job(), Context(cancelled=True))
    assert called == []
    with pytest.raises(SafeJobError):
        RunExecutor({"document": lambda *_: {"artifactId": "not-a-uuid"}})(job(), Context())
    with pytest.raises(ValueError):
        StagedArtifact(kind="document", spec={"kind": "html", "title": "wrong", "html": ""})


def test_run_executor_rejects_unknown_kind_before_handler():
    called = []
    bad = job(payload={"kind": "shell", "inputs": {}})
    with pytest.raises(SafeJobError):
        RunExecutor({"document": lambda *args: called.append(True)})(bad, Context())
    assert called == []


def test_artifact_persistence_uses_typed_immutable_store(migrated_dsn, db, tmp_path):
    from app.db.connection import connect

    persistence = ArtifactPersistence(
        lambda: connect(migrated_dsn), data_dir=tmp_path, workspace_key="worker-local"
    )
    spec = {
        "kind": "document",
        "title": "Generated guide",
        "blocks": [{"id": "p1", "type": "paragraph", "text": "Grounded text"}],
    }
    created = persistence.create(spec)
    assert created["kind"] == "document" and created["spec"]["blocks"][0]["text"] == "Grounded text"
    changed = {
        **spec,
        "blocks": [{"id": "p1", "type": "paragraph", "text": "Revised grounded text"}],
    }
    revision = persistence.revise(
        UUID(created["artifactId"]), UUID(created["revisionId"]), changed, draft_id="run-revision-1"
    )
    assert revision["parentRevisionId"] == created["revisionId"]
    assert revision["spec"]["blocks"][0]["text"] == "Revised grounded text"


def _staged(text="Published"):
    return StagedRunResult(
        (
            StagedArtifact(
                kind="document",
                spec={
                    "kind": "document",
                    "title": text,
                    "blocks": [{"id": "p1", "type": "paragraph", "text": text}],
                },
            ),
        )
    )


def _executor(tmp_path):
    return RunExecutor({"document": lambda *_: _staged()}, data_dir=tmp_path)


def test_cancel_winning_before_fenced_publication_creates_no_artifact(migrated_dsn, db, tmp_path):
    from app.db.connection import connect

    store = JobStore(lambda: connect(migrated_dsn))
    store.create(
        kind="artifact.run",
        scope_key="cancel-before-publish",
        idempotency_key="cancel-before-publish",
        payload={"kind": "document", "inputs": {}},
    )
    claimed = store.claim("test-worker")
    assert claimed is not None and claimed.lease_token is not None
    executor = _executor(tmp_path)
    store.cancel(claimed.job_id)
    finished = store._finish_success(
        claimed, claimed.lease_token, _staged(), publisher=executor.publish_success
    )
    assert finished.status is JobStatus.CANCELLED
    conn = connect(migrated_dsn)
    try:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM artifacts WHERE workspace_key = %s",
                ("cancel-before-publish",),
            ).fetchone()["n"]
            == 0
        )
    finally:
        conn.close()


def test_publication_and_success_are_atomic_against_concurrent_cancel(migrated_dsn, db, tmp_path):
    from app.db.connection import connect

    store = JobStore(lambda: connect(migrated_dsn))
    store.create(
        kind="artifact.run",
        scope_key="publish-race",
        idempotency_key="publish-race",
        payload={"kind": "document", "inputs": {}},
    )
    claimed = store.claim("publish-worker")
    assert claimed is not None and claimed.lease_token is not None
    executor = _executor(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    def slow_publisher(conn, job_value, result):
        entered.set()
        assert release.wait(5)
        return executor.publish_success(conn, job_value, result)

    cancel_started = threading.Event()

    def cancel_job():
        cancel_started.set()
        return store.cancel(claimed.job_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        finishing = pool.submit(
            store._finish_success, claimed, claimed.lease_token, _staged(), publisher=slow_publisher
        )
        assert entered.wait(5)
        cancelling = pool.submit(cancel_job)
        assert cancel_started.wait(5)
        time.sleep(0.05)
        assert not cancelling.done()
        release.set()
        done = finishing.result(timeout=10)
        after_cancel = cancelling.result(timeout=10)
    assert done.status is JobStatus.SUCCEEDED
    assert after_cancel.status is JobStatus.SUCCEEDED
    assert len(done.result["artifactIds"]) == 1
    conn = connect(migrated_dsn)
    try:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM artifacts WHERE workspace_key = %s", ("publish-race",)
            ).fetchone()["n"]
            == 1
        )
    finally:
        conn.close()


def test_expired_lease_fences_old_publisher_and_reclaimed_job_publishes_once(
    migrated_dsn, db, tmp_path
):
    from app.db.connection import connect

    now = [datetime.now(UTC)]
    store = JobStore(lambda: connect(migrated_dsn), clock=lambda: now[0])
    store.create(
        kind="artifact.run",
        scope_key="recover-publish",
        idempotency_key="recover-publish",
        payload={"kind": "document", "inputs": {}},
    )
    old = store.claim("old-worker", lease_seconds=1)
    assert old is not None and old.lease_token is not None
    now[0] += timedelta(seconds=2)
    assert store.recover_expired() == 1
    current = store.claim("new-worker", lease_seconds=30)
    assert current is not None and current.lease_token is not None
    executor = _executor(tmp_path)
    with pytest.raises(LeaseLost):
        store._finish_success(
            old, old.lease_token, _staged("stale"), publisher=executor.publish_success
        )
    finished = store._finish_success(
        current, current.lease_token, _staged("fresh"), publisher=executor.publish_success
    )
    assert finished.status is JobStatus.SUCCEEDED
    conn = connect(migrated_dsn)
    try:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM artifacts WHERE workspace_key = %s", ("recover-publish",)
            ).fetchone()["n"]
            == 1
        )
    finally:
        conn.close()


def test_staged_profile_selection_is_fenced_before_artifact_publication(tmp_path):
    profile_id = uuid4()
    staged = StagedRunResult((StagedArtifact(
        kind="document", spec={"kind": "document", "title": "Draft", "blocks": []},
        profile_id=profile_id, profile_revision=3,
    ),))
    class Result:
        def fetchone(self):
            return {"enabled": True, "revision": 4}
    class EmptyResult:
        def fetchone(self):
            return None
    class Conn:
        def execute(self, query, params):
            if "FROM artifact_run_plans AS p" in query:
                return EmptyResult()
            assert params["profile_id"] == profile_id
            return Result()
    with pytest.raises(SafeJobError, match="changed during generation"):
        _executor(tmp_path).publish_success(Conn(), job(), staged)


def test_staged_profile_guard_requires_complete_positive_revision():
    with pytest.raises(ValueError):
        StagedArtifact(kind="document", spec={"kind": "document", "title": "x", "blocks": []}, profile_id=uuid4())
    with pytest.raises(ValueError):
        StagedArtifact(kind="document", spec={"kind": "document", "title": "x", "blocks": []}, profile_id=uuid4(), profile_revision=0)


def test_lease_expiring_during_fenced_publication_rolls_back_artifact(migrated_dsn, db, tmp_path):
    from app.db.connection import connect

    now = [datetime.now(UTC)]
    store = JobStore(lambda: connect(migrated_dsn), clock=lambda: now[0])
    store.create(
        kind="artifact.run", scope_key="expire-publish", idempotency_key="expire-publish",
        payload={"kind": "document", "inputs": {}},
    )
    claimed = store.claim("expiring-worker", lease_seconds=1)
    assert claimed is not None and claimed.lease_token is not None
    executor = _executor(tmp_path)

    def slow_publisher(conn, job_value, result):
        published = executor.publish_success(conn, job_value, result)
        now[0] += timedelta(seconds=2)
        return published

    with pytest.raises(LeaseLost):
        store._finish_success(
            claimed, claimed.lease_token, _staged(), publisher=slow_publisher
        )
    conn = connect(migrated_dsn)
    try:
        assert conn.execute(
            "SELECT count(*) AS n FROM artifacts WHERE workspace_key = %s",
            ("expire-publish",),
        ).fetchone()["n"] == 0
    finally:
        conn.close()

def test_fenced_publication_can_create_compare_and_swap_revision(migrated_dsn, db, tmp_path):
    from app.artifacts.models import DocumentSpec
    from app.artifacts.storage import ArtifactStore
    from app.db.connection import connect

    workspace = "atomic-revise"
    conn = connect(migrated_dsn)
    try:
        original = ArtifactStore(conn, workspace_key=workspace, data_dir=tmp_path).create(
            DocumentSpec.model_validate({"kind": "document", "title": "Before", "blocks": []})
        )
    finally:
        conn.close()
    store = JobStore(lambda: connect(migrated_dsn))
    store.create(
        kind="artifact.run",
        scope_key=workspace,
        idempotency_key="revise-run",
        payload={"kind": "document", "inputs": {}},
    )
    claimed = store.claim("revision-worker")
    assert claimed is not None and claimed.lease_token is not None
    staged = StagedRunResult(
        (
            StagedArtifact(
                kind="document",
                operation="revise",
                artifact_id=original["artifact_id"],
                expected_revision_id=original["revision_id"],
                draft_id="run-revision",
                spec={"kind": "document", "title": "After", "blocks": []},
            ),
        )
    )
    executor = RunExecutor({"document": lambda *_: staged}, data_dir=tmp_path)
    completed = store._finish_success(
        claimed, claimed.lease_token, staged, publisher=executor.publish_success
    )
    assert completed.status is JobStatus.SUCCEEDED
    assert completed.result["artifactIds"] == [str(original["artifact_id"])]
    conn = connect(migrated_dsn)
    try:
        row = ArtifactStore(conn, workspace_key=workspace, data_dir=tmp_path).get(
            original["artifact_id"]
        )
        assert row["spec"]["title"] == "After"
        assert str(row["parent_revision_id"]) == str(original["revision_id"])
    finally:
        conn.close()


@pytest.mark.parametrize("exhaustion", ["deadline", "tokens"])
def test_operation_budget_is_rechecked_before_artifact_publish(
    migrated_dsn, db, tmp_path, exhaustion
):
    from app.db.connection import connect
    from app.operations.budget import OperationBudgetController, OperationLimits

    current = job()
    OperationBudgetController().ensure(
        db,
        current.job_id,
        current.scope_key,
        OperationLimits(
            active_budget_seconds=60, max_calls=2, total_token_budget=100,
            per_call_output_cap=10, per_call_timeout_seconds=10,
        ),
    )
    if exhaustion == "deadline":
        db.execute(
            "UPDATE operations SET started_at=clock_timestamp()-interval '2 minutes', "
            "deadline_at=clock_timestamp()-interval '1 minute' WHERE operation_id=%s",
            (current.job_id,),
        )
    else:
        db.execute(
            "UPDATE operations SET accounted_tokens=total_token_budget+1 WHERE operation_id=%s",
            (current.job_id,),
        )
    db.commit()
    staged = StagedRunResult((StagedArtifact(
        kind="document",
        spec={"kind": "document", "title": "Budgeted", "blocks": []},
        operation_id=current.job_id,
    ),))
    executor = _executor(tmp_path)
    with pytest.raises(SafeJobError, match="time or token budget"), db.transaction():
        executor.publish_success(db, current, staged)

    conn = connect(migrated_dsn)
    try:
        assert conn.execute(
            "SELECT count(*) AS n FROM artifacts WHERE workspace_key = %s",
            (current.scope_key,),
        ).fetchone()["n"] == 0
    finally:
        conn.close()


def test_operation_deadline_expiring_during_publish_rolls_back_artifact(
    migrated_dsn, db, tmp_path, monkeypatch
):
    from app.artifacts.storage import ArtifactStore
    from app.db.connection import connect
    from app.operations.budget import OperationBudgetController, OperationLimits

    current = job()
    OperationBudgetController().ensure(
        db,
        current.job_id,
        current.scope_key,
        OperationLimits(
            active_budget_seconds=60, max_calls=2, total_token_budget=100,
            per_call_output_cap=10, per_call_timeout_seconds=10,
        ),
    )
    db.execute(
        "UPDATE operations SET started_at=clock_timestamp()-interval '1 second', "
        "deadline_at=clock_timestamp()+interval '1 second' WHERE operation_id=%s",
        (current.job_id,),
    )
    db.commit()
    original = ArtifactStore.create_uncommitted

    def delayed_create(store, spec):
        time.sleep(1.1)
        return original(store, spec)

    monkeypatch.setattr(ArtifactStore, "create_uncommitted", delayed_create)
    staged = StagedRunResult((StagedArtifact(
        kind="document",
        spec={"kind": "document", "title": "Late", "blocks": []},
        operation_id=current.job_id,
    ),))
    with pytest.raises(SafeJobError, match="time or token budget"), db.transaction():
        _executor(tmp_path).publish_success(db, current, staged)

    conn = connect(migrated_dsn)
    try:
        assert conn.execute(
            "SELECT count(*) AS n FROM artifacts WHERE workspace_key = %s",
            (current.scope_key,),
        ).fetchone()["n"] == 0
    finally:
        conn.close()
