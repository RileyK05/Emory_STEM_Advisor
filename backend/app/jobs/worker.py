"""Synchronous worker runner. The injected executor always runs outside transactions."""

from __future__ import annotations

import json
import logging
import signal
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Condition, Event, Lock, Thread
from typing import Any, Self
from urllib.parse import urlsplit

from app.jobs.store import JobCancelled, JobStore, LeaseLost, SafeJobError
from app.jobs.types import Job, JobEvent, JobStatus
from app.ops.readiness import ReadinessResult

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

    def may_retry(exc: Exception, default: bool) -> bool:
        # A kind that names its retryable failures retries nothing else.
        codes = getattr(executor, "retryable_codes", None)
        if callable(codes):
            codes = codes(job)
        if codes is None:
            return default
        return isinstance(exc, SafeJobError) and exc.retryable and exc.code in codes

    def finish_failed(exc: Exception, *, stage: str, retryable: bool) -> Job:
        if isinstance(exc, SafeJobError):
            error = {"type": exc.code, "message": exc.safe_message}
        else:
            error = {"type": "execution_failed", "message": "Job execution failed."}
        # Server-written text only: a SafeJobError's message or an HTTP error's
        # detail. Other exceptions can carry model output, so only their type.
        detail = (f"{exc.code}: {exc.safe_message}" if isinstance(exc, SafeJobError)
                  else f"HTTP {exc.status_code}: {exc.detail}"
                  if isinstance(getattr(exc, "status_code", None), int)
                  and isinstance(getattr(exc, "detail", None), str) else "")
        logger.warning("job %s (%s) failed during %s: %s %s",
                       job.job_id, job.kind, stage, type(exc).__name__, detail)
        finished = store._finish_failure(
            job, job.lease_token, error,
            retryable=retryable, retry_after=getattr(exc, "retry_after", None),
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
                retryable=may_retry(exc, not isinstance(exc, SafeJobError) or exc.retryable),
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
            return finish_failed(exc, stage="publication", retryable=may_retry(exc, exc.retryable))
        except Exception as exc:  # noqa: BLE001 - publication boundary redacts failures
            return finish_failed(exc, stage="publication", retryable=may_retry(exc, True))
    finally:
        context.stop_heartbeats()


def install_stop_signals(on_stop: Callable[[], None]) -> Callable[[], None]:
    """Register SIGTERM, SIGINT, and Windows SIGBREAK to trigger graceful drain."""
    if threading.current_thread() is not threading.main_thread():
        return lambda: None
    signals: list[signal.Signals] = [signal.SIGTERM, signal.SIGINT]
    sigbreak = getattr(signal, "SIGBREAK", None)
    if sigbreak is not None:
        signals.append(sigbreak)
    previous: list[tuple[signal.Signals, Any]] = []

    def _handle(_signum: int, _frame: Any) -> None:
        on_stop()

    for sig in signals:
        previous.append((sig, signal.getsignal(sig)))
        signal.signal(sig, _handle)

    def restore() -> None:
        for sig, handler in previous:
            signal.signal(sig, handler)

    return restore


class _ReuseThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class WorkerHealthServer:
    """Small HTTP probe server reporting worker liveness (/health) and readiness (/ready)."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        accepting: Callable[[], bool],
        stopping: Callable[[], bool] | None = None,
        readiness_check: Callable[[], ReadinessResult] | None = None,
    ) -> None:
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self._respond(include_body=True)

            def do_HEAD(self) -> None:
                self._respond(include_body=False)

            def do_POST(self) -> None:
                self._send_json(405, {"detail": "Method Not Allowed"}, include_body=True)

            def _respond(self, *, include_body: bool) -> None:
                path = urlsplit(self.path).path
                if path == "/health":
                    self._send_json(200, {"status": "ok"}, include_body=include_body)
                    return
                if path == "/ready":
                    status_code, payload = outer._evaluate_ready()
                    self._send_json(status_code, payload, include_body=include_body)
                    return
                self._send_json(404, {"detail": "Not Found"}, include_body=include_body)

            def _send_json(
                self, status_code: int, payload: Mapping[str, Any], *, include_body: bool
            ) -> None:
                encoded = json.dumps(payload).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if include_body:
                    self.wfile.write(encoded)

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        self._accepting = accepting
        self._stopping = stopping
        self._readiness_check = readiness_check
        self._server = _ReuseThreadingHTTPServer((host, port), _Handler)
        self.port: int = int(self._server.server_address[1])
        self._thread = Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.1},
            daemon=True,
            name="advisor-worker-health",
        )
        self._closed = False

    def _evaluate_ready(self) -> tuple[int, Mapping[str, Any]]:
        if self._stopping is not None and self._stopping():
            return 503, {"status": "unavailable", "reasons": ["draining"]}
        if not self._accepting():
            return 503, {"status": "unavailable", "reasons": ["starting"]}
        if self._readiness_check is not None:
            result = self._readiness_check()
            return (200 if result.ready else 503), result.as_dict()
        return 200, {"status": "ready"}

    def start(self) -> Self:
        self._thread.start()
        return self

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2.0)

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.close()


class WorkerRunner:
    """Coordinate worker claim threads, maintenance, and graceful SIGTERM drain."""

    def __init__(
        self,
        store: JobStore,
        worker_id: str,
        executor: Callable[[Job, JobContext], Any],
        *,
        lease_seconds: int = 60,
        idle_seconds: float = 1.0,
        concurrency: int = 1,
        maintenance: Callable[[], None] | None = None,
        maintenance_interval_seconds: float = 5.0,
        on_error: Callable[[str, Exception], None] | None = None,
        clock: Any | None = None,
        max_idle_seconds: float | None = None,
        drain_seconds: float | None = None,
    ) -> None:
        self.store = store
        self.worker_id = worker_id
        self.executor = executor
        self.lease_seconds = lease_seconds
        # How long a stopping worker waits for in-flight jobs before requeueing
        # them. Heartbeats keep their leases alive meanwhile, so this may exceed
        # the lease. The platform's termination grace period must be longer.
        self.drain_seconds = float(drain_seconds if drain_seconds is not None else lease_seconds)
        self.idle_seconds = idle_seconds
        # An empty queue is polled less and less often, up to this cap; a job
        # notification (see `listen_for_jobs`) wakes every idle thread at once.
        self.max_idle_seconds = max(idle_seconds, max_idle_seconds or idle_seconds)
        self._wakes = 0
        self.concurrency = concurrency
        self._maintenance = maintenance
        self._maintenance_interval_seconds = maintenance_interval_seconds
        self._on_error = on_error
        self._clock = clock
        self._stop = Event()
        self._accepting = Event()
        self._started = Event()
        self._cond = Condition()
        self._threads: tuple[Thread, ...] = ()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    @property
    def accepting(self) -> bool:
        return self._accepting.is_set() and not self._stop.is_set()

    def wait_until_accepting(self, timeout: float = 5.0) -> bool:
        return self._started.wait(timeout)

    def join_threads(self, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + max(0.0, timeout)
        for thread in self._threads:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))

    def wake(self) -> None:
        """End every idle wait now: new work was queued."""
        with self._cond:
            self._wakes += 1
            self._cond.notify_all()

    def request_stop(self) -> None:
        """Stop claiming new jobs while allowing in-flight jobs to drain."""
        self._accepting.clear()
        self._stop.set()
        with self._cond:
            self._cond.notify_all()

    def _now(self) -> float:
        if callable(self._clock):
            return float(self._clock().timestamp())
        return time.monotonic()

    def _subscribe_clock(self) -> Callable[[], None]:
        subscribe = getattr(self._clock, "subscribe", None)
        if not callable(subscribe):
            return lambda: None

        def _wake() -> None:
            with self._cond:
                self._cond.notify_all()

        return subscribe(_wake)

    def _wait_stop_or_timeout(self, seconds: float) -> bool:
        if self._stop.is_set():
            return True
        if seconds <= 0:
            return self._stop.is_set()
        has_fake_clock = callable(getattr(self._clock, "subscribe", None))
        unsubscribe = self._subscribe_clock()
        try:
            with self._cond:
                deadline = self._now() + seconds
                wakes = self._wakes
                while not self._stop.is_set():
                    if self._wakes != wakes:
                        return False
                    remaining = deadline - self._now()
                    if remaining <= 0:
                        return False
                    if has_fake_clock:
                        self._cond.wait()
                    else:
                        self._cond.wait(timeout=remaining)
                return True
        finally:
            unsubscribe()

    def _drain_threads(self, threads: list[Thread]) -> None:
        has_fake_clock = callable(getattr(self._clock, "subscribe", None))
        if has_fake_clock:
            unsubscribe = self._subscribe_clock()
            try:
                with self._cond:
                    deadline = self._now() + self.drain_seconds
                    while any(t.is_alive() for t in threads) and self._now() < deadline:
                        self._cond.wait()
            finally:
                unsubscribe()
            for thread in threads:
                if not thread.is_alive():
                    thread.join(timeout=1.0)
            return

        deadline = time.monotonic() + self.drain_seconds
        for thread in threads:
            remaining = max(0.0, deadline - time.monotonic())
            thread.join(timeout=remaining)

    def run(self) -> None:
        def work(index: int) -> None:
            thread_worker_id = (
                self.worker_id if self.concurrency == 1 else f"{self.worker_id}-{index}"
            )
            idle = self.idle_seconds
            try:
                while not self._stop.is_set():
                    try:
                        job = run_once(
                            self.store,
                            thread_worker_id,
                            self.executor,
                            lease_seconds=self.lease_seconds,
                        )
                    except LeaseLost:
                        job = None
                    except Exception as exc:  # noqa: BLE001 - one thread must not stop the queue
                        if self._on_error is not None:
                            self._on_error(thread_worker_id, exc)
                        self._wait_stop_or_timeout(max(0.5, self.idle_seconds))
                        continue
                    if job is not None:
                        idle = self.idle_seconds
                    elif not self._stop.is_set():
                        woken = self._wakes
                        self._wait_stop_or_timeout(idle)
                        # Back off only while nothing arrives; a wake resets it.
                        idle = (self.idle_seconds if self._wakes != woken
                                else min(self.max_idle_seconds, idle * 2))
            finally:
                with self._cond:
                    self._cond.notify_all()

        threads = [
            Thread(target=work, args=(index,), daemon=True, name=f"advisor-worker-{index}")
            for index in range(self.concurrency)
        ]
        self._threads = tuple(threads)
        if not self._stop.is_set():
            self._accepting.set()
        self._started.set()
        for thread in threads:
            thread.start()
        try:
            while not self._stop.is_set():
                if self._maintenance is not None:
                    self._maintenance()
                self._wait_stop_or_timeout(self._maintenance_interval_seconds)
        except KeyboardInterrupt:
            self.request_stop()
        finally:
            self.request_stop()
            self._drain_threads(threads)
            if any(thread.is_alive() for thread in threads):
                # The drain ran out: requeue now rather than when the leases expire.
                owners = [self.worker_id] if self.concurrency == 1 else [
                    f"{self.worker_id}-{index}" for index in range(self.concurrency)]
                try:
                    self.store.release_leases(owners)
                except Exception as exc:  # noqa: BLE001 - recovery still requeues them later
                    if self._on_error is not None:
                        self._on_error(self.worker_id, exc)


JOB_CHANNEL = "advisor_jobs"


def listen_for_jobs(connect: Callable[[], Any], runner: WorkerRunner, *,
                    timeout_seconds: float = 5.0, retry_seconds: float = 5.0,
                    on_listening: Callable[[], None] | None = None) -> None:
    """Wake the runner whenever a job is queued; returns when the runner stops.

    Polling stays the safety net (a retry that becomes due, a missed
    notification), so a lost listener only makes pickup slower, never wrong.
    """
    while not runner.stopping:
        conn = None
        try:
            conn = connect()
            conn.autocommit = True
            conn.execute(f"LISTEN {JOB_CHANNEL}")
            if on_listening is not None:
                on_listening()
            while not runner.stopping:
                for _notify in conn.notifies(timeout=timeout_seconds, stop_after=1):
                    runner.wake()
        except Exception:
            # The listener is an optimisation; polling keeps the queue moving.
            logger.warning("job listener lost its connection; retrying", exc_info=True)
            runner._wait_stop_or_timeout(retry_seconds)
        finally:
            if conn is not None:
                with suppress(Exception):
                    conn.close()
