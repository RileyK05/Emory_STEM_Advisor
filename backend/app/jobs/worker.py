"""Synchronous worker runner. The injected executor always runs outside transactions."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from threading import Event, Lock, Thread
from typing import Any

from app.jobs.store import JobCancelled, JobStore, LeaseLost, SafeJobError
from app.jobs.types import Job, JobEvent, JobStatus


class JobContext:
    """Worker callbacks use short independent transactions for progress and leases."""

    def __init__(self, store: JobStore, job: Job, lease_seconds: int):
        assert job.lease_token is not None
        self._store = store
        self.job_id = job.job_id
        self.lease_token = job.lease_token
        self.lease_seconds = lease_seconds
        self._stop_heartbeats = Event()
        self._failure_lock = Lock()
        self._heartbeat_failure: JobCancelled | LeaseLost | None = None
        self._heartbeat_thread: Thread | None = None

    def start_heartbeats(self) -> None:
        interval = max(0.1, self.lease_seconds / 3)

        def keep_lease_alive() -> None:
            while not self._stop_heartbeats.wait(interval):
                try:
                    self.heartbeat()
                except (JobCancelled, LeaseLost) as exc:
                    with self._failure_lock:
                        self._heartbeat_failure = exc
                    self._stop_heartbeats.set()
                    return
                except Exception:  # noqa: BLE001 - loss of renewal means lease cannot be proven
                    # A failed renewal means ownership cannot be proven. The worker
                    # may finish its external call, but it must not publish a result.
                    with self._failure_lock:
                        self._heartbeat_failure = LeaseLost(str(self.job_id))
                    self._stop_heartbeats.set()
                    return

        self._heartbeat_thread = Thread(target=keep_lease_alive, daemon=True)
        self._heartbeat_thread.start()

    def stop_heartbeats(self) -> None:
        self._stop_heartbeats.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=min(5.0, max(0.1, self.lease_seconds)))

    def progress(self, payload: Mapping[str, Any]) -> JobEvent:
        return self._store.progress(self.job_id, self.lease_token, payload)

    def heartbeat(self) -> Job:
        return self._store.heartbeat(
            self.job_id, self.lease_token, lease_seconds=self.lease_seconds
        )

    def check_cancelled(self) -> None:
        """Raise JobCancelled when cancellation was requested; LeaseLost if fenced out."""
        with self._failure_lock:
            if self._heartbeat_failure is not None:
                raise self._heartbeat_failure
        self._store.check_cancelled(self.job_id, self.lease_token)


def run_once(
    store: JobStore,
    worker_id: str,
    executor: Callable[[Job, JobContext], Any],
    *,
    lease_seconds: int = 60,
) -> Job | None:
    """Claim one job, execute without a DB transaction, then commit its outcome."""
    job = store.claim(worker_id, lease_seconds=lease_seconds)
    if job is None:
        return None
    assert job.lease_token is not None
    context = JobContext(store, job, lease_seconds)
    context.start_heartbeats()

    def finish_if_cancelled() -> Job:
        try:
            context.check_cancelled()
        except JobCancelled:
            pass
        return store._finish_cancelled(job, job.lease_token)

    try:
        try:
            result = executor(job, context)
        except JobCancelled:
            context.stop_heartbeats()
            return finish_if_cancelled()
        except Exception as exc:  # noqa: BLE001 - executor boundary records arbitrary failures
            context.stop_heartbeats()
            try:
                context.check_cancelled()
            except JobCancelled:
                return store._finish_cancelled(job, job.lease_token)
            if isinstance(exc, SafeJobError):
                error = {"type": exc.code, "message": exc.safe_message}
            else:
                error = {"type": "execution_failed", "message": "Job execution failed."}
            finished = store._finish_failure(
                job, job.lease_token, error,
                retryable=not isinstance(exc, SafeJobError) or exc.retryable,
            )
            if finished.status is JobStatus.FAILED:
                failure_hook = getattr(executor, "on_failure", None)
                if callable(failure_hook):
                    with store._transaction() as conn:
                        failure_hook(conn, job, exc)
            return finished
        context.stop_heartbeats()
        try:
            context.check_cancelled()
            # Publication can wait on row locks; refresh the lease immediately
            # before entering the finish transaction.
            context.heartbeat()
        except JobCancelled:
            return store._finish_cancelled(job, job.lease_token)
        publisher = getattr(executor, "publish_success", None)
        try:
            if callable(publisher):
                return store._finish_success(job, job.lease_token, result, publisher=publisher)
            return store._finish_success(job, job.lease_token, result)
        except JobCancelled:
            return store._finish_cancelled(job, job.lease_token)
        except LeaseLost:
            raise
        except SafeJobError as exc:
            return store._finish_failure(
                job,
                job.lease_token,
                {"type": exc.code, "message": exc.safe_message},
                retryable=exc.retryable,
            )
        except Exception:  # noqa: BLE001 - publication boundary must redact arbitrary failures
            return store._finish_failure(
                job,
                job.lease_token,
                {"type": "execution_failed", "message": "Job execution failed."},
            )
    finally:
        context.stop_heartbeats()
