"""Small immutable job and event values shared by workers and API layers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Job:
    job_id: UUID
    kind: str
    scope_key: str
    idempotency_key: str
    payload: dict[str, Any]
    result: Any
    error: Any
    status: JobStatus
    attempt_count: int
    max_attempts: int
    available_at: datetime
    lease_owner: str | None
    lease_token: UUID | None
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    cancel_requested: bool
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Job:
        values = dict(row)
        values["status"] = JobStatus(values["status"])
        return cls(**values)


@dataclass(frozen=True)
class JobEvent:
    event_id: int
    job_id: UUID
    event_type: str
    payload: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> JobEvent:
        return cls(**dict(row))
