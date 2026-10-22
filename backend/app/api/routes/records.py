"""API routes for student academic records, course attempts, imports, and verification."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from app.api import deps
from app.api.schemas import (
    AcademicRecordAttemptItem,
    AcademicRecordAttemptUpsertRequest,
    AcademicRecordDetailResponse,
    AcademicRecordImportBatchResponse,
    AcademicRecordImportCommitRequest,
    AcademicRecordImportCommitResponse,
    AcademicRecordImportPreviewRequest,
    AcademicRecordImportUpdateRequest,
    AcademicRecordRevisionResponse,
    AcademicRecordSummary,
    AcademicRecordVerificationEventItem,
    AcademicRecordVerifyAttemptRequest,
)
from app.auth.dependencies import get_principal
from app.auth.models import Principal
from app.records.service import AcademicRecordService
from app.util.json import iso

router = APIRouter(prefix="/api/academic-record", tags=["academic-record"])
Conn = Annotated[object, Depends(deps.get_conn)]
CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def _serialize_record(r: dict) -> AcademicRecordSummary:
    return AcademicRecordSummary(
        recordId=r["record_id"],
        workspaceKey=r["workspace_key"],
        recordRevision=r["record_revision"],
        createdAt=iso(r["created_at"]),
        updatedAt=iso(r["updated_at"]),
    )


def _serialize_attempt(a: dict) -> AcademicRecordAttemptItem:
    return AcademicRecordAttemptItem(
        attemptId=a["attempt_id"],
        recordId=a["record_id"],
        workspaceKey=a["workspace_key"],
        institution=a["institution"],
        submittedCourseCode=a["submitted_course_code"],
        resolvedCourseCode=a.get("resolved_course_code"),
        courseTitle=a.get("course_title"),
        term=a["term"],
        attemptedCredits=float(a["attempted_credits"]),
        earnedCredits=float(a["earned_credits"]),
        grade=a.get("grade"),
        status=a["status"],
        provenance=a["provenance"],
        isVerified=bool(a["is_verified"]),
        verificationMethod=a.get("verification_method"),
        verifiedByUserId=a.get("verified_by_user_id"),
        verifiedAt=iso(a.get("verified_at")),
        verificationNotes=a.get("verification_notes"),
        createdAt=iso(a["created_at"]),
        updatedAt=iso(a["updated_at"]),
    )


def _serialize_event(e: dict) -> AcademicRecordVerificationEventItem:
    return AcademicRecordVerificationEventItem(
        eventId=e["event_id"],
        recordId=e["record_id"],
        attemptId=e.get("attempt_id"),
        eventType=e["event_type"],
        actorUserId=e.get("actor_user_id"),
        actorRole=e["actor_role"],
        details=e.get("details") or {},
        createdAt=iso(e["created_at"]),
    )


@router.get("", response_model=AcademicRecordDetailResponse, response_model_exclude_unset=True)
def get_record(
    conn: Conn,
    principal: CurrentPrincipal,
    workspace_key: str | None = None,
):
    target_workspace = workspace_key or principal.workspace_key
    data = AcademicRecordService.get_record(
        conn, target_workspace, principal,
        create=target_workspace == principal.workspace_key,
    )
    conn.commit()
    return AcademicRecordDetailResponse(
        record=_serialize_record(data["record"]),
        attempts=[_serialize_attempt(a) for a in data["attempts"]],
        recentEvents=[_serialize_event(e) for e in data["recentEvents"]],
    )


@router.post("/attempts", response_model=AcademicRecordAttemptItem, response_model_exclude_unset=True)
def upsert_attempt(
    req: AcademicRecordAttemptUpsertRequest,
    conn: Conn,
    principal: CurrentPrincipal,
):
    target_workspace = req.workspaceKey or principal.workspace_key
    attempt = AcademicRecordService.upsert_attempt(
        conn,
        target_workspace,
        req.model_dump(),
        principal,
    )
    conn.commit()
    return _serialize_attempt(attempt)


@router.delete("/attempts/{attempt_id}", status_code=204, response_class=Response,
               responses={204: {"description": "Academic attempt deleted."}})
def delete_attempt(
    attempt_id: UUID,
    conn: Conn,
    principal: CurrentPrincipal,
    workspace_key: str | None = None,
):
    target_workspace = workspace_key or principal.workspace_key
    AcademicRecordService.delete_attempt(conn, target_workspace, attempt_id, principal)
    conn.commit()
    return Response(status_code=204)


@router.post(
    "/attempts/{attempt_id}/verify",
    response_model=AcademicRecordAttemptItem,
    response_model_exclude_unset=True,
)
def verify_attempt(
    attempt_id: UUID,
    req: AcademicRecordVerifyAttemptRequest,
    conn: Conn,
    principal: CurrentPrincipal,
):
    target_workspace = req.workspaceKey or principal.workspace_key
    verified = AcademicRecordService.verify_attempt(
        conn,
        target_workspace,
        attempt_id,
        req.method or "manual_staff",
        req.notes,
        principal,
    )
    conn.commit()
    return _serialize_attempt(verified)


@router.post(
    "/imports/preview",
    response_model=AcademicRecordImportBatchResponse,
    response_model_exclude_unset=True,
)
def import_preview(
    req: AcademicRecordImportPreviewRequest,
    conn: Conn,
    principal: CurrentPrincipal,
):
    target_workspace = req.workspaceKey or principal.workspace_key
    batch = AcademicRecordService.create_import_preview(
        conn,
        target_workspace,
        req.filename,
        req.rawContent,
        req.expectedRecordRevision,
        principal,
    )
    conn.commit()
    return _batch_response(batch)


def _batch_response(batch: dict) -> AcademicRecordImportBatchResponse:
    return AcademicRecordImportBatchResponse(
        batchId=batch["batch_id"],
        recordId=batch["record_id"],
        workspaceKey=batch["workspace_key"],
        expectedRecordRevision=batch["expected_record_revision"],
        status=batch["status"],
        filename=batch["filename"],
        parsedRows=batch.get("parsed_rows") or [],
        rowErrors=batch.get("row_errors") or [],
        duplicateCandidates=batch.get("duplicate_candidates") or [],
        previewDigest=batch.get("preview_digest"),
        rowDecisions=list(batch.get("row_decisions") or []),
        createdAt=iso(batch["created_at"]),
        committedAt=iso(batch.get("committed_at")),
    )


@router.post(
    "/imports/{batch_id}/rows",
    response_model=AcademicRecordImportBatchResponse,
    response_model_exclude_unset=True,
)
def update_import_preview(
    batch_id: UUID,
    req: AcademicRecordImportUpdateRequest,
    conn: Conn,
    principal: CurrentPrincipal,
):
    target_workspace = req.workspaceKey or principal.workspace_key
    batch = AcademicRecordService.update_import_preview(
        conn,
        target_workspace,
        batch_id,
        req.expectedRecordRevision,
        req.decisions,
        [item.model_dump() for item in req.corrections],
        principal,
    )
    conn.commit()
    return _batch_response(batch)


@router.get(
    "/revisions/{revision}",
    response_model=AcademicRecordRevisionResponse,
    response_model_exclude_unset=True,
)
def get_record_revision(
    revision: int,
    conn: Conn,
    principal: CurrentPrincipal,
    workspace_key: str | None = None,
):
    target_workspace = workspace_key or principal.workspace_key
    row = AcademicRecordService.get_revision(conn, target_workspace, revision, principal)
    conn.commit()
    return AcademicRecordRevisionResponse(
        recordId=row["recordId"],
        revision=row["revision"],
        attempts=row["attempts"],
    )


@router.post(
    "/imports/{batch_id}/commit",
    response_model=AcademicRecordImportCommitResponse,
    response_model_exclude_unset=True,
)
def import_commit(
    batch_id: UUID,
    req: AcademicRecordImportCommitRequest,
    conn: Conn,
    principal: CurrentPrincipal,
):
    target_workspace = req.workspaceKey or principal.workspace_key
    result = AcademicRecordService.commit_import_batch(
        conn,
        target_workspace,
        batch_id,
        req.expectedRecordRevision,
        req.previewDigest,
        principal,
    )
    conn.commit()
    return AcademicRecordImportCommitResponse(
        batchId=result["batchId"],
        status=result["status"],
        attemptsImported=result["attemptsImported"],
        newRecordRevision=result["newRecordRevision"],
    )

