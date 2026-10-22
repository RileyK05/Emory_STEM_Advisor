"""Owner-scoped feedback revisions, triage, and redacted eval candidates."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg.errors import UniqueViolation

from app.auth.capabilities import check_workspace_access, require_operation
from app.auth.models import Principal
from app.db import queries
from app.gaps.store import GapConflict, GapInvalidObservation, GapStore
from app.privacy.fence import require_live_workspace
from app.util.digest import json_digest

_ISSUE_TYPES = frozenset({"missing_evidence", "provider", "explanation", "privacy", "ui"})
_TRIAGE_STATUSES = frozenset({"open", "reviewed", "withdrawn"})


class FeedbackService:
    @staticmethod
    def _owner(conn, actor: Principal, workspace_key: str) -> None:
        if actor.workspace_key != workspace_key or not check_workspace_access(
                conn, actor, workspace_key, "all_records", action="write"):
            raise HTTPException(403, "access to this response is denied")
        require_live_workspace(conn, workspace_key, lock=True)

    @classmethod
    def submit(
        cls,
        conn,
        workspace_key: str,
        response_id: UUID,
        vote: str,
        reason_code: str,
        comment: str | None,
        actor: Principal,
    ) -> dict[str, Any]:
        cls._owner(conn, actor, workspace_key)
        current = conn.execute(
            queries.get("get_current_response_feedback"),
            {"workspace_key": workspace_key, "response_id": response_id},
        ).fetchone()
        unchanged = (
            current is not None
            and current["vote"] == vote
            and current["reason_code"] == reason_code
            and current["comment"] == comment
        )
        if unchanged:
            stored = dict(current)
        else:
            revision = 1 if current is None else int(current["revision"]) + 1
            if current is not None:
                conn.execute(
                    queries.get("clear_current_response_feedback"),
                    {"workspace_key": workspace_key, "response_id": response_id},
                )
            stored = dict(conn.execute(
                queries.get("insert_response_feedback"),
                {
                    "feedback_id": uuid4(),
                    "workspace_key": workspace_key,
                    "response_id": response_id,
                    "revision": revision,
                    "vote": vote,
                    "reason_code": reason_code,
                    "comment": comment,
                },
            ).fetchone())
        votes = conn.execute(
            queries.get("count_current_feedback_votes"),
            {"response_id": response_id},
        ).fetchone()["votes"]
        stored["current_votes"] = votes
        return stored

    @classmethod
    def create_candidate(
        cls,
        conn,
        redacted_prompt: str,
        redacted_expected: str,
        source_feedback_id: UUID | None,
        actor: Principal,
    ) -> dict[str, Any]:
        if actor.role not in {"admin", "advisor"}:
            raise HTTPException(403, "eval candidate review is denied")
        row = conn.execute(
            queries.get("insert_eval_candidate"),
            {
                "candidate_id": uuid4(),
                "redacted_prompt": redacted_prompt.strip(),
                "redacted_expected": redacted_expected.strip(),
                "source_feedback_id": source_feedback_id,
            },
        ).fetchone()
        return dict(row)

    @classmethod
    def list_triage(cls, conn, actor: Principal) -> list[dict[str, Any]]:
        require_operation(actor, "sources.review")
        rows = conn.execute(queries.get("list_feedback_triage_events"), {}).fetchall()
        return [dict(row) for row in rows]

    @classmethod
    def triage(
        cls,
        conn,
        actor: Principal,
        *,
        feedback_id: UUID,
        issue_type: str,
        to_status: str,
        expected_revision: int,
        expected_triage_revision: int,
        note: str | None,
        trace_id: UUID | None,
        collection_id: UUID | None,
        investigation_query: str | None,
    ) -> dict[str, Any]:
        """Record a reviewed transition. Missing evidence opens a gap, not a catalog fact."""
        require_operation(actor, "sources.review")
        if issue_type not in _ISSUE_TYPES or to_status not in _TRIAGE_STATUSES:
            raise HTTPException(422, "unsupported feedback issue or triage status")
        feedback = conn.execute(
            queries.get("get_response_feedback_for_update"),
            {"feedback_id": feedback_id},
        ).fetchone()
        if feedback is None:
            raise HTTPException(404, "feedback not found")
        if (
            feedback["issue_type"] == issue_type
            and feedback["triage_status"] == to_status
            and int(feedback["triage_revision"]) == expected_triage_revision
            and int(feedback["revision"]) == expected_revision
        ):
            prior = conn.execute(
                queries.get("latest_feedback_triage_event"),
                {"feedback_id": feedback_id},
            ).fetchone()
            return _triage_result(feedback, prior["gap_id"] if prior else None,
                                  prior["event_id"] if prior else None)
        if (
            int(feedback["revision"]) != expected_revision
            or int(feedback["triage_revision"]) != expected_triage_revision
        ):
            raise HTTPException(409, "feedback revision changed; refresh before triage")
        gap_id = None
        if issue_type == "missing_evidence":
            if feedback["vote"] != "down":
                raise HTTPException(422, "a negative vote opens an investigation")
            gap_id = cls._open_gap(
                conn, feedback, trace_id, collection_id, investigation_query,
            )
        event = conn.execute(
            queries.get("insert_feedback_triage_event"),
            {
                "event_id": uuid4(),
                "feedback_id": feedback_id,
                "issue_type": issue_type,
                "from_status": feedback["triage_status"],
                "to_status": to_status,
                "actor_user_id": actor.user_id,
                "note": note,
                "expected_revision": expected_revision,
                "gap_id": gap_id,
            },
        ).fetchone()
        updated = conn.execute(
            queries.get("set_feedback_triage"),
            {
                "feedback_id": feedback_id,
                "issue_type": issue_type,
                "to_status": to_status,
                "expected_revision": expected_revision,
                "expected_triage_revision": expected_triage_revision,
            },
        ).fetchone()
        if updated is None:
            raise HTTPException(409, "feedback revision changed; refresh before triage")
        return _triage_result(updated, gap_id, event["event_id"])

    @classmethod
    def _open_gap(cls, conn, feedback, trace_id, collection_id, investigation_query) -> UUID:
        if trace_id is None or collection_id is None or not (investigation_query or "").strip():
            raise HTTPException(422, "missing evidence needs a trace, collection, and query")
        comment = feedback["comment"] or ""
        if comment and comment in investigation_query:
            raise HTTPException(422, "investigation query copies the student comment")
        try:
            gap = GapStore(conn, feedback["workspace_key"]).record(
                collection_id=collection_id, query=investigation_query, source_ids=None,
                trace_id=trace_id, event_kind="retrieval_refusal",
            )
        except ValueError as exc:
            raise HTTPException(422, "investigation query is empty") from exc
        except GapInvalidObservation as exc:
            raise HTTPException(403, "trace ownership is denied") from exc
        except GapConflict as exc:
            raise HTTPException(409, str(exc) or "trace is already counted against another gap") from exc
        return gap["gap_id"]

    @classmethod
    def promote_candidate(
        cls,
        conn,
        actor: Principal,
        *,
        feedback_id: UUID,
        redacted_prompt: str,
        redacted_expected: str,
        source_id: UUID,
        trace_id: UUID,
        collection_id: UUID,
        re_review: bool,
    ) -> dict[str, Any]:
        """Pin one redacted candidate. A moved source blocks a silent second promotion."""
        require_operation(actor, "sources.review")
        feedback = conn.execute(
            queries.get("get_response_feedback_for_update"),
            {"feedback_id": feedback_id},
        ).fetchone()
        if feedback is None:
            raise HTTPException(404, "feedback not found")
        cls._reject_student_text(feedback, redacted_prompt, redacted_expected)
        trace = conn.execute(
            queries.get("get_gap_trace_for_collection"),
            {"trace_id": trace_id},
        ).fetchone()
        if trace is None or trace["collection_id"] != collection_id:
            raise HTTPException(403, "trace ownership is denied")
        source = conn.execute(
            queries.get("get_source_index_for_promotion"),
            {"source_id": source_id},
        ).fetchone()
        if source is None or source["collection_id"] != collection_id:
            raise HTTPException(403, "source ownership is denied")
        if source["workspace_key"] not in (None, feedback["workspace_key"]):
            raise HTTPException(403, "source ownership is denied")
        prompt = redacted_prompt.strip()
        expected = redacted_expected.strip()
        digest = json_digest({
            "expected": expected,
            "feedbackId": str(feedback_id),
            "prompt": prompt,
            "sourceId": str(source_id),
        })
        current = conn.execute(
            queries.get("get_current_eval_candidate_by_digest"),
            {"candidate_digest": digest},
        ).fetchone()
        pinned = int(source["index_revision"])
        if current is not None and int(current["source_index_revision"]) == pinned:
            return dict(current)
        if current is not None and not re_review:
            conn.execute(
                queries.get("stale_current_eval_candidate"),
                {"candidate_digest": digest},
            )
            conn.commit()
            raise HTTPException(409, "source evidence changed; re-review is required")
        try:
            with conn.transaction():
                if current is not None:
                    conn.execute(
                        queries.get("stale_current_eval_candidate"),
                        {"candidate_digest": digest},
                    )
                row = conn.execute(queries.get("insert_promoted_eval_candidate"), {
                    "candidate_id": uuid4(),
                    "redacted_prompt": prompt,
                    "redacted_expected": expected,
                    "source_feedback_id": feedback_id,
                    "candidate_digest": digest,
                    "source_id": source_id,
                    "source_index_revision": pinned,
                    "review_state": "re_reviewed" if re_review else "reviewed",
                    "gap_id": None,
                }).fetchone()
        except UniqueViolation:
            row = conn.execute(
                queries.get("get_current_eval_candidate_by_digest"),
                {"candidate_digest": digest},
            ).fetchone()
            if row is None:
                raise
        return dict(row)

    @staticmethod
    def _reject_student_text(feedback, redacted_prompt: str, redacted_expected: str) -> None:
        private = [feedback["workspace_key"]]
        if feedback["comment"]:
            private.append(feedback["comment"])
        combined = f"{redacted_prompt}\n{redacted_expected}"
        if any(piece and piece in combined for piece in private):
            raise HTTPException(422, "redacted candidate includes student history")


def _triage_result(feedback, gap_id, event_id) -> dict[str, Any]:
    return {
        "feedback_id": feedback["feedback_id"],
        "issue_type": feedback["issue_type"],
        "triage_status": feedback["triage_status"],
        "triage_revision": feedback["triage_revision"],
        "vote_revision": feedback["revision"],
        "gap_id": gap_id,
        "event_id": event_id,
    }
