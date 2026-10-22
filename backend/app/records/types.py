"""Bounded domain types for academic records, course attempts, and verifications."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

AttemptStatus = Literal[
    "completed",
    "in_progress",
    "planned",
    "withdrawn",
    "failed",
    "repeated",
    "transfer",
    "test_credit",
]

AttemptProvenance = Literal[
    "student_manual",
    "csv_import",
    "transcript_upload",
    "institutional_sync",
]

VerificationMethod = Literal[
    "manual_staff",
    "official_transcript",
    "sis_feed",
]

ImportBatchStatus = Literal[
    "preview",
    "committed",
    "rejected",
    "failed",
]

VerificationEventType = Literal[
    "attempt_created",
    "attempt_updated",
    "attempt_verified",
    "verification_invalidated",
    "attempt_deleted",
    "batch_committed",
]


@dataclass(frozen=True)
class AcademicRecord:
    record_id: UUID
    workspace_key: str
    record_revision: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class CourseAttempt:
    attempt_id: UUID
    record_id: UUID
    workspace_key: str
    institution: str
    submitted_course_code: str
    resolved_course_code: str | None
    course_title: str | None
    term: str
    attempted_credits: float
    earned_credits: float
    grade: str | None
    status: AttemptStatus
    provenance: AttemptProvenance
    is_verified: bool
    verification_method: VerificationMethod | None
    verified_by_user_id: UUID | None
    verified_at: datetime | None
    verification_notes: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ImportBatch:
    batch_id: UUID
    record_id: UUID
    workspace_key: str
    expected_record_revision: int
    status: ImportBatchStatus
    filename: str
    raw_content: str
    parsed_rows: list[dict[str, Any]]
    row_errors: list[dict[str, Any]]
    duplicate_candidates: list[dict[str, Any]]
    created_at: datetime
    committed_at: datetime | None


@dataclass(frozen=True)
class VerificationEvent:
    event_id: UUID
    record_id: UUID
    attempt_id: UUID | None
    event_type: VerificationEventType
    actor_user_id: UUID | None
    actor_role: str
    details: dict[str, Any]
    created_at: datetime

