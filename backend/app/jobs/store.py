"""Durable Postgres-backed job queue primitives."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.db import queries
from app.db.connection import connection_transaction
from app.jobs.types import Job, JobEvent, JobStatus

MAX_PAYLOAD_BYTES = 64 * 1024
MAX_RESULT_BYTES = 256 * 1024
MAX_ERROR_BYTES = 8 * 1024
MAX_PROGRESS_BYTES = 16 * 1024


def utcnow() -> datetime:
    return datetime.now(UTC)


def _bounded_json(value: Any, limit: int, name: str) -> Any:
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be JSON serializable") from exc
    size = len(encoded.encode("utf-8"))
    if size > limit:
        raise ValueError(f"{name} exceeds {limit} bytes ({size})")
    return value


def _timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("job clock must return timezone-aware datetimes")
    return value.astimezone(UTC)


class JobError(RuntimeError):
    """Base class for job-state errors."""


class JobNotFound(JobError):
    """The requested job does not exist."""


class LeaseLost(JobError):
    """The caller's worker lease is stale or expired."""


class JobCancelled(JobError):
    """Cooperative signal raised when a running job was cancelled."""


class SafeJobError(JobError):
    """An application-defined failure safe to show in job status/events."""

    def __init__(self, code: str, safe_message: str):
        if not code or len(code) > 80 or not safe_message or len(safe_message) > 500:
            raise ValueError("safe job error code/message has invalid length")
        self.code = code
        self.safe_message = safe_message
        super().__init__(safe_message)


