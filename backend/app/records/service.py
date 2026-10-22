"""Service layer for academic records, course attempts, imports, and verifications."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException

from app.auth.capabilities import Capability, check_workspace_access, require_capability
from app.auth.models import Principal
from app.db import queries
from app.privacy.fence import require_live_workspace
from app.records.importer import (
    infer_status_and_credits,
    normalize_course_code,
    normalize_term,
    parse_csv_content,
)
from app.util.digest import json_digest
from app.util.json import loose_jsonb

logger = logging.getLogger(__name__)


def _attempt_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "attemptId": str(row["attempt_id"]),
        "submittedCourseCode": row["submitted_course_code"],
        "resolvedCourseCode": row.get("resolved_course_code"),
        "institution": row["institution"],
        "courseTitle": row.get("course_title"),
        "term": row["term"],
        "attemptedCredits": float(row["attempted_credits"]),
        "earnedCredits": float(row["earned_credits"]),
        "grade": row.get("grade"),
        "status": row["status"],
        "provenance": row["provenance"],
        "isVerified": bool(row["is_verified"]),
        "verificationMethod": row.get("verification_method"),
        "verifiedByUserId": str(row["verified_by_user_id"]) if row.get("verified_by_user_id") else None,
        "verifiedAt": row["verified_at"].isoformat() if row.get("verified_at") else None,
        "verificationNotes": row.get("verification_notes"),
    }


def _store_revision(conn, record_id) -> None:
    """Keep the attempts that belong to the record's current revision."""
    record = conn.execute(
        queries.get("get_academic_record_by_id"), {"record_id": record_id},
    ).fetchone()
    if record is None:
        return
    attempts = conn.execute(
        queries.get("get_academic_record_attempts"), {"record_id": record_id},
    ).fetchall()
    conn.execute(queries.get("insert_academic_record_revision"), {
        "record_id": record_id,
        "revision": record["record_revision"],
        "attempts": loose_jsonb([_attempt_snapshot(dict(row)) for row in attempts]),
    })


def _bump_revision(conn, record_id) -> None:
    """Advance the record revision and snapshot it."""
    conn.execute(queries.get("bump_record_revision"), {"record_id": record_id})
    _store_revision(conn, record_id)


def _record_event(conn, record_id, attempt_id, event_type: str, actor: Principal,
                  details: dict) -> None:
    conn.execute(queries.get("insert_academic_record_verification_event"), {
        "event_id": uuid4(), "record_id": record_id, "attempt_id": attempt_id,
        "event_type": event_type, "actor_user_id": actor.user_id, "actor_role": actor.role,
        "details": loose_jsonb(details),
    })


def _selected_import_rows(parsed_rows: list, decisions: list) -> list:
    selected = []
    for index, row in enumerate(parsed_rows):
        decision = decisions[index] if index < len(decisions) else "select"
        if decision == "select":
            selected.append(row)
    return selected


def _import_digest(parsed_rows: list, decisions: list) -> str:
    return json_digest(_selected_import_rows(parsed_rows, decisions))


