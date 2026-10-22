"""Feedback votes and redacted eval candidates."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api import deps
from app.api.schemas import (
    EvalCandidateCreateRequest,
    EvalCandidatePromotionRequest,
    EvalCandidatePromotionResponse,
    EvalCandidateResponse,
    FeedbackTriageListItem,
    FeedbackTriageRequest,
    FeedbackTriageResponse,
    ResponseFeedbackRequest,
    ResponseFeedbackResponse,
)
from app.auth.dependencies import get_principal
from app.auth.models import Principal
from app.gaps.feedback import FeedbackService

router = APIRouter(prefix="/api/feedback", tags=["feedback"])
Conn = Annotated[object, Depends(deps.get_conn)]
CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def _iso(value) -> str:
    return value.isoformat()


def _feedback(row: dict) -> ResponseFeedbackResponse:
    return ResponseFeedbackResponse(
        feedbackId=row["feedback_id"],
        workspaceKey=row["workspace_key"],
        responseId=row["response_id"],
        revision=row["revision"],
        vote=row["vote"],
        reasonCode=row["reason_code"],
        comment=row["comment"],
        currentVotes=row["current_votes"],
        createdAt=_iso(row["created_at"]),
    )


@router.post("", response_model=ResponseFeedbackResponse, response_model_exclude_unset=True)
def submit_feedback(body: ResponseFeedbackRequest, conn: Conn, principal: CurrentPrincipal):
    row = FeedbackService.submit(
        conn,
        body.workspaceKey or principal.workspace_key,
        body.responseId,
        body.vote,
        body.reasonCode,
        body.comment,
        principal,
    )
    conn.commit()
    return _feedback(row)


def _triage(row: dict) -> FeedbackTriageResponse:
    return FeedbackTriageResponse(
        feedbackId=row["feedback_id"],
        issueType=row["issue_type"],
        triageStatus=row["triage_status"],
        triageRevision=row["triage_revision"],
        voteRevision=row["vote_revision"],
        gapId=row["gap_id"],
        eventId=row["event_id"],
    )


@router.get("/triage", response_model=list[FeedbackTriageListItem])
def list_feedback_triage(conn: Conn, principal: CurrentPrincipal):
    rows = FeedbackService.list_triage(conn, principal)
    conn.commit()
    return [
        FeedbackTriageListItem(
            eventId=row["event_id"],
            feedbackId=row["feedback_id"],
            issueType=row["issue_type"],
            triageStatus=row["to_status"],
            triageRevision=row["triage_revision"],
            voteRevision=row["revision"],
            vote=row["vote"],
            gapId=row["gap_id"],
            createdAt=_iso(row["created_at"]),
        )
        for row in rows
    ]


@router.post("/triage", response_model=FeedbackTriageResponse, response_model_exclude_unset=True)
def triage_feedback(body: FeedbackTriageRequest, conn: Conn, principal: CurrentPrincipal):
    row = FeedbackService.triage(
        conn,
        principal,
        feedback_id=body.feedbackId,
        issue_type=body.issueType,
        to_status=body.toStatus,
        expected_revision=body.expectedRevision,
        expected_triage_revision=body.expectedTriageRevision,
        note=body.note,
        trace_id=body.traceId,
        collection_id=body.collectionId,
        investigation_query=body.investigationQuery,
    )
    conn.commit()
    return _triage(row)


@router.post(
    "/promotions",
    response_model=EvalCandidatePromotionResponse,
    response_model_exclude_unset=True,
)
def promote_eval_candidate(
    body: EvalCandidatePromotionRequest, conn: Conn, principal: CurrentPrincipal,
):
    row = FeedbackService.promote_candidate(
        conn,
        principal,
        feedback_id=body.feedbackId,
        redacted_prompt=body.redactedPrompt,
        redacted_expected=body.redactedExpected,
        source_id=body.sourceId,
        trace_id=body.traceId,
        collection_id=body.collectionId,
        re_review=body.reReview,
    )
    conn.commit()
    return EvalCandidatePromotionResponse(
        candidateId=row["candidate_id"],
        redactedPrompt=row["redacted_prompt"],
        redactedExpected=row["redacted_expected"],
        sourceFeedbackId=row["source_feedback_id"],
        sourceIndexRevision=row["source_index_revision"],
        promotionState=row["promotion_state"],
        reviewState=row["review_state"],
        createdAt=_iso(row["created_at"]),
    )


@router.post("/eval-candidates", response_model=EvalCandidateResponse, response_model_exclude_unset=True)
def create_eval_candidate(body: EvalCandidateCreateRequest, conn: Conn, principal: CurrentPrincipal):
    row = FeedbackService.create_candidate(
        conn, body.redactedPrompt, body.redactedExpected, body.sourceFeedbackId, principal,
    )
    conn.commit()
    return EvalCandidateResponse(
        candidateId=row["candidate_id"],
        redactedPrompt=row["redacted_prompt"],
        redactedExpected=row["redacted_expected"],
        sourceFeedbackId=row["source_feedback_id"],
        createdAt=_iso(row["created_at"]),
    )
