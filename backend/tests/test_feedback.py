"""A replacement vote does not inflate totals, and eval candidates omit student history."""

from uuid import uuid4

import pytest
from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app.db import queries
from app.gaps.feedback import FeedbackService
from tests.helpers import api_client, create_user


def test_vote_replacement_and_redacted_candidate(db, collection):
    student = create_user(db, role="student")
    other = create_user(db, role="student")
    reviewer = create_user(db, role="admin")
    response_id = _response(db, collection, student.workspace_key)

    first = FeedbackService.submit(
        db, student.workspace_key, response_id, "up", "helpful", None, student,
    )
    same = FeedbackService.submit(
        db, student.workspace_key, response_id, "up", "helpful", None, student,
    )
    alias = db.execute("SELECT response_id FROM responses WHERE trace_id = %s",
                       (response_id,)).fetchone()["response_id"]
    aliased = FeedbackService.submit(db, student.workspace_key, alias, "up", "helpful", None, student)
    assert aliased["feedback_id"] == first["feedback_id"]
    changed = FeedbackService.submit(
        db, student.workspace_key, response_id, "down", "missing-evidence", "needs a source", student,
    )
    assert same["feedback_id"] == first["feedback_id"]
    assert same["revision"] == 1
    assert changed["revision"] == 2
    assert changed["current_votes"] == 1
    assert first["current_votes"] == 1
    for hidden in (uuid4(), _response(db, collection, other.workspace_key)):
        with pytest.raises(HTTPException) as missing:
            FeedbackService.submit(db, student.workspace_key, hidden, "up", "helpful", None, student)
        assert missing.value.status_code == 404

    with pytest.raises(HTTPException) as denied:
        FeedbackService.submit(
            db, student.workspace_key, response_id, "down", "missing-evidence", None, other,
        )
    assert denied.value.status_code == 403

    from datetime import UTC, datetime, timedelta

    from app.admin.store import AdminStore

    advisor = create_user(db, role="advisor")
    AdminStore(db, reviewer).create_grant(
        grant_type="advisor_access", actor_user_id=advisor.user_id,
        target_workspace_key=student.workspace_key, resource_type="all_records",
        allowed_actions=["read"], reason="Read the student's academic record",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db.commit()
    with pytest.raises(HTTPException) as read_only:
        FeedbackService.submit(
            db, student.workspace_key, response_id, "up", "helpful", None, advisor,
        )
    assert read_only.value.status_code == 403

    with pytest.raises(HTTPException) as student_denied:
        FeedbackService.create_candidate(db, "What is CS 170?", "A catalog course.", None, student)
    assert student_denied.value.status_code == 403

    candidate = FeedbackService.create_candidate(
        db, "What does the catalog require?", "The cited requirement line.", changed["feedback_id"], reviewer,
    )
    stored = db.execute(
        "SELECT * FROM eval_candidates WHERE candidate_id = %s",
        (candidate["candidate_id"],),
    ).fetchone()
    assert set(stored.keys()) == {
        "candidate_id", "redacted_prompt", "redacted_expected", "source_feedback_id", "created_at",
        "candidate_digest", "source_id", "source_index_revision", "promotion_state",
        "review_state", "gap_id",
    }
    assert "workspace_key" not in stored
    assert "comment" not in stored
    assert student.workspace_key not in stored["redacted_prompt"]
    assert "needs a source" not in stored["redacted_expected"]

    client = api_client(db, collection)
    rejected = client.post(
        "/api/feedback/eval-candidates",
        json={
            "redactedPrompt": "What does the catalog require?",
            "redactedExpected": "The cited requirement line.",
            "studentHistory": [{"workspaceKey": student.workspace_key}],
        },
    )
    assert rejected.status_code == 422


def _source(db, collection, workspace_key):
    source_id = uuid4()
    db.execute(queries.get("upsert_source"), {
        "source_id": source_id,
        "collection_id": collection,
        "workspace_key": workspace_key,
        "filename": "catalog.md",
        "content_type": "text/markdown",
        "file_hash": "a" * 64,
        "raw_path": "catalog.md",
        "origin_key": None,
    })
    return source_id


def _trace(db, collection, workspace_key=None):
    trace_id = uuid4()
    db.execute(queries.get("insert_trace"), {
        "trace_id": trace_id,
        "collection_id": collection,
        "workspace_key": workspace_key,
        "query": "Which requirement line is absent?",
        "normalized_query": "which requirement line is absent?",
        "config_version": 1,
        "embed_model": "fake",
        "refused": True,
    })
    return trace_id


def _response(db, collection, workspace_key):
    response_id = uuid4()
    trace_id = _trace(db, collection, workspace_key)
    db.execute(queries.get("insert_response"), {
        "response_id": response_id, "trace_id": trace_id, "status": "refusal",
        "text": "", "reason": "no relevant material", "prompt_version": "test",
        "context_snapshot": Jsonb([]),
    })
    return trace_id


def test_triage_opens_a_gap_and_stale_source_blocks_silent_promotion(db, collection):
    student = create_user(db, role="student")
    advisor = create_user(db, role="advisor")
    reviewer = create_user(db, role="admin")
    response_id = _response(db, collection, student.workspace_key)
    comment = "needs a source from the student"
    feedback = FeedbackService.submit(
        db, student.workspace_key, response_id, "down", "missing-evidence", comment, student,
    )
    source_id = _source(db, collection, student.workspace_key)
    trace_id = _trace(db, collection)
    other_collection = uuid4()
    db.execute(queries.get("insert_collection"), {
        "collection_id": other_collection, "name": f"c-{other_collection}",
    })
    foreign_trace = _trace(db, other_collection)
    courses = db.execute("SELECT count(*) AS n FROM courses").fetchone()["n"]
    edges = db.execute("SELECT count(*) AS n FROM prereq_nodes").fetchone()["n"]

    with pytest.raises(HTTPException) as student_denied:
        FeedbackService.triage(
            db, student, feedback_id=feedback["feedback_id"], issue_type="provider",
            to_status="reviewed", expected_revision=1, expected_triage_revision=0,
            note=None, trace_id=None, collection_id=None, investigation_query=None,
        )
    assert student_denied.value.status_code == 403
    with pytest.raises(HTTPException) as advisor_denied:
        FeedbackService.triage(
            db, advisor, feedback_id=feedback["feedback_id"], issue_type="provider",
            to_status="reviewed", expected_revision=1, expected_triage_revision=0,
            note=None, trace_id=None, collection_id=None, investigation_query=None,
        )
    assert advisor_denied.value.status_code == 403

    provider = FeedbackService.triage(
        db, reviewer, feedback_id=feedback["feedback_id"], issue_type="provider",
        to_status="reviewed", expected_revision=1, expected_triage_revision=0,
        note="provider failed", trace_id=None, collection_id=None, investigation_query=None,
    )
    assert provider["issue_type"] == "provider"
    assert provider["gap_id"] is None
    assert db.execute("SELECT count(*) AS n FROM retrieval_gaps").fetchone()["n"] == 0

    with pytest.raises(HTTPException) as wrong_trace:
        FeedbackService.triage(
            db, reviewer, feedback_id=feedback["feedback_id"], issue_type="missing_evidence",
            to_status="reviewed", expected_revision=1, expected_triage_revision=1,
            note="kept", trace_id=foreign_trace, collection_id=collection,
            investigation_query="Which catalog requirement line is absent?",
        )
    assert wrong_trace.value.status_code == 403
    assert db.execute(
        "SELECT issue_type FROM response_feedback WHERE feedback_id = %s",
        (feedback["feedback_id"],),
    ).fetchone()["issue_type"] == "provider"

    with pytest.raises(HTTPException) as stale:
        FeedbackService.triage(
            db, reviewer, feedback_id=feedback["feedback_id"], issue_type="privacy",
            to_status="reviewed", expected_revision=1, expected_triage_revision=0,
            note="stale note stays in the client", trace_id=None, collection_id=None,
            investigation_query=None,
        )
    assert stale.value.status_code == 409
    assert db.execute(
        "SELECT count(*) AS n FROM feedback_triage_events WHERE note = %s",
        ("stale note stays in the client",),
    ).fetchone()["n"] == 0

    opened = FeedbackService.triage(
        db, reviewer, feedback_id=feedback["feedback_id"], issue_type="missing_evidence",
        to_status="reviewed", expected_revision=1, expected_triage_revision=1,
        note="open a gap", trace_id=trace_id, collection_id=collection,
        investigation_query="Which catalog requirement line is absent?",
    )
    assert opened["issue_type"] == "missing_evidence"
    assert opened["triage_revision"] == 2
    gap = db.execute(
        "SELECT query, workspace_key FROM retrieval_gaps WHERE gap_id = %s",
        (opened["gap_id"],),
    ).fetchone()
    assert comment not in gap["query"]
    assert gap["workspace_key"] == student.workspace_key
    again = FeedbackService.triage(
        db, reviewer, feedback_id=feedback["feedback_id"], issue_type="missing_evidence",
        to_status="reviewed", expected_revision=1, expected_triage_revision=2,
        note="open a gap", trace_id=trace_id, collection_id=collection,
        investigation_query="Which catalog requirement line is absent?",
    )
    assert again["event_id"] == opened["event_id"]
    # A second report about the same trace joins the gap without counting the trace twice.
    second = FeedbackService.submit(
        db, student.workspace_key, _response(db, collection, student.workspace_key),
        "down", "missing-evidence", None, student,
    )
    joined = FeedbackService.triage(
        db, reviewer, feedback_id=second["feedback_id"], issue_type="missing_evidence",
        to_status="reviewed", expected_revision=1, expected_triage_revision=0,
        note=None, trace_id=trace_id, collection_id=collection,
        investigation_query="Which catalog requirement line is absent?",
    )
    assert joined["gap_id"] == opened["gap_id"]
    assert db.execute("SELECT occurrence_count FROM retrieval_gaps WHERE gap_id = %s",
                      (opened["gap_id"],)).fetchone()["occurrence_count"] == 1
    assert db.execute("SELECT count(*) AS n FROM retrieval_gaps").fetchone()["n"] == 1
    assert db.execute("SELECT count(*) AS n FROM courses").fetchone()["n"] == courses
    assert db.execute("SELECT count(*) AS n FROM prereq_nodes").fetchone()["n"] == edges

    promoted = FeedbackService.promote_candidate(
        db, reviewer, feedback_id=feedback["feedback_id"],
        redacted_prompt="Which requirement is missing?",
        redacted_expected="The catalog line for the requirement.",
        source_id=source_id, trace_id=trace_id, collection_id=collection, re_review=False,
    )
    duplicate = FeedbackService.promote_candidate(
        db, reviewer, feedback_id=feedback["feedback_id"],
        redacted_prompt="Which requirement is missing?",
        redacted_expected="The catalog line for the requirement.",
        source_id=source_id, trace_id=trace_id, collection_id=collection, re_review=False,
    )
    assert duplicate["candidate_id"] == promoted["candidate_id"]
    db.execute(
        "UPDATE sources SET index_revision = index_revision + 1 WHERE source_id = %s",
        (source_id,),
    )
    with pytest.raises(HTTPException) as blocked:
        FeedbackService.promote_candidate(
            db, reviewer, feedback_id=feedback["feedback_id"],
            redacted_prompt="Which requirement is missing?",
            redacted_expected="The catalog line for the requirement.",
            source_id=source_id, trace_id=trace_id, collection_id=collection, re_review=False,
        )
    assert blocked.value.status_code == 409
    stale_row = db.execute(
        "SELECT promotion_state FROM eval_candidates WHERE candidate_id = %s",
        (promoted["candidate_id"],),
    ).fetchone()
    assert stale_row["promotion_state"] == "stale"
    reviewed = FeedbackService.promote_candidate(
        db, reviewer, feedback_id=feedback["feedback_id"],
        redacted_prompt="Which requirement is missing?",
        redacted_expected="The catalog line for the requirement.",
        source_id=source_id, trace_id=trace_id, collection_id=collection, re_review=True,
    )
    assert reviewed["candidate_id"] != promoted["candidate_id"]
    assert reviewed["review_state"] == "re_reviewed"
    assert reviewed["source_index_revision"] == 1
    stored = db.execute(
        "SELECT * FROM eval_candidates WHERE candidate_id = %s",
        (reviewed["candidate_id"],),
    ).fetchone()
    assert "workspace_key" not in stored
    assert "comment" not in stored
    assert student.workspace_key not in stored["redacted_prompt"]
    assert comment not in stored["redacted_expected"]
    surviving = db.execute(queries.get("get_current_response_feedback"), {
        "workspace_key": student.workspace_key, "response_id": response_id,
    }).fetchone()
    assert surviving["feedback_id"] == feedback["feedback_id"]
    assert surviving["comment"] == comment
    assert surviving["revision"] == 1

    client = api_client(db, collection)
    hidden = client.post("/api/feedback/promotions", json={
        "feedbackId": str(feedback["feedback_id"]),
        "redactedPrompt": "Which requirement is missing?",
        "redactedExpected": "The catalog line for the requirement.",
        "sourceId": str(source_id),
        "traceId": str(trace_id),
        "collectionId": str(collection),
        "studentHistory": [{"workspaceKey": student.workspace_key}],
    })
    assert hidden.status_code == 422
    supplied = client.post("/api/feedback/triage", json={
        "feedbackId": str(feedback["feedback_id"]),
        "issueType": "ui",
        "toStatus": "reviewed",
        "expectedRevision": 1,
        "expectedTriageRevision": 2,
        "workspaceKey": "someone-else",
    })
    assert supplied.status_code == 422