class JobStore:
    """Job operations over a connection factory; transactions never span calls."""

    def __init__(
        self,
        connect: Callable[[], Connection],
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._connect = connect
        self._clock = clock

    def _transaction(self):
        return connection_transaction(self._connect)

    def create(
        self,
        *,
        kind: str,
        scope_key: str,
        idempotency_key: str,
        payload: Mapping[str, Any],
        max_attempts: int = 3,
    ) -> Job:
        if not kind or len(kind) > 100:
            raise ValueError("kind must contain 1 to 100 characters")
        if not scope_key or len(scope_key) > 200:
            raise ValueError("scope_key must contain 1 to 200 characters")
        if not idempotency_key or len(idempotency_key) > 200:
            raise ValueError("idempotency_key must contain 1 to 200 characters")
        if not 1 <= max_attempts <= 20:
            raise ValueError("max_attempts must be between 1 and 20")
        _bounded_json(payload, MAX_PAYLOAD_BYTES, "payload")
        now = _timestamp(self._clock())
        args = {
            "job_id": uuid4(),
            "kind": kind,
            "scope_key": scope_key,
            "idempotency_key": idempotency_key,
            "payload": Jsonb(dict(payload)),
            "max_attempts": max_attempts,
            "available_at": now,
        }
        with self._transaction() as conn:
            row = conn.execute(queries.get("insert_job"), args).fetchone()
            created = row is not None
            if row is None:
                row = conn.execute(
                    queries.get("get_job_by_idempotency"),
                    {"scope_key": scope_key, "idempotency_key": idempotency_key},
                ).fetchone()
            if row is None:
                raise RuntimeError("job idempotency conflict did not resolve to a job")
            job = Job.from_row(row)
            if created:
                self._append_event(conn, job.job_id, "queued", {"status": "queued"}, now)
            return job

    def get(self, job_id: UUID) -> Job:
        with self._transaction() as conn:
            row = conn.execute(queries.get("get_job"), {"job_id": job_id}).fetchone()
            if row is None:
                raise JobNotFound(str(job_id))
            return Job.from_row(row)

    def claim(self, worker_id: str, *, lease_seconds: int = 60) -> Job | None:
        if not worker_id or len(worker_id) > 200:
            raise ValueError("worker_id must contain 1 to 200 characters")
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 1 and 3600")
        now = _timestamp(self._clock())
        token = uuid4()
        with self._transaction() as conn:
            row = conn.execute(
                queries.get("claim_next_job"),
                {
                    "now": now,
                    "worker_id": worker_id,
                    "lease_token": token,
                    "lease_expires_at": now + timedelta(seconds=lease_seconds),
                },
            ).fetchone()
            if row is None:
                return None
            job = Job.from_row(row)
            self._append_event(
                conn,
                job.job_id,
                "claimed",
                {"attempt": job.attempt_count, "worker_id": worker_id},
                now,
            )
            return job

    def heartbeat(self, job_id: UUID, lease_token: UUID, *, lease_seconds: int = 60) -> Job:
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 1 and 3600")
        now = _timestamp(self._clock())
        with self._transaction() as conn:
            row = conn.execute(
                queries.get("heartbeat_job"),
                {
                    "job_id": job_id,
                    "lease_token": lease_token,
                    "now": now,
                    "lease_expires_at": now + timedelta(seconds=lease_seconds),
                },
            ).fetchone()
            if row is None:
                self._raise_lease_or_cancelled(conn, job_id, lease_token)
            return Job.from_row(row)

    def check_cancelled(self, job_id: UUID, lease_token: UUID) -> None:
        with self._transaction() as conn:
            self._check_lease(conn, job_id, lease_token)

    def progress(self, job_id: UUID, lease_token: UUID, payload: Mapping[str, Any]) -> JobEvent:
        _bounded_json(payload, MAX_PROGRESS_BYTES, "progress")
        now = _timestamp(self._clock())
        with self._transaction() as conn:
            row = conn.execute(
                queries.get("append_job_progress"),
                {
                    "job_id": job_id,
                    "lease_token": lease_token,
                    "payload": Jsonb(dict(payload)),
                    "now": now,
                },
            ).fetchone()
            if row is None:
                self._raise_lease_or_cancelled(conn, job_id, lease_token)
            return JobEvent.from_row(row)

    def cancel(self, job_id: UUID) -> Job:
        now = _timestamp(self._clock())
        with self._transaction() as conn:
            row = conn.execute(
                queries.get("request_job_cancel"), {"job_id": job_id, "now": now}
            ).fetchone()
            if row is None:
                existing = conn.execute(queries.get("get_job"), {"job_id": job_id}).fetchone()
                if existing is None:
                    raise JobNotFound(str(job_id))
                return Job.from_row(existing)
            job = Job.from_row(row)
            event = "cancelled" if job.status is JobStatus.CANCELLED else "cancel_requested"
            self._append_event(conn, job_id, event, {"status": job.status.value}, now)
            return job

    def recover_expired(self, *, limit: int = 100) -> int:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        now = _timestamp(self._clock())
        with self._transaction() as conn:
            rows = conn.execute(
                queries.get("recover_expired_jobs"), {"now": now, "limit": limit}
            ).fetchall()
            return len(rows)

    def events(self, job_id: UUID, *, after_event_id: int = 0, limit: int = 100) -> list[JobEvent]:
        if after_event_id < 0:
            raise ValueError("after_event_id must be non-negative")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self._transaction() as conn:
            if conn.execute(queries.get("get_job"), {"job_id": job_id}).fetchone() is None:
                raise JobNotFound(str(job_id))
            rows = conn.execute(
                queries.get("list_job_events"),
                {"job_id": job_id, "after_event_id": after_event_id, "limit": limit},
            ).fetchall()
            return [JobEvent.from_row(row) for row in rows]

    def _finish_success(
        self,
        job: Job,
        lease_token: UUID,
        result: Any,
        *,
        publisher: Callable[[Connection, Job, Any], Any] | None = None,
    ) -> Job:
        with self._transaction() as conn:
            state = conn.execute(
                queries.get("lock_job_for_success_publish"), {"job_id": job.job_id}
            ).fetchone()
            now = _timestamp(self._clock())
            if state is None:
                raise JobNotFound(str(job.job_id))
            if state["status"] != JobStatus.RUNNING.value or state["lease_token"] != lease_token:
                self._raise_lease_or_cancelled(conn, job.job_id, lease_token)
            if state["lease_expires_at"] <= now:
                raise LeaseLost(str(job.job_id))
            if state["cancel_requested"]:
                return self._record_cancelled(conn, job.job_id, lease_token, now)

            final_result = publisher(conn, job, result) if publisher is not None else result
            _bounded_json(final_result, MAX_RESULT_BYTES, "result")
            # Publication may wait on a profile fence; ownership must still be live
            # immediately before the success update, or this transaction rolls back.
            now = _timestamp(self._clock())
            if state["lease_expires_at"] <= now:
                raise LeaseLost(str(job.job_id))
            row = conn.execute(
                queries.get("finish_job_success"),
                {
                    "job_id": job.job_id,
                    "lease_token": lease_token,
                    "result": Jsonb(final_result),
                    "now": now,
                },
            ).fetchone()
            if row is None:
                self._raise_lease_or_cancelled(conn, job.job_id, lease_token)
            finished = Job.from_row(row)
            event_payload = {"status": finished.status.value}
            if isinstance(final_result, dict):
                for key in ("artifactIds", "artifactRevisions"):
                    if isinstance(final_result.get(key), list):
                        event_payload[key] = final_result[key]
            event = "cancelled" if finished.status is JobStatus.CANCELLED else "succeeded"
            self._append_event(conn, job.job_id, event, event_payload, now)
            return finished

    def _finish_cancelled(self, job: Job, lease_token: UUID) -> Job:
        now = _timestamp(self._clock())
        with self._transaction() as conn:
            return self._record_cancelled(conn, job.job_id, lease_token, now)

    def _record_cancelled(self, conn, job_id: UUID, lease_token: UUID, now) -> Job:
        row = conn.execute(
            queries.get("finish_job_cancelled"),
            {"job_id": job_id, "lease_token": lease_token, "now": now},
        ).fetchone()
        if row is None:
            self._raise_lease_or_cancelled(conn, job_id, lease_token)
        self._append_event(conn, job_id, "cancelled", {"status": "cancelled"}, now)
        return Job.from_row(row)

    def _finish_failure(self, job: Job, lease_token: UUID, error: Any) -> Job:
        safe_error = _bounded_json(error, MAX_ERROR_BYTES, "error")
        now = _timestamp(self._clock())
        retry = job.attempt_count < job.max_attempts
        retry_at = now + timedelta(seconds=retry_delay(job.attempt_count))
        with self._transaction() as conn:
            row = conn.execute(
                queries.get("finish_job_failure"),
                {
                    "job_id": job.job_id,
                    "lease_token": lease_token,
                    "error": Jsonb(safe_error),
                    "retry": retry,
                    "retry_at": retry_at,
                    "now": now,
                },
            ).fetchone()
            if row is None:
                self._raise_lease_or_cancelled(conn, job.job_id, lease_token)
            finished = Job.from_row(row)
            event = (
                "retry_scheduled" if finished.status is JobStatus.QUEUED else finished.status.value
            )
            self._append_event(
                conn,
                job.job_id,
                event,
                {
                    "status": finished.status.value,
                    "attempt": job.attempt_count,
                    "error": finished.error,
                },
                now,
            )
            return finished

    @staticmethod
    def _append_event(conn, job_id: UUID, event_type: str, payload: Mapping[str, Any], now):
        return conn.execute(
            queries.get("append_job_event"),
            {
                "job_id": job_id,
                "event_type": event_type,
                "payload": Jsonb(dict(payload)),
                "now": now,
            },
        ).fetchone()

    def _check_lease(self, conn, job_id: UUID, lease_token: UUID) -> None:
        """Return only if the caller still holds a live lease and nobody asked to cancel."""
        row = conn.execute(queries.get("job_cancel_state"), {"job_id": job_id}).fetchone()
        if row is None:
            raise JobNotFound(str(job_id))
        if row["status"] != "running" or row["lease_token"] != lease_token:
            raise LeaseLost(str(job_id))
        if row["lease_expires_at"] <= _timestamp(self._clock()):
            raise LeaseLost(str(job_id))
        if row["cancel_requested"]:
            raise JobCancelled(str(job_id))

    def _raise_lease_or_cancelled(self, conn, job_id: UUID, lease_token: UUID) -> None:
        """A guarded update matched no row: say why, defaulting to a lost lease."""
        self._check_lease(conn, job_id, lease_token)
        raise LeaseLost(str(job_id))


def retry_delay(attempt: int, *, base_seconds: float = 1.0, cap_seconds: float = 300.0) -> float:
    """Deterministic capped exponential backoff after a failed attempt."""
    if attempt < 1 or base_seconds <= 0 or cap_seconds <= 0:
        raise ValueError("attempt and retry delay settings must be positive")
    return min(cap_seconds, base_seconds * (2 ** (attempt - 1)))
