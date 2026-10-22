"""Saved program comparisons. Mention overlap is not saved credit."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.advising.comparison_service import ProgramComparisonService
from app.api import deps
from app.api.schemas import (
    ProgramComparisonCreateRequest,
    ProgramComparisonMember,
    ProgramComparisonResponse,
    ProgramComparisonSummary,
)
from app.auth.dependencies import get_principal
from app.auth.models import Principal
from app.util.json import iso

router = APIRouter(prefix="/api/comparisons", tags=["comparisons"])
Conn = Annotated[object, Depends(deps.get_conn)]
CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def _member(row: dict) -> ProgramComparisonMember:
    return ProgramComparisonMember(
        position=row["position"],
        programId=row["program_id"],
        programTitle=row["program_title"],
        ruleReleaseId=row["rule_release_id"],
        auditId=row["audit_id"],
        savedStatus=row["saved_status"],
        savedCompletedCredits=float(row["saved_completed_credits"]),
        overlayStatus=row["overlay_status"],
        unresolvedCount=row["unresolved_count"],
    )


def _detail(data: dict) -> ProgramComparisonResponse:
    return ProgramComparisonResponse(
        comparisonId=data["comparison_id"],
        workspaceKey=data["workspace_key"],
        recordId=data["record_id"],
        recordRevision=data["record_revision"],
        sharingPolicy=data["sharing_policy"],
        mentionKind=data["mention_kind"],
        sharedMentions=list(data["shared_mentions"] or []),
        creditSavings=None if data["credit_savings"] is None else float(data["credit_savings"]),
        coverageComparable=data["coverage_comparable"],
        rankingBlockedReason=data["ranking_blocked_reason"],
        members=[_member(row) for row in data["members"]],
        createdAt=iso(data["created_at"]),
    )


@router.post("", response_model=ProgramComparisonResponse, response_model_exclude_unset=True)
def create_comparison(
    req: ProgramComparisonCreateRequest,
    conn: Conn,
    principal: CurrentPrincipal,
):
    target = req.workspaceKey or principal.workspace_key
    data = ProgramComparisonService.create(
        conn,
        target,
        req.programIds,
        req.addCourseCodes,
        req.removeCourseCodes,
        principal,
    )
    conn.commit()
    return _detail(data)


@router.get("", response_model=list[ProgramComparisonSummary], response_model_exclude_unset=True)
def list_comparisons(
    conn: Conn,
    principal: CurrentPrincipal,
    workspace_key: str | None = None,
):
    target = workspace_key or principal.workspace_key
    rows = ProgramComparisonService.list_for_workspace(conn, target, principal)
    conn.commit()
    return [
        ProgramComparisonSummary(
            comparisonId=row["comparison_id"],
            recordRevision=row["record_revision"],
            sharingPolicy=row["sharing_policy"],
            mentionKind=row["mention_kind"],
            sharedMentions=list(row["shared_mentions"] or []),
            creditSavings=None if row["credit_savings"] is None else float(row["credit_savings"]),
            coverageComparable=row["coverage_comparable"],
            rankingBlockedReason=row["ranking_blocked_reason"],
            createdAt=iso(row["created_at"]),
        )
        for row in rows
    ]


@router.get("/{comparison_id}", response_model=ProgramComparisonResponse, response_model_exclude_unset=True)
def get_comparison(
    comparison_id: UUID,
    conn: Conn,
    principal: CurrentPrincipal,
    workspace_key: str | None = None,
):
    target = workspace_key or principal.workspace_key
    data = ProgramComparisonService.get(conn, target, comparison_id, principal)
    conn.commit()
    return _detail(data)
