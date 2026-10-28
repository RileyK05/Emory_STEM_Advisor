"""Synchronous worker runner. The injected executor always runs outside transactions."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from threading import Event, Lock, Thread
from typing import Any

from app.jobs.store import JobCancelled, JobStore, LeaseLost, SafeJobError
from app.jobs.types import Job, JobEvent, JobStatus

logger = logging.getLogger(__name__)


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

    def invoke_hook(name: str, exc: Exception) -> None:
        """Run an executor settlement hook without letting it wedge the worker."""
        hook = getattr(executor, name, None)
        if not callable(hook):
            return
        try:
            with store._transaction() as conn:
                hook(conn, job, exc)
        except Exception:
            logger.exception("executor %s hook failed for job %s", name, job.job_id)

    def finish_cancelled(exc: Exception) -> Job:
        try:
            context.check_cancelled()
        except JobCancelled:
            pass
        finished = store._finish_cancelled(job, job.lease_token)
        invoke_hook("on_cancelled", exc)
        return finished

    def finish_failed(exc: Exception, *, stage: str, retryable: bool) -> Job:
        if isinstance(exc, SafeJobError):
            error = {"type": exc.code, "message": exc.safe_message}
        else:
            error = {"type": "execution_failed", "message": "Job execution failed."}
        logger.warning("job %s (%s) failed during %s: %s",
                       job.job_id, job.kind, stage, type(exc).__name__)
        finished = store._finish_failure(
            job, job.lease_token, error,
            retryable=retryable,
        )
        if finished.status is JobStatus.FAILED:
            invoke_hook("on_failure", exc)
        return finished

    try:
        try:
            result = executor(job, context)
        except JobCancelled as exc:
            context.stop_heartbeats()
            return finish_cancelled(exc)
        except Exception as exc:  # noqa: BLE001 - executor boundary records arbitrary failures
            context.stop_heartbeats()
            try:
                context.check_cancelled()
            except JobCancelled as cancelled:
                return finish_cancelled(cancelled)
            return finish_failed(
                exc, stage="execution",
                retryable=not isinstance(exc, SafeJobError) or exc.retryable,
            )
        context.stop_heartbeats()
        try:
            context.check_cancelled()
            # Publication can wait on row locks; refresh the lease immediately
            # before entering the finish transaction.
            context.heartbeat()
        except JobCancelled as exc:
            return finish_cancelled(exc)
        publisher = getattr(executor, "publish_success", None)
        try:
            if callable(publisher):
                finished = store._finish_success(
                    job, job.lease_token, result, publisher=publisher)
            else:
                finished = store._finish_success(job, job.lease_token, result)
            if finished.status is JobStatus.CANCELLED:
                invoke_hook("on_cancelled", JobCancelled(str(job.job_id)))
            return finished
        except JobCancelled as exc:
            return finish_cancelled(exc)
        except LeaseLost:
            raise
        except SafeJobError as exc:
            return finish_failed(exc, stage="publication", retryable=exc.retryable)
        except Exception as exc:  # noqa: BLE001 - publication boundary redacts failures
            return finish_failed(exc, stage="publication", retryable=True)
    finally:
        context.stop_heartbeats()