class AcademicRecordService:
    @staticmethod
    def get_or_create_record(conn, workspace_key: str, *, lock: bool = False) -> dict[str, Any]:
        require_live_workspace(conn, workspace_key, lock=True)
        row = conn.execute(
            queries.get("lock_academic_record_by_workspace" if lock
                        else "get_academic_record_by_workspace"),
            {"workspace_key": workspace_key},
        ).fetchone()
        if row:
            return dict(row)

        record_id = uuid4()
        created = conn.execute(
            queries.get("get_or_create_academic_record"),
            {"record_id": record_id, "workspace_key": workspace_key},
        ).fetchone()
        _store_revision(conn, created["record_id"])
        return dict(created)

    @classmethod
    def get_record(
        cls, conn, workspace_key: str, actor: Principal, *, create: bool = False
    ) -> dict[str, Any]:
        if not check_workspace_access(
            conn, actor, workspace_key, "all_records", action="read"
        ):
            raise HTTPException(403, "access to academic record is denied")

        if create:
            record = cls.get_or_create_record(conn, workspace_key)
        else:
            record = conn.execute(
                queries.get("get_academic_record_by_workspace"),
                {"workspace_key": workspace_key},
            ).fetchone()
            if record is None:
                raise HTTPException(404, "academic record not found")
            record = dict(record)
        record_id = record["record_id"]

        attempts = conn.execute(
            queries.get("get_academic_record_attempts"),
            {"record_id": record_id},
        ).fetchall()

        events = conn.execute(
            queries.get("list_academic_record_verification_events"),
            {"record_id": record_id},
        ).fetchall()

        return {
            "record": record,
            "attempts": [dict(a) for a in attempts],
            "recentEvents": [dict(e) for e in events[:50]],
        }

    @classmethod
    def upsert_attempt(
        cls,
        conn,
        workspace_key: str,
        attempt_data: dict[str, Any],
        actor: Principal,
    ) -> dict[str, Any]:
        if not check_workspace_access(
            conn, actor, workspace_key, "all_records", action="write"
        ):
            raise HTTPException(403, "write access to academic record is denied")

        record = cls.get_or_create_record(conn, workspace_key, lock=True)
        record_id = record["record_id"]

        raw_code = attempt_data.get("submittedCourseCode")
        if not raw_code:
            raise HTTPException(422, "submitted course code is required")
        course_code = normalize_course_code(str(raw_code))

        raw_term = attempt_data.get("term")
        if not raw_term:
            raise HTTPException(422, "term is required")
        term = normalize_term(str(raw_term))
        if not course_code or not term:
            raise HTTPException(422, "course code and term must not be blank")

        attempted_credits = float(attempt_data.get("attemptedCredits", 3.0))
        if attempted_credits < 0.0 or attempted_credits > 30.0:
            raise HTTPException(422, "attempted credits out of reasonable range")

        explicit_earned = attempt_data.get("earnedCredits")
        grade = attempt_data.get("grade")
        try:
            status, earned_credits = infer_status_and_credits(
                grade=grade,
                attempted_credits=attempted_credits,
                explicit_status=attempt_data.get("status"),
                explicit_earned_credits=float(explicit_earned) if explicit_earned is not None else None,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

        fields = {
            "institution": attempt_data.get("institution") or "Emory University",
            "submitted_course_code": course_code,
            "resolved_course_code": normalize_course_code(
                attempt_data.get("resolvedCourseCode") or course_code),
            "course_title": attempt_data.get("courseTitle"),
            "term": term,
            "attempted_credits": attempted_credits,
            "earned_credits": earned_credits,
            "grade": grade.strip().upper() if grade else None,
            "status": status,
            "provenance": "student_manual",
        }
        summary = {"course_code": course_code, "term": term}

        if attempt_data.get("attemptId"):
            attempt_id = UUID(str(attempt_data["attemptId"]))
            existing = conn.execute(
                queries.get("get_academic_record_attempt_by_id"),
                {"attempt_id": attempt_id},
            ).fetchone()
            if not existing or existing["record_id"] != record_id:
                raise HTTPException(404, "attempt not found on this record")
            if existing["is_verified"]:
                # Editing a verified attempt withdraws its verification.
                conn.execute(queries.get("invalidate_attempt_verification"),
                             {"attempt_id": attempt_id})
                _record_event(conn, record_id, attempt_id, "verification_invalidated", actor, {
                    "reason": "attempt_edited",
                    "previous_grade": existing["grade"],
                    "previous_earned_credits": float(existing["earned_credits"]),
                })
            row = conn.execute(queries.get("update_academic_record_attempt"),
                               {"attempt_id": attempt_id, **fields}).fetchone()
            event_type = "attempt_updated"
        else:
            attempt_id = uuid4()
            row = conn.execute(queries.get("insert_academic_record_attempt"), {
                "attempt_id": attempt_id, "record_id": record_id,
                "workspace_key": workspace_key, **fields,
                "is_verified": False, "verification_method": None,
                "verified_by_user_id": None, "verified_at": None, "verification_notes": None,
            }).fetchone()
            event_type = "attempt_created"
        _record_event(conn, record_id, attempt_id, event_type, actor, summary)
        _bump_revision(conn, record_id)
        return dict(row)

    @classmethod
    def delete_attempt(
        cls,
        conn,
        workspace_key: str,
        attempt_id: UUID,
        actor: Principal,
    ) -> None:
        if not check_workspace_access(
            conn, actor, workspace_key, "all_records", action="write"
        ):
            raise HTTPException(403, "write access to academic record is denied")

        record = cls.get_or_create_record(conn, workspace_key, lock=True)
        record_id = record["record_id"]

        existing = conn.execute(
            queries.get("get_academic_record_attempt_by_id"),
            {"attempt_id": attempt_id},
        ).fetchone()
        if not existing or existing["record_id"] != record_id:
            raise HTTPException(404, "attempt not found on this record")

        conn.execute(
            queries.get("delete_academic_record_attempt"),
            {"attempt_id": attempt_id},
        )
        _record_event(conn, record_id, None, "attempt_deleted", actor, {
            "deleted_attempt_id": str(attempt_id),
            "course_code": existing["submitted_course_code"],
            "term": existing["term"],
        })
        _bump_revision(conn, record_id)

    @classmethod
    def create_import_preview(
        cls,
        conn,
        workspace_key: str,
        filename: str,
        raw_content: str,
        expected_revision: int,
        actor: Principal,
    ) -> dict[str, Any]:
        if not check_workspace_access(
            conn, actor, workspace_key, "all_records", action="write"
        ):
            raise HTTPException(403, "write access to academic record is denied")

        record = cls.get_or_create_record(conn, workspace_key, lock=True)
        record_id = record["record_id"]

        existing_attempts = conn.execute(
            queries.get("get_academic_record_attempts"),
            {"record_id": record_id},
        ).fetchall()

        parsed_rows, row_errors, duplicate_candidates = parse_csv_content(
            raw_content,
            [dict(a) for a in existing_attempts],
        )

        duplicate_rows = {item["rowIndex"] for item in duplicate_candidates}
        # Duplicates stay visible for review but are not imported by default:
        # silently selecting them would double-count repeated credit.
        decisions = [
            "reject" if row["rowIndex"] in duplicate_rows else "select"
            for row in parsed_rows
        ]
        batch_id = uuid4()
        batch = conn.execute(
            queries.get("insert_import_batch"),
            {
                "batch_id": batch_id,
                "record_id": record_id,
                "workspace_key": workspace_key,
                "expected_record_revision": expected_revision,
                "status": "preview",
                "filename": filename,
                "raw_content": raw_content,
                "parsed_rows": loose_jsonb(parsed_rows),
                "row_errors": loose_jsonb(row_errors),
                "duplicate_candidates": loose_jsonb(duplicate_candidates),
                "preview_digest": _import_digest(parsed_rows, decisions),
                "row_decisions": loose_jsonb(decisions),
            },
        ).fetchone()

        return dict(batch)

    @classmethod
    def commit_import_batch(
        cls,
        conn,
        workspace_key: str,
        batch_id: UUID,
        expected_revision: int,
        preview_digest: str,
        actor: Principal,
    ) -> dict[str, Any]:
        if not check_workspace_access(
            conn, actor, workspace_key, "all_records", action="write"
        ):
            raise HTTPException(403, "write access to academic record is denied")

        record = cls.get_or_create_record(conn, workspace_key, lock=True)
        record_id = record["record_id"]

        batch = conn.execute(
            queries.get("get_import_batch_by_id"),
            {"batch_id": batch_id},
        ).fetchone()
        if not batch or batch["record_id"] != record_id:
            raise HTTPException(404, "import batch not found")

        if batch["status"] == "committed":
            if preview_digest != batch["preview_digest"]:
                raise HTTPException(409, "import preview changed; review the saved rows")
            return {
                "batchId": batch_id,
                "status": "committed",
                "attemptsImported": batch["committed_count"],
                "newRecordRevision": record["record_revision"],
            }
        if batch["status"] != "preview":
            raise HTTPException(400, f"import batch is already {batch['status']}")
        if preview_digest != batch["preview_digest"]:
            raise HTTPException(409, "import preview changed; review the saved rows")

        # Optimistic concurrency check on record revision
        rev_res = conn.execute(
            queries.get("increment_record_revision"),
            {"record_id": record_id, "expected_revision": expected_revision},
        ).fetchone()
        if not rev_res:
            raise HTTPException(
                409,
                f"record revision conflict: expected revision {expected_revision} does not match current record",
            )

        committed_count = 0
        parsed_rows = list(batch["parsed_rows"] or [])
        decisions = list(batch["row_decisions"] or [])
        for row in _selected_import_rows(parsed_rows, decisions):
            attempt_id = uuid4()
            conn.execute(
                queries.get("insert_academic_record_attempt"),
                {
                    "attempt_id": attempt_id,
                    "record_id": record_id,
                    "workspace_key": workspace_key,
                    "institution": row.get("institution", "Emory University"),
                    "submitted_course_code": row["submittedCourseCode"],
                    "resolved_course_code": row.get("resolvedCourseCode", row["submittedCourseCode"]),
                    "course_title": row.get("courseTitle"),
                    "term": row["term"],
                    "attempted_credits": float(row.get("attemptedCredits", 3.0)),
                    "earned_credits": float(row.get("earnedCredits", 0.0)),
                    "grade": row.get("grade"),
                    "status": row.get("status", "completed"),
                    "provenance": "csv_import",
                    "is_verified": False,
                    "verification_method": None,
                    "verified_by_user_id": None,
                    "verified_at": None,
                    "verification_notes": None,
                },
            )
            committed_count += 1

        conn.execute(
            queries.get("update_import_batch_status"),
            {"batch_id": batch_id, "status": "committed", "committed_count": committed_count},
        )
        _store_revision(conn, record_id)
        _record_event(conn, record_id, None, "batch_committed", actor, {
            "batch_id": str(batch_id),
            "filename": batch["filename"],
            "attempts_imported": committed_count,
        })

        return {
            "batchId": batch_id,
            "status": "committed",
            "attemptsImported": committed_count,
            "newRecordRevision": rev_res["record_revision"],
        }

    @classmethod
    def update_import_preview(
        cls,
        conn,
        workspace_key: str,
        batch_id: UUID,
        expected_revision: int,
        decisions: list[str] | None,
        corrections: list[dict[str, Any]] | None,
        actor: Principal,
    ) -> dict[str, Any]:
        """Replace selected or corrected preview rows without touching a stale record."""
        if not check_workspace_access(
            conn, actor, workspace_key, "all_records", action="write"
        ):
            raise HTTPException(403, "write access to academic record is denied")
        record = cls.get_or_create_record(conn, workspace_key, lock=True)
        if record["record_revision"] != expected_revision:
            raise HTTPException(
                409,
                "record revision conflict: expected revision "
                f"{expected_revision} does not match current record",
            )
        batch = conn.execute(
            queries.get("get_import_batch_by_id"), {"batch_id": batch_id},
        ).fetchone()
        if not batch or batch["record_id"] != record["record_id"]:
            raise HTTPException(404, "import batch not found")
        if batch["status"] != "preview":
            raise HTTPException(409, f"import batch is already {batch['status']}")
        rows = [dict(row) for row in (batch["parsed_rows"] or [])]
        current = list(batch["row_decisions"] or [])
        if len(current) < len(rows):
            current.extend(["select"] * (len(rows) - len(current)))
        if decisions is not None:
            if len(decisions) != len(rows) or any(item not in ("select", "reject") for item in decisions):
                raise HTTPException(422, "each preview row must be selected or rejected")
            current = list(decisions)
        for correction in corrections or []:
            index = correction.get("rowIndex", correction.get("row_index"))
            if type(index) is not int or not 0 <= index < len(rows):
                raise HTTPException(422, "import row correction is out of range")
            changed = {key for key, value in correction.items() if value is not None}
            for field in (
                "submittedCourseCode", "term", "grade", "courseTitle",
                "attemptedCredits", "earnedCredits", "status",
            ):
                if correction.get(field) is not None:
                    rows[index][field] = correction[field]
            row = rows[index]
            row["submittedCourseCode"] = normalize_course_code(row["submittedCourseCode"])
            row["term"] = normalize_term(row["term"])
            if not row["submittedCourseCode"] or not row["term"]:
                raise HTTPException(422, "course code and term must not be blank")
            if "submittedCourseCode" in changed:
                row["resolvedCourseCode"] = row["submittedCourseCode"]
            # A changed grade must not retain the previous inferred completion
            # status or earned credits. Explicit corrections still take precedence.
            status = None if "grade" in changed and "status" not in changed else row.get("status")
            earned = row.get("earnedCredits")
            if changed & {"grade", "status", "attemptedCredits"} and "earnedCredits" not in changed:
                earned = None
            try:
                row["status"], row["earnedCredits"] = infer_status_and_credits(
                    row.get("grade"), row["attemptedCredits"], status, earned)
            except (TypeError, ValueError) as exc:
                raise HTTPException(422, str(exc)) from exc
            if row.get("grade") is not None:
                row["grade"] = row["grade"].strip().upper() or None
            current[index] = "select"
        updated = conn.execute(queries.get("update_import_batch_preview"), {
            "batch_id": batch_id,
            "parsed_rows": loose_jsonb(rows),
            "row_decisions": loose_jsonb(current),
            "preview_digest": _import_digest(rows, current),
        }).fetchone()
        return dict(updated)

    @classmethod
    def get_revision(cls, conn, workspace_key: str, revision: int, actor: Principal) -> dict[str, Any]:
        if not check_workspace_access(
            conn, actor, workspace_key, "all_records", action="read"
        ):
            raise HTTPException(403, "access to academic record is denied")
        record = cls.get_or_create_record(conn, workspace_key)
        row = conn.execute(queries.get("get_academic_record_revision"), {
            "record_id": record["record_id"], "revision": revision,
        }).fetchone()
        if row is None:
            raise HTTPException(404, "academic record revision was not found")
        return {
            "recordId": record["record_id"],
            "revision": row["revision"],
            "attempts": row["attempts"] or [],
        }

    @classmethod
    def verify_attempt(
        cls,
        conn,
        workspace_key: str,
        attempt_id: UUID,
        verification_method: str,
        verification_notes: str | None,
        actor: Principal,
    ) -> dict[str, Any]:
        require_capability(actor, Capability.VERIFY_ACADEMIC_RECORD)
        if not check_workspace_access(
            conn, actor, workspace_key, "all_records", str(attempt_id), action="verify"
        ):
            raise HTTPException(403, "verification access to academic record is denied")

        record = cls.get_or_create_record(conn, workspace_key, lock=True)
        record_id = record["record_id"]

        existing = conn.execute(
            queries.get("get_academic_record_attempt_by_id"),
            {"attempt_id": attempt_id},
        ).fetchone()
        if not existing or existing["record_id"] != record_id:
            raise HTTPException(404, "attempt not found on this record")

        valid_methods = {"manual_staff", "official_transcript", "sis_feed"}
        if verification_method not in valid_methods:
            raise HTTPException(422, f"invalid verification method: {verification_method}")

        verified = conn.execute(
            queries.get("verify_academic_record_attempt"),
            {
                "attempt_id": attempt_id,
                "verification_method": verification_method,
                "verified_by_user_id": actor.user_id,
                "verification_notes": verification_notes,
            },
        ).fetchone()

        _record_event(conn, record_id, attempt_id, "attempt_verified", actor, {
            "method": verification_method,
            "notes": verification_notes,
        })
        _bump_revision(conn, record_id)
        return dict(verified)
