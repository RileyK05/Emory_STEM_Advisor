"""API routes for deterministic program degree audits and evaluations."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.advising.audit_service import DegreeAuditService
from app.api import deps
from app.api.schemas import (
    AuditableProgramItem,
    DegreeAuditDetailResponse,
    DegreeAuditRecordItem,
    DegreeAuditRuleEvaluationItem,
    DegreeAuditRunRequest,
)
from app.auth.dependencies import get_principal
from app.auth.models import Principal
from app.util.json import iso

router = APIRouter(prefix="/api/audits", tags=["audits"])
Conn = Annotated[object, Depends(deps.get_conn)]
CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def _serialize_audit(a: dict) -> DegreeAuditRecordItem:
    return DegreeAuditRecordItem(
        auditId=a["audit_id"],
        workspaceKey=a["workspace_key"],
        programId=a["program_id"],
        ruleReleaseId=a["rule_release_id"],
        recordId=a["record_id"],
        recordRevision=a["record_revision"],
        rulesDigest=a["rules_digest"],
        overallStatus=a["overall_status"],
        isStale=bool(a["is_stale"]),
        completedCredits=float(a["completed_credits"]),
        inProgressCredits=float(a["in_progress_credits"]),
        unverifiedCredits=float(a["unverified_credits"]),
        resultsSummary=a.get("results_summary") or {},
        blockers=a.get("blockers") or [],
        assumptions=a.get("assumptions") or [],
        programTitle=a.get("program_title"),
        programKind=a.get("program_kind"),
        createdAt=iso(a["created_at"]),
    )


def _detail(data: dict) -> DegreeAuditDetailResponse:
    return DegreeAuditDetailResponse(
        audit=_serialize_audit(data["audit"]),
        evaluations=[_serialize_evaluation(e) for e in data["evaluations"]],
        programTitle=data["programTitle"],
        programKind=data["programKind"],
        coverageScope="program_audit",
    )


def _serialize_evaluation(e: dict) -> DegreeAuditRuleEvaluationItem:
    return DegreeAuditRuleEvaluationItem(
        evaluationId=e["evaluation_id"],
        auditId=e["audit_id"],
        nodeId=e["node_id"],
        parentNodeId=e.get("parent_node_id"),
        nodeType=e["node_type"],
        title=e["title"],
        status=e["status"],
        allocatedAttemptIds=e.get("allocated_attempt_ids") or [],
        allocatedCredits=float(e["allocated_credits"]),
        requiredCount=e.get("required_count"),
        satisfiedCount=e.get("satisfied_count"),
        unresolvedReason=e.get("unresolved_reason"),
        rawPosition=e.get("raw_position"),
        createdAt=iso(e["created_at"]),
    )


@router.post("/run", response_model=DegreeAuditDetailResponse, response_model_exclude_unset=True)
def run_audit(
    req: DegreeAuditRunRequest,
    conn: Conn,
    principal: CurrentPrincipal,
):
    target_workspace = req.workspaceKey or principal.workspace_key
    data = DegreeAuditService.run_audit(
        conn,
        target_workspace,
        req.programId,
        principal,
    )
    conn.commit()
    return _detail(data)


@router.get("/programs", response_model=list[AuditableProgramItem], response_model_exclude_unset=True)
def list_auditable_programs(conn: Conn, _principal: CurrentPrincipal):
    rows = DegreeAuditService.list_auditable_programs(conn)
    conn.commit()
    return [
        AuditableProgramItem(
            programId=row["program_id"],
            title=row["title"],
            kind=row["kind"],
            ruleReleaseId=row["rule_release_id"],
        )
        for row in rows
    ]


@router.get("/{audit_id}/export", response_model=DegreeAuditDetailResponse, response_model_exclude_unset=True)
def export_audit(
    audit_id: UUID,
    conn: Conn,
    principal: CurrentPrincipal,
    workspace_key: str | None = None,
):
    target_workspace = workspace_key or principal.workspace_key
    data = DegreeAuditService.get_audit(conn, target_workspace, audit_id, principal)
    conn.commit()
    return _detail(data)


@router.get("/{audit_id}", response_model=DegreeAuditDetailResponse, response_model_exclude_unset=True)
def get_audit(
    audit_id: UUID,
    conn: Conn,
    principal: CurrentPrincipal,
    workspace_key: str | None = None,
):
    target_workspace = workspace_key or principal.workspace_key
    data = DegreeAuditService.get_audit(
        conn,
        target_workspace,
        audit_id,
        principal,
    )
    conn.commit()
    return _detail(data)


@router.get("", response_model=list[DegreeAuditRecordItem], response_model_exclude_unset=True)
def list_audits(
    conn: Conn,
    principal: CurrentPrincipal,
    workspace_key: str | None = None,
):
    target_workspace = workspace_key or principal.workspace_key
    rows = DegreeAuditService.list_audits(
        conn,
        target_workspace,
        principal,
    )
    conn.commit()
    return [_serialize_audit(r) for r in rows]

