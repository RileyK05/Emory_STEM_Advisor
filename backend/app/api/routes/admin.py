"""Institutional Admin API: users, roles, audit, and audited content inspection."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.admin.catalog_refresh import CatalogRefreshService
from app.admin.course_maintenance import CourseMaintenanceService
from app.admin.source_review import SourceReviewService
from app.admin.store import AdminStore
from app.api import deps
from app.api.schemas import (
    AdminArtifact,
    AdminAuditEntry,
    AdminCollection,
    AdminCollectionWrite,
    AdminConversation,
    AdminCourse,
    AdminCourseChange,
    AdminCourseChangeCreate,
    AdminCourseChangeReject,
    AdminCourseConflict,
    AdminCourseDependent,
    AdminCourseDetail,
    AdminCourseListResponse,
    AdminCourseMention,
    AdminCourseNode,
    AdminCourseParsedMention,
    AdminCourseParsedRequisites,
    AdminCoursePreviewResponse,
    AdminCourseProgramReference,
    AdminCoursePublishResponse,
    AdminJob,
    AdminOverview,
    AdminRevokedSessions,
    AdminSource,
    AdminTranscript,
    AdminUser,
    AdminUserSession,
    AdminUserUpdate,
    AlertStatusResponse,
    CatalogEditionItem,
    CatalogRefreshBatchResponse,
    CatalogRefreshPublishRequest,
    CatalogRefreshStartRequest,
    CatalogRefreshStatusResponse,
    GrantedResultStaging,
    ModelPriceSchedule,
    ModelPriceScheduleCreate,
    ProgramInspectionResponse,
    ProgramRawItem,
    ProgramRuleDiffResponse,
    ProgramRuleDraftCreate,
    ProgramRuleRejectAction,
    ProgramRuleReleaseDetail,
    ProgramRuleReleaseSummary,
    ProgramRuleReviewAction,
    ResourceGrant,
    ResourceGrantCreate,
    SourceDependencies,
    SourceImpactPreviewResponse,
    SourceLifecycleChangeRequest,
    SourceLifecycleChangeResponse,
    SourceLifecycleEvent,
    SourceReviewDetailResponse,
    SourceReviewProposal,
    SourceReviewProposalCreate,
    SourceReviewProposalReject,
    SourceRevisionEvidenceResponse,
    SourceRevisionPassage,
    SupportGrantRequest,
    SupportGrantRequestCreate,
    SupportGrantRequestDecision,
    SupportGrantRequestUpdate,
    TelemetryUsageResponse,
)
from app.auth.dependencies import get_admin_principal, get_auditor_or_admin_principal
from app.auth.models import Principal
from app.db import queries
from app.ingest.evidence import preserve_indexed_source_evidence
from app.pricing import PricingService
from app.programs.review_service import ProgramReviewService
from app.util.json import iso

router = APIRouter(prefix="/api/admin", tags=["institutional admin"])
Conn = Annotated[object, Depends(deps.get_conn)]


def _admin(request: Request,
           principal: Annotated[Principal, Depends(get_admin_principal)]) -> Principal:
    if not request.app.state.settings.admin_panel_enabled:
        raise HTTPException(404, "institutional administration is disabled")
    return principal


def _auditor_or_admin(
    request: Request,
    principal: Annotated[Principal, Depends(get_auditor_or_admin_principal)],
) -> Principal:
    if not request.app.state.settings.admin_panel_enabled:
        raise HTTPException(404, "institutional administration is disabled")
    return principal


Admin = Annotated[Principal, Depends(_admin)]
AuditorOrAdmin = Annotated[Principal, Depends(_auditor_or_admin)]


def _operation(principal: Principal, operation: str) -> None:
    from app.auth.capabilities import require_operation

    require_operation(principal, operation)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _parse_timestamp(value: str, field: str) -> datetime:
    try:
        return _aware(datetime.fromisoformat(value))
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, f"{field} must be an ISO timestamp") from exc


def _user(row) -> AdminUser:
    return AdminUser(
        userId=row["user_id"], issuer=row["issuer"], subject=row["subject"],
        workspaceKey=row["workspace_key"], displayName=row["display_name"],
        email=row["email"], role=row["role"], status=row["status"],
        conversationCount=row.get("conversation_count", 0),
        activeSessions=row.get("active_sessions", 0),
        createdAt=iso(row["created_at"]), updatedAt=iso(row["updated_at"]),
        lastSeenAt=iso(row["last_seen_at"]),
    )


def _conversation(row) -> AdminConversation:
    return AdminConversation(
        conversationId=row["conversation_id"], workspaceKey=row["workspace_key"],
        title=row["title"], lastSequence=row["last_sequence"],
        pendingRequestId=row["pending_request_id"],
        turnCount=row.get("turn_count", 0), summaryCount=row.get("summary_count", 0),
        createdAt=iso(row["created_at"]), updatedAt=iso(row["updated_at"]),
    )


@router.get("/overview", response_model=AdminOverview)
def overview(conn: Conn, principal: Admin):
    row = AdminStore(conn, principal).overview()
    return AdminOverview(
        users=row["users"], admins=row["admins"], disabledUsers=row["disabled_users"],
        activeSessions=row["active_sessions"], conversations=row["conversations"],
        turns=row["turns"], summaries=row["summaries"], queuedJobs=row["queued_jobs"],
        runningJobs=row["running_jobs"], failedJobs=row["failed_jobs"],
        sources=row["sources"], artifacts=row["artifacts"], traces=row["traces"],
    )


@router.get("/users", response_model=list[AdminUser])
def users(conn: Conn, principal: Admin, query: str = "", role: str = "",
          status: str = "", limit: int = Query(50, ge=1, le=200), offset: int = 0):
    rows = AdminStore(conn, principal).users(
        query=query, role=role, status=status, limit=limit, offset=offset)
    return [_user(row) for row in rows]


@router.get("/users/{user_id}", response_model=AdminUser)
def user(user_id: UUID, conn: Conn, principal: Admin):
    return _user(AdminStore(conn, principal).user(user_id))


@router.get("/users/{user_id}/sessions", response_model=list[AdminUserSession])
def user_sessions(user_id: UUID, conn: Conn, principal: Admin):
    rows = AdminStore(conn, principal).user_sessions(user_id)
    return [AdminUserSession(
        createdAt=iso(row["created_at"]), expiresAt=iso(row["expires_at"]),
        revokedAt=iso(row["revoked_at"]),
    ) for row in rows]


@router.patch("/users/{user_id}", response_model=AdminUser)
def update_user(user_id: UUID, body: AdminUserUpdate, conn: Conn, principal: Admin):
    _operation(principal, "users.manage")
    store = AdminStore(conn, principal)
    row = store.update_user(user_id, role=body.role, status=body.status)
    return _user(store.user(user_id) | {"role": row["role"], "status": row["status"]})


@router.post("/users/{user_id}/revoke-sessions", response_model=AdminRevokedSessions)
def revoke_user_sessions(user_id: UUID, conn: Conn, principal: Admin):
    store = AdminStore(conn, principal)
    row = store.user(user_id)
    return AdminRevokedSessions(revoked=store.revoke_sessions(row["workspace_key"]))


@router.get("/audit", response_model=list[AdminAuditEntry])
def audit(
    response: Response,
    conn: Conn,
    principal: AuditorOrAdmin,
    action: str = "",
    target_type: str = "",
    target_id: str = "",
    actor: str = "",
    since: datetime | None = None,
    until: datetime | None = None,
    cursor: str = "",
    limit: int = Query(100, ge=1, le=500),
):
    _operation(principal, "audit.read")
    rows, next_cursor = AdminStore(conn, principal).audit_log(
        action=action, target_type=target_type, target_id=target_id,
        actor=actor, since=since, until=until, cursor=cursor, limit=limit,
    )
    if next_cursor:
        response.headers["X-Next-Cursor"] = next_cursor
    return [AdminAuditEntry(
        auditId=row["audit_id"], actorUserId=row["actor_user_id"],
        actorWorkspace=row["actor_workspace"], action=row["action"],
        targetType=row["target_type"], targetId=row["target_id"],
        detail=row["detail"], createdAt=iso(row["created_at"]),
        actorEmail=row["actor_email"], actorDisplayName=row["actor_display_name"],
    ) for row in rows]


@router.get(
    "/audit/export",
    response_class=Response,
    responses={
        200: {
            "description": "Exported audit records in CSV or JSON format.",
            "content": {
                "text/csv": {},
                "application/json": {},
            },
        }
    },
)
def export_audit(
    conn: Conn,
    principal: AuditorOrAdmin,
    format: str = Query("csv", pattern="^(csv|json)$"),
    action: str = "",
    target_type: str = "",
    target_id: str = "",
    actor: str = "",
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(5000, ge=1, le=10000),
):
    _operation(principal, "audit.export")
    store = AdminStore(conn, principal)
    content, media_type, snapshot_until = store.export_audit(
        format=format, action=action, target_type=target_type, target_id=target_id,
        actor=actor, since=since, until=until, limit=limit,
    )
    timestamp = snapshot_until.strftime("%Y%m%d-%H%M%S")
    extension = "csv" if format == "csv" else "json"
    filename = f"admin-audit-{timestamp}.{extension}"
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Snapshot-Until": snapshot_until.isoformat(),
        },
    )



@router.get("/conversations", response_model=list[AdminConversation])
def conversations(conn: Conn, principal: Admin, workspace: str = "", query: str = "",
                  limit: int = Query(50, ge=1, le=200), offset: int = 0):
    rows = AdminStore(conn, principal).conversations(
        workspace=workspace, query=query, limit=limit, offset=offset)
    return [_conversation(row) for row in rows]


@router.get("/conversations/{conversation_id}/transcript", response_model=AdminTranscript)
def transcript(conversation_id: UUID, conn: Conn, principal: Admin):
    value = AdminStore(conn, principal).transcript(conversation_id)
    return AdminTranscript(
        conversation=_conversation(value["conversation"]), turns=value["turns"],
    )


def _job(row) -> AdminJob:
    return AdminJob(
        jobId=row["job_id"], kind=row["kind"], scopeKey=row["scope_key"],
        status=row["status"], attemptCount=row["attempt_count"],
        maxAttempts=row["max_attempts"], cancelRequested=row["cancel_requested"],
        error=row["error"], createdAt=iso(row["created_at"]),
        updatedAt=iso(row["updated_at"]), finishedAt=iso(row["finished_at"]),
    )


@router.get("/jobs", response_model=list[AdminJob])
def jobs(conn: Conn, principal: Admin, status: str = "",
         limit: int = Query(100, ge=1, le=500), offset: int = 0):
    rows = AdminStore(conn, principal).jobs(status=status, limit=limit, offset=offset)
    return [_job(row) for row in rows]


@router.post("/jobs/{job_id}/cancel", response_model=AdminJob)
def cancel_job(job_id: UUID, conn: Conn, principal: Admin):
    return _job(AdminStore(conn, principal).cancel_job(job_id))


@router.get("/sources", response_model=list[AdminSource])
def sources(conn: Conn, principal: Admin, workspace: str = "",
            limit: int = Query(100, ge=1, le=500), offset: int = 0):
    rows = AdminStore(conn, principal).sources(workspace=workspace, limit=limit,
                                              offset=offset)
    return [AdminSource(
        sourceId=row["source_id"], collectionId=row["collection_id"],
        filename=row["filename"], contentType=row["content_type"], status=row["status"],
        workspaceKey=row["workspace_key"], indexRevision=row["index_revision"],
        isCurrent=row["is_current"], createdAt=iso(row["created_at"]),
        updatedAt=iso(row["updated_at"]),
    ) for row in rows]


@router.delete("/conversations/{conversation_id}", status_code=204, response_class=Response,
               responses={204: {"description": "Conversation transcript and turn jobs deleted."}})
def delete_conversation(conversation_id: UUID, conn: Conn, principal: Admin):
    AdminStore(conn, principal).delete_conversation(conversation_id)
    return Response(status_code=204)


@router.get("/artifacts", response_model=list[AdminArtifact])
def artifacts(conn: Conn, principal: Admin, limit: int = Query(100, ge=1, le=500),
              offset: int = 0):
    rows = AdminStore(conn, principal).artifacts(limit=limit, offset=offset)
    return [AdminArtifact(
        artifactId=row["artifact_id"], workspaceKey=row["workspace_key"],
        kind=row["kind"], headRevisionId=row["head_revision_id"],
        originalFilename=row["original_filename"], revisionCount=row["revision_count"],
        createdAt=iso(row["created_at"]),
    ) for row in rows]


def _collection(row) -> AdminCollection:
    return AdminCollection(
        collectionId=row["collection_id"], name=row["name"],
        sourceCount=row["source_count"], createdAt=iso(row["created_at"]),
    )


@router.get("/collections", response_model=list[AdminCollection])
def collections(conn: Conn, principal: Admin, limit: int = Query(100, ge=1, le=500),
                offset: int = 0):
    rows = AdminStore(conn, principal).collections(limit=limit, offset=offset)
    return [_collection(row) for row in rows]


@router.post("/collections", response_model=AdminCollection, status_code=201)
def create_collection(body: AdminCollectionWrite, conn: Conn, principal: Admin):
    _operation(principal, "collections.manage")
    return _collection(AdminStore(conn, principal).create_collection(body.name))


@router.patch("/collections/{collection_id}", response_model=AdminCollection)
def rename_collection(collection_id: UUID, body: AdminCollectionWrite,
                      request: Request, conn: Conn, principal: Admin):
    return _collection(AdminStore(conn, principal).rename_collection(
        collection_id, body.name,
        protected_name=request.app.state.settings.default_collection))


@router.delete("/collections/{collection_id}", status_code=204, response_class=Response,
               responses={204: {"description": "Empty collection deleted."}})
def delete_collection(collection_id: UUID, request: Request, conn: Conn, principal: Admin):
    AdminStore(conn, principal).delete_collection(
        collection_id, protected_name=request.app.state.settings.default_collection)
    return Response(status_code=204)


@router.get("/telemetry/usage", response_model=TelemetryUsageResponse)
def telemetry_usage(
    conn: Conn,
    principal: AuditorOrAdmin,
    since: datetime | None = None,
    until: datetime | None = None,
    provider: str = "",
    model_id: str = "",
    group_by: str = Query("date", pattern="^(date|model|operation)$"),
    limit: int = Query(100, ge=1, le=500),
):
    _operation(principal, "telemetry.view")
    summary = PricingService.telemetry_summary(
        conn, since=since, until=until, provider=provider, model_id=model_id,
    )
    breakdown = PricingService.telemetry_breakdown(
        conn,
        group_by=group_by,
        since=since,
        until=until,
        provider=provider,
        model_id=model_id,
        limit=limit,
    )
    return {"summary": summary, "breakdown": breakdown}


@router.get("/pricing/schedules", response_model=list[ModelPriceSchedule])
def list_price_schedules(conn: Conn, principal: AuditorOrAdmin):
    return PricingService.list_schedules(conn)


@router.post("/pricing/schedules", response_model=ModelPriceSchedule, status_code=201)
def create_price_schedule(body: ModelPriceScheduleCreate, conn: Conn, principal: Admin):
    try:
        effective_from = datetime.fromisoformat(body.effectiveFrom)
        effective_to = datetime.fromisoformat(body.effectiveTo) if body.effectiveTo else None
    except ValueError as exc:
        raise HTTPException(422, "price schedule dates must be ISO timestamps") from exc
    schedule = PricingService.create_schedule(
        conn,
        provider=body.provider,
        model_id=body.modelId,
        currency=body.currency or "USD",
        billing_mode=body.billingMode,
        input_rate_per_million=body.inputRatePerMillion,
        cached_read_rate_per_million=body.cachedReadRatePerMillion or 0,
        output_rate_per_million=body.outputRatePerMillion,
        effective_from=effective_from,
        effective_to=effective_to,
        provenance=body.provenance,
    )
    store = AdminStore(conn, principal)
    store.record_audit(
        "create_price_schedule",
        "model_price_schedule",
        str(schedule["scheduleId"]),
        {"provider": body.provider, "model_id": body.modelId},
    )
    conn.commit()
    return schedule


def _grant(row) -> ResourceGrant:
    return ResourceGrant(
        grantId=row["grant_id"],
        grantType=row["grant_type"],
        actorUserId=row["actor_user_id"],
        targetWorkspaceKey=row["target_workspace_key"],
        resourceType=row["resource_type"],
        resourceId=row.get("resource_id"),
        allowedActions=row["allowed_actions"],
        reason=row["reason"],
        approvedByUserId=row["approved_by_user_id"],
        expiresAt=iso(row["expires_at"]),
        createdAt=iso(row["created_at"]),
        revokedAt=iso(row.get("revoked_at")),
        revokedByUserId=row.get("revoked_by_user_id"),
        actorEmail=row.get("actor_email"),
        actorDisplayName=row.get("actor_display_name"),
        approvedByEmail=row.get("approved_by_email"),
        approvedByDisplayName=row.get("approved_by_display_name"),
    )


@router.get("/grants", response_model=list[ResourceGrant])
def list_grants(
    conn: Conn,
    principal: Admin,
    target_workspace: str = "",
    actor_user_id: UUID | None = None,
    grant_type: str = "",
    active_only: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = 0,
):
    rows = AdminStore(conn, principal).list_grants(
        target_workspace_key=target_workspace,
        actor_user_id=actor_user_id,
        grant_type=grant_type,
        active_only=active_only,
        limit=limit,
        offset=offset,
    )
    return [_grant(row) for row in rows]


@router.post("/grants", response_model=ResourceGrant, status_code=201)
def create_grant(body: ResourceGrantCreate, conn: Conn, principal: Admin):
    _operation(principal, "grants.approve")
    expires_at = _parse_timestamp(body.expiresAt, "expiresAt")
    row = AdminStore(conn, principal).create_grant(
        grant_type=body.grantType,
        actor_user_id=body.actorUserId,
        target_workspace_key=body.targetWorkspaceKey,
        resource_type=body.resourceType,
        resource_id=body.resourceId,
        reason=body.reason,
        expires_at=expires_at,
        allowed_actions=body.allowedActions,
    )
    return _grant(row)


@router.post("/grants/{grant_id}/revoke", response_model=ResourceGrant)
def revoke_grant(grant_id: UUID, conn: Conn, principal: Admin):
    _operation(principal, "grants.revoke")
    row = AdminStore(conn, principal).revoke_grant(grant_id)
    return _grant(row)


def _support_request(row) -> SupportGrantRequest:
    return SupportGrantRequest(
        requestId=row["request_id"],
        requesterUserId=row["requester_user_id"],
        actorUserId=row["actor_user_id"],
        targetWorkspaceKey=row["target_workspace_key"],
        resourceType=row["resource_type"],
        resourceId=row.get("resource_id"),
        allowedActions=row["allowed_actions"],
        reason=row["reason"],
        expiresAt=iso(row["expires_at"]),
        status=row["status"],
        revision=row["revision"],
        decisionNote=row.get("decision_note"),
        decidedByUserId=row.get("decided_by_user_id"),
        decidedAt=iso(row.get("decided_at")),
        grantId=row.get("grant_id"),
        createdAt=iso(row["created_at"]),
        updatedAt=iso(row["updated_at"]),
        actorEmail=row.get("actor_email"),
        actorDisplayName=row.get("actor_display_name"),
    )


@router.get("/grant-requests", response_model=list[SupportGrantRequest])
def list_grant_requests(
    conn: Conn,
    principal: Admin,
    status: str = "",
    limit: int = Query(50, ge=1, le=200),
    offset: int = 0,
):
    _operation(principal, "grants.request")
    rows = AdminStore(conn, principal).list_support_grant_requests(
        status=status, limit=limit, offset=offset,
    )
    return [_support_request(row) for row in rows]


@router.post("/grant-requests", response_model=SupportGrantRequest, status_code=201)
def create_grant_request(body: SupportGrantRequestCreate, conn: Conn, principal: Admin):
    _operation(principal, "grants.request")
    row = AdminStore(conn, principal).request_support_grant(
        target_workspace_key=body.targetWorkspaceKey,
        resource_type=body.resourceType,
        resource_id=body.resourceId,
        reason=body.reason,
        expires_at=_parse_timestamp(body.expiresAt, "expiresAt"),
    )
    return _support_request(row)


@router.patch("/grant-requests/{request_id}", response_model=SupportGrantRequest)
def update_grant_request(
    request_id: UUID, body: SupportGrantRequestUpdate, conn: Conn, principal: Admin,
):
    _operation(principal, "grants.request")
    row = AdminStore(conn, principal).update_support_grant_request(
        request_id,
        target_workspace_key=body.targetWorkspaceKey,
        resource_type=body.resourceType,
        resource_id=body.resourceId,
        reason=body.reason,
        expires_at=_parse_timestamp(body.expiresAt, "expiresAt"),
        expected_revision=body.expectedRevision,
    )
    return _support_request(row)


@router.post("/grant-requests/{request_id}/approve", response_model=SupportGrantRequest)
def approve_grant_request(
    request_id: UUID, body: SupportGrantRequestDecision, conn: Conn, principal: Admin,
):
    _operation(principal, "grants.approve")
    row = AdminStore(conn, principal).approve_support_grant_request(
        request_id, expected_revision=body.expectedRevision,
    )
    return _support_request(row)


@router.post("/grant-requests/{request_id}/reject", response_model=SupportGrantRequest)
def reject_grant_request(
    request_id: UUID, body: SupportGrantRequestDecision, conn: Conn, principal: Admin,
):
    _operation(principal, "grants.approve")
    row = AdminStore(conn, principal).reject_support_grant_request(
        request_id, expected_revision=body.expectedRevision, note=body.note or "",
    )
    return _support_request(row)


@router.get(
    "/conversations/{conversation_id}/transcript/download",
    response_class=Response,
    responses={
        200: {
            "description": "Owned conversation transcript as plain text.",
            "content": {"text/plain": {}},
        }
    },
)
def download_transcript(conversation_id: UUID, conn: Conn, principal: Admin):
    body = AdminStore(conn, principal).transcript_download(conversation_id)
    return Response(
        content=body,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="transcript.txt"'},
    )


@router.post(
    "/conversations/{conversation_id}/granted-results",
    response_model=GrantedResultStaging,
    status_code=201,
)
def stage_granted_result(conversation_id: UUID, conn: Conn, principal: Admin):
    row = AdminStore(conn, principal).stage_granted_transcript(conversation_id)
    return GrantedResultStaging(
        stagingId=row["staging_id"],
        grantId=row["grant_id"],
        status=row["status"],
        resourceId=row["resource_id"],
    )


@router.post(
    "/granted-results/{staging_id}/publish",
    response_class=Response,
    responses={
        200: {
            "description": "Published granted transcript as plain text.",
            "content": {"text/plain": {}},
        }
    },
)
def publish_granted_result(staging_id: UUID, conn: Conn, principal: Admin):
    body = AdminStore(conn, principal).publish_granted_result(staging_id)
    return Response(content=body, media_type="text/plain; charset=utf-8")


@router.get("/alerts", response_model=list[AlertStatusResponse], response_model_exclude_unset=True)
def list_alerts(
    conn: Conn,
    _principal: AuditorOrAdmin,
    request: Request,
) -> list[dict]:
    from app.observability.alerts import AlertManager
    return AlertManager.evaluate_all(conn, request.app.state)


def _proposal(row: dict) -> SourceReviewProposal:
    return SourceReviewProposal(
        proposalId=row["proposal_id"],
        sourceId=row["source_id"],
        expectedMetadataRevision=row["expected_metadata_revision"],
        proposedByUserId=row.get("proposed_by_user_id"),
        status=row["status"],
        sourceCategory=row["source_category"],
        classificationOrigin=row["classification_origin"],
        publisher=row.get("publisher"),
        sourceUrl=row.get("source_url"),
        edition=row.get("edition"),
        geography=row.get("geography"),
        occupationId=row.get("occupation_id"),
        publishedAt=iso(row.get("published_at")),
        acquiredAt=iso(row.get("acquired_at")),
        attribution=row.get("attribution"),
        title=row.get("title"),
        institution=row.get("institution"),
        issuingUnit=row.get("issuing_unit"),
        documentRoles=row.get("document_roles") or [],
        applicability=row.get("applicability") or {},
        applicabilityStatus=row["applicability_status"],
        lifecycleStatus=row["lifecycle_status"],
        replacementIntent=row.get("replacement_intent"),
        reviewNotes=row.get("review_notes"),
        rejectionReason=row.get("rejection_reason"),
        reviewedByUserId=row.get("reviewed_by_user_id"),
        reviewedAt=iso(row.get("reviewed_at")),
        createdAt=iso(row["created_at"]),
        updatedAt=iso(row["updated_at"]),
    )


def _lifecycle_event(row: dict) -> SourceLifecycleEvent:
    return SourceLifecycleEvent(
        eventId=row["event_id"],
        sourceId=row["source_id"],
        eventType=row["event_type"],
        proposalId=row.get("proposal_id"),
        metadataRevision=row["metadata_revision"],
        actorUserId=row.get("actor_user_id"),
        details=row.get("details") or {},
        createdAt=iso(row["created_at"]),
    )


@router.get("/sources/{source_id}/review", response_model=SourceReviewDetailResponse)
def get_source_review_detail(source_id: UUID, conn: Conn, _principal: Admin) -> SourceReviewDetailResponse:
    data = SourceReviewService.get_source_detail(conn, source_id)
    source_row = data["source"]
    serialized_source = {
        "sourceId": str(source_row["source_id"]),
        "collectionId": str(source_row["collection_id"]),
        "filename": source_row["filename"],
        "contentType": source_row["content_type"],
        "fileHash": source_row["file_hash"],
        "status": source_row["status"],
        "workspaceKey": source_row.get("workspace_key"),
        "indexRevision": source_row["index_revision"],
        "isCurrent": source_row["is_current"],
        "originKey": source_row.get("origin_key"),
        "createdAt": iso(source_row["created_at"]),
        "updatedAt": iso(source_row["updated_at"]),
        "sourceCategory": source_row.get("source_category"),
        "classificationOrigin": source_row.get("classification_origin"),
        "publisher": source_row.get("publisher"),
        "sourceUrl": source_row.get("source_url"),
        "edition": source_row.get("edition"),
        "geography": source_row.get("geography"),
        "occupationId": source_row.get("occupation_id"),
        "publishedAt": iso(source_row.get("published_at")),
        "acquiredAt": iso(source_row.get("acquired_at")),
        "attribution": source_row.get("attribution"),
        "title": source_row.get("title"),
        "institution": source_row.get("institution"),
        "issuingUnit": source_row.get("issuing_unit"),
        "documentRoles": source_row.get("document_roles") or [],
        "applicability": source_row.get("applicability") or {},
        "applicabilityStatus": source_row.get("applicability_status") or "unknown",
        "metadataRevision": source_row.get("metadata_revision") or 1,
        "lifecycleStatus": source_row.get("lifecycle_status") or "active",
    }
    pending = _proposal(data["pendingProposal"]) if data.get("pendingProposal") else None
    proposals = [_proposal(p) for p in data.get("proposals", [])]
    events = [_lifecycle_event(e) for e in data.get("lifecycleEvents", [])]
    deps_data = data["dependencies"]
    deps = SourceDependencies(
        programCount=deps_data["programCount"],
        courseCount=deps_data["courseCount"],
        gapCount=deps_data["gapCount"],
        researchRunCount=deps_data["researchRunCount"],
        programs=deps_data.get("programs", []),
    )
    return SourceReviewDetailResponse(
        source=serialized_source,
        pendingProposal=pending,
        proposals=proposals,
        lifecycleEvents=events,
        dependencies=deps,
    )


@router.get("/sources/{source_id}/impact", response_model=SourceImpactPreviewResponse)
def get_source_impact_preview(source_id: UUID, conn: Conn, _principal: Admin) -> SourceImpactPreviewResponse:
    preview = SourceReviewService.get_impact_preview(conn, source_id)
    return SourceImpactPreviewResponse(
        sourceId=preview["sourceId"],
        filename=preview["filename"],
        programCount=preview["programCount"],
        courseCount=preview["courseCount"],
        gapCount=preview["gapCount"],
        researchRunCount=preview["researchRunCount"],
        programs=preview.get("programs", []),
    )


@router.post("/sources/{source_id}/proposals", response_model=SourceReviewProposal, status_code=201)
def create_source_review_proposal(
    source_id: UUID,
    body: SourceReviewProposalCreate,
    conn: Conn,
    principal: Admin,
) -> SourceReviewProposal:
    _operation(principal, "sources.review")
    proposal = SourceReviewService.create_proposal(
        conn,
        source_id=source_id,
        actor_user_id=principal.user_id,
        expected_metadata_revision=body.expectedMetadataRevision,
        raw_proposal=body.model_dump(),
    )
    return _proposal(proposal)


@router.post("/sources/{source_id}/proposals/{proposal_id}/approve", response_model=SourceReviewProposal)
def approve_source_review_proposal(
    source_id: UUID,
    proposal_id: UUID,
    conn: Conn,
    principal: Admin,
) -> SourceReviewProposal:
    _operation(principal, "sources.review")
    proposal = SourceReviewService.approve_proposal(
        conn,
        source_id=source_id,
        proposal_id=proposal_id,
        reviewer_user_id=principal.user_id,
    )
    return _proposal(proposal)


@router.post("/sources/{source_id}/proposals/{proposal_id}/reject", response_model=SourceReviewProposal)
def reject_source_review_proposal(
    source_id: UUID,
    proposal_id: UUID,
    body: SourceReviewProposalReject,
    conn: Conn,
    principal: Admin,
) -> SourceReviewProposal:
    _operation(principal, "sources.review")
    proposal = SourceReviewService.reject_proposal(
        conn,
        source_id=source_id,
        proposal_id=proposal_id,
        reviewer_user_id=principal.user_id,
        reason=body.reason,
    )
    return _proposal(proposal)


@router.post("/sources/{source_id}/lifecycle", response_model=SourceLifecycleChangeResponse)
def change_source_lifecycle_status(
    source_id: UUID,
    body: SourceLifecycleChangeRequest,
    conn: Conn,
    principal: Admin,
) -> SourceLifecycleChangeResponse:
    _operation(principal, "sources.review")
    row = SourceReviewService.change_lifecycle_status(
        conn,
        source_id=source_id,
        actor_user_id=principal.user_id,
        action=body.action,
        expected_metadata_revision=body.expectedMetadataRevision,
        notes=body.notes,
    )
    return {
        "sourceId": str(source_id),
        "lifecycleStatus": row["lifecycle_status"],
        "metadataRevision": row["metadata_revision"],
    }


@router.get("/sources/{source_id}/evidence", response_model=SourceRevisionEvidenceResponse)
def get_source_revision_evidence(
    source_id: UUID,
    conn: Conn,
    principal: Admin,
    index_revision: int = Query(ge=0),
) -> SourceRevisionEvidenceResponse:
    """Return the text stored for one indexed revision, including a replaced one."""
    _operation(principal, "sources.review")
    source = conn.execute(
        queries.get("get_source_indexed_revision"), {"source_id": source_id},
    ).fetchone()
    if source is None:
        raise HTTPException(404, "source was not found")
    if source["indexed_content_revision"] == index_revision:
        preserve_indexed_source_evidence(conn, source_id)
        conn.commit()
    rows = conn.execute(queries.get("list_source_revision_evidence"), {
        "source_id": source_id, "index_revision": index_revision,
    }).fetchall()
    return SourceRevisionEvidenceResponse(
        sourceId=source_id,
        indexRevision=index_revision,
        passages=[SourceRevisionPassage(
            chunkId=row["chunk_id"],
            chunkIndex=row["chunk_index"],
            locatorLabel=row["locator_label"],
            text=row["text"],
            textDigest=row["text_digest"],
        ) for row in rows],
    )


def _rule_release_summary(row: dict) -> ProgramRuleReleaseSummary:
    return ProgramRuleReleaseSummary(
        releaseId=row["release_id"],
        programId=row["program_id"],
        collectionId=row["collection_id"],
        sourceId=row["source_id"],
        sourceIndexRevision=row["source_index_revision"],
        sourceSha256=row["source_sha256"],
        releaseVersion=row["release_version"],
        status=row["status"],
        rulesDigest=row["rules_digest"],
        coverageSummary=row.get("coverage_summary") or {},
        createdByUserId=row.get("created_by_user_id"),
        approvedByUserId=row.get("approved_by_user_id"),
        approvedAt=iso(row.get("approved_at")),
        notes=row.get("notes"),
        createdAt=iso(row["created_at"]),
        updatedAt=iso(row["updated_at"]),
    )


def _rule_release_detail(row: dict) -> ProgramRuleReleaseDetail:
    return ProgramRuleReleaseDetail(
        releaseId=row["release_id"],
        programId=row["program_id"],
        collectionId=row["collection_id"],
        sourceId=row["source_id"],
        sourceIndexRevision=row["source_index_revision"],
        sourceSha256=row["source_sha256"],
        releaseVersion=row["release_version"],
        status=row["status"],
        rulesTree=row["rules_tree"],
        rulesDigest=row["rules_digest"],
        coverageSummary=row.get("coverage_summary") or {},
        createdByUserId=row.get("created_by_user_id"),
        approvedByUserId=row.get("approved_by_user_id"),
        approvedAt=iso(row.get("approved_at")),
        notes=row.get("notes"),
        createdAt=iso(row["created_at"]),
        updatedAt=iso(row["updated_at"]),
    )


@router.get("/programs/{program_id}/inspection", response_model=ProgramInspectionResponse)
def get_program_inspection(program_id: UUID, conn: Conn, _principal: Admin) -> ProgramInspectionResponse:
    data = ProgramReviewService.get_program_inspection(conn, program_id)
    raw_prog = data["program"]
    prog_dict = {
        "programId": str(raw_prog["program_id"]),
        "collectionId": str(raw_prog["collection_id"]),
        "title": raw_prog["title"],
        "kind": raw_prog["kind"],
        "catalog": raw_prog["catalog"],
        "sourceUrl": raw_prog["source_url"],
        "firstPage": raw_prog["first_page"],
        "lastPage": raw_prog["last_page"],
        "parserVersion": raw_prog["parser_version"],
        "sourceId": str(raw_prog["source_id"]) if raw_prog.get("source_id") else None,
        "sourceIndexRevision": raw_prog.get("source_index_revision"),
        "sourceSha256": raw_prog.get("source_sha256"),
        "originKey": raw_prog.get("origin_key"),
        "bindingStatus": raw_prog["binding_status"],
        "boundAt": iso(raw_prog.get("bound_at")),
        "currentRuleReleaseId": str(raw_prog["current_rule_release_id"]) if raw_prog.get("current_rule_release_id") else None,
        "degree": raw_prog.get("degree"),
        "hours": raw_prog.get("hours"),
        "coursesRequired": raw_prog.get("courses_required"),
        "department": raw_prog.get("department"),
        "sourceFilename": raw_prog.get("source_filename"),
        "sourceIsCurrent": raw_prog.get("source_is_current"),
        "sourceStatus": raw_prog.get("source_status"),
        "sourceCategory": raw_prog.get("source_category"),
        "sourceMetadataRevision": raw_prog.get("source_metadata_revision"),
        "sourceLifecycleStatus": raw_prog.get("source_lifecycle_status"),
    }
    items = [
        ProgramRawItem(
            position=it["position"],
            section=it.get("section"),
            text=it["text"],
            choose=it.get("choose"),
            mentions=it.get("mentions") or [],
            evidence=it.get("evidence") or [],
        )
        for it in data.get("items", [])
    ]
    releases = [_rule_release_summary(r) for r in data.get("releases", [])]
    current_rel = _rule_release_detail(data["currentRelease"]) if data.get("currentRelease") else None
    return ProgramInspectionResponse(
        program=prog_dict,
        items=items,
        releases=releases,
        currentRelease=current_rel,
    )


@router.post("/programs/{program_id}/rules/draft", response_model=ProgramRuleReleaseDetail, status_code=201)
def create_program_rule_draft(
    program_id: UUID,
    body: ProgramRuleDraftCreate,
    conn: Conn,
    principal: Admin,
) -> ProgramRuleReleaseDetail:
    _operation(principal, "programs.review")
    draft = ProgramReviewService.create_rule_draft(
        conn,
        program_id=program_id,
        created_by_user_id=principal.user_id,
        rules_tree=body.rulesTree,
        notes=body.notes,
    )
    return _rule_release_detail(draft)


@router.post("/programs/{program_id}/rules/{release_id}/approve", response_model=ProgramRuleReleaseDetail)
def approve_program_rule_release(
    program_id: UUID,
    release_id: UUID,
    body: ProgramRuleReviewAction,
    conn: Conn,
    principal: Admin,
) -> ProgramRuleReleaseDetail:
    _operation(principal, "programs.review")
    rel = ProgramReviewService.approve_rule_release(
        conn,
        release_id=release_id,
        reviewer_user_id=principal.user_id,
        notes=body.notes,
        expected_program_id=program_id,
    )
    return _rule_release_detail(rel)


@router.post("/programs/{program_id}/rules/{release_id}/publish", response_model=ProgramRuleReleaseDetail)
def publish_program_rule_release(
    program_id: UUID,
    release_id: UUID,
    body: ProgramRuleReviewAction,
    conn: Conn,
    principal: Admin,
) -> ProgramRuleReleaseDetail:
    _operation(principal, "programs.review")
    rel = ProgramReviewService.publish_rule_release(
        conn,
        release_id=release_id,
        reviewer_user_id=principal.user_id,
        notes=body.notes,
        expected_program_id=program_id,
    )
    return _rule_release_detail(rel)


@router.post("/programs/{program_id}/rules/{release_id}/reject", response_model=ProgramRuleReleaseDetail)
def reject_program_rule_release(
    program_id: UUID,
    release_id: UUID,
    body: ProgramRuleRejectAction,
    conn: Conn,
    principal: Admin,
) -> ProgramRuleReleaseDetail:
    _operation(principal, "programs.review")
    rel = ProgramReviewService.reject_rule_release(
        conn,
        release_id=release_id,
        reviewer_user_id=principal.user_id,
        reason=body.reason,
        expected_program_id=program_id,
    )
    return _rule_release_detail(rel)


@router.get("/programs/{program_id}/rules/diff", response_model=ProgramRuleDiffResponse)
def diff_program_rule_releases(
    program_id: UUID,
    release_id_a: UUID,
    release_id_b: UUID,
    conn: Conn,
    _principal: Admin,
) -> ProgramRuleDiffResponse:
    diff = ProgramReviewService.diff_rule_releases(
        conn,
        program_id=program_id,
        release_id_a=release_id_a,
        release_id_b=release_id_b,
    )
    return ProgramRuleDiffResponse(
        programId=diff["programId"],
        releaseIdA=diff["releaseIdA"],
        versionA=diff["versionA"],
        releaseIdB=diff["releaseIdB"],
        versionB=diff["versionB"],
        addedNodeIds=diff["addedNodeIds"],
        removedNodeIds=diff["removedNodeIds"],
        modifiedNodes=diff["modifiedNodes"],
        digestMatch=diff["digestMatch"],
    )


def _edition_item(row: dict | None) -> CatalogEditionItem | None:
    if row is None:
        return None
    return CatalogEditionItem(
        editionId=row["edition_id"],
        revision=row["revision"],
        editionKey=row["edition_key"],
        isCurrent=row["is_current"],
        topicBuildId=row["topic_build_id"],
        sourceIds=row["source_ids"],
    )


def _refresh_batch(data: dict) -> CatalogRefreshBatchResponse:
    return CatalogRefreshBatchResponse(
        batchId=data["batch_id"],
        collectionId=data["collection_id"],
        editionKey=data["edition_key"],
        status=data["status"],
        baseRevision=data["base_revision"],
        stagedRevision=data["staged_revision"],
        compatible=data["compatible"],
        currentEdition=_edition_item(data["current_edition"]),
        stagedEdition=_edition_item(data["staged_edition"]),
    )


@router.get("/catalog-refresh", response_model=CatalogRefreshStatusResponse)
def catalog_refresh_status(collectionId: UUID, conn: Conn, principal: Admin):
    _operation(principal, "sources.review")
    data = CatalogRefreshService.current(conn, collectionId, principal)
    conn.commit()
    return CatalogRefreshStatusResponse(
        currentEdition=_edition_item(data["current_edition"]),
        staging=_refresh_batch(data["staging"]) if data["staging"] else None,
    )


@router.post("/catalog-refresh", response_model=CatalogRefreshBatchResponse, status_code=201)
def start_catalog_refresh(body: CatalogRefreshStartRequest, conn: Conn, principal: Admin):
    _operation(principal, "sources.review")
    data = CatalogRefreshService.start(
        conn, body.collectionId, body.editionKey, body.compatible, principal,
    )
    conn.commit()
    return _refresh_batch(data)


@router.post("/catalog-refresh/{batch_id}/cancel", response_model=CatalogRefreshBatchResponse)
def cancel_catalog_refresh(batch_id: UUID, conn: Conn, principal: Admin):
    _operation(principal, "sources.review")
    data = CatalogRefreshService.cancel(conn, batch_id, principal)
    conn.commit()
    return _refresh_batch(data)


@router.post("/catalog-refresh/{batch_id}/publish", response_model=CatalogRefreshBatchResponse)
def publish_catalog_refresh(
    batch_id: UUID, body: CatalogRefreshPublishRequest, conn: Conn, principal: Admin,
):
    _operation(principal, "sources.review")
    data = CatalogRefreshService.publish(conn, batch_id, body.expectedRevision, principal)
    conn.commit()
    return _refresh_batch(data)


# ---- Reviewed course maintenance (phase B) ---------------------------------


def _admin_course(row: dict) -> AdminCourse:
    return AdminCourse(
        courseId=row["course_id"],
        code=row["code"],
        subject=row["subject"],
        title=row["title"],
        canonicalCode=row.get("canonical_code"),
        parseStatus=row["parse_status"],
        lifecycle=row["lifecycle"],
        authority=row["authority"],
        adminRevision=row["admin_revision"],
        requisitesRaw=row.get("requisites_raw"),
        oxford=bool(row.get("oxford")),
        mentionCount=row.get("mention_count") or 0,
        unresolvedCount=row.get("unresolved_count") or 0,
        programCount=row.get("program_count") or 0,
        filename=row.get("filename"),
        contentType=row.get("content_type"),
        originKey=row.get("origin_key"),
        indexRevision=row.get("index_revision"),
        sourceStatus=row.get("source_status"),
        sourceIsCurrent=row.get("source_is_current"),
        publisher=row.get("publisher"),
        sourceUrl=row.get("source_url"),
        sourceTitle=row.get("source_title"),
        institution=row.get("institution"),
        issuingUnit=row.get("issuing_unit"),
        documentRoles=row.get("document_roles") or [],
        applicability=row.get("applicability") or {},
        applicabilityStatus=row.get("applicability_status"),
    )


def _admin_course_change(row: dict) -> AdminCourseChange:
    return AdminCourseChange(
        changeId=row["change_id"],
        code=row["code"],
        changeKind=row["change_kind"],
        status=row["status"],
        expectedRevision=row["expected_revision"],
        publishedRevision=row.get("published_revision"),
        baseAuthority=row.get("base_authority"),
        baseValue=row.get("base_value") or {},
        proposedValue=row.get("proposed_value") or {},
        reason=row["reason"],
        actorUserId=row.get("actor_user_id"),
        publishedByUserId=row.get("published_by_user_id"),
        conflictNote=row.get("conflict_note"),
        createdAt=iso(row["created_at"]),
        updatedAt=iso(row["updated_at"]),
        publishedAt=iso(row.get("published_at")),
    )


def _admin_course_dependent(row: dict) -> AdminCourseDependent:
    return AdminCourseDependent(
        courseId=row["course_id"],
        code=row["code"],
        title=row["title"],
        kind=row["kind"],
        targetCode=row["target_code"],
        resolved=bool(row["resolved"]),
    )


def _admin_course_program(row: dict) -> AdminCourseProgramReference:
    return AdminCourseProgramReference(
        programId=row["program_id"], title=row["title"], kind=row["kind"]
    )


def _course_state(row: dict | None) -> dict | None:
    if row is None:
        return None
    return {
        "courseId": str(row["course_id"]),
        "code": row["code"],
        "title": row["title"],
        "requisitesRaw": row["requisites_raw"],
        "parseStatus": row["parse_status"],
        "lifecycle": row["lifecycle"],
        "authority": row["authority"],
        "adminRevision": row["admin_revision"],
        "sourceId": str(row["source_id"]),
    }


def _course_collection(request: Request, conn, collection_id: UUID | None) -> UUID:
    if collection_id is not None:
        return collection_id
    resolved, _name = deps.resolve_collection(conn, request.app.state.settings, None)
    return resolved


@router.get("/courses", response_model=AdminCourseListResponse, response_model_exclude_unset=True)
def list_admin_courses(
    request: Request,
    conn: Conn,
    principal: AuditorOrAdmin,
    collection_id: UUID | None = None,
    query: str = "",
    subject: str = "",
    lifecycle: str = "",
    authority: str = "",
    campus: str = "",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> AdminCourseListResponse:
    _operation(principal, "courses.read")
    data = CourseMaintenanceService.list_courses(
        conn,
        collection_id=_course_collection(request, conn, collection_id),
        query=query,
        subject=subject,
        lifecycle=lifecycle,
        authority=authority,
        campus=campus,
        limit=limit,
        offset=offset,
    )
    return AdminCourseListResponse(
        courses=[_admin_course(row) for row in data["courses"]],
        total=data["total"],
        subjects=data["subjects"],
    )


@router.get("/courses/{code}", response_model=AdminCourseDetail, response_model_exclude_unset=True)
def get_admin_course(
    code: str, request: Request, conn: Conn, principal: AuditorOrAdmin,
    collection_id: UUID | None = None,
) -> AdminCourseDetail:
    _operation(principal, "courses.read")
    data = CourseMaintenanceService.get_course(
        conn, collection_id=_course_collection(request, conn, collection_id), code=code,
    )
    return AdminCourseDetail(
        course=_admin_course(data["course"]),
        mentions=[AdminCourseMention(**m) for m in data["mentions"]],
        nodes=[
            AdminCourseNode(
                nodeId=str(n["node_id"]),
                kind=n["kind"],
                parentNodeId=str(n["parent_node_id"]) if n["parent_node_id"] else None,
                position=n["position"],
                nodeType=n["node_type"],
                targetCode=n["target_code"],
                conditionText=n["condition_text"],
            )
            for n in data["nodes"]
        ],
        programs=[_admin_course_program(p) for p in data["programs"]],
        dependents=[_admin_course_dependent(d) for d in data["dependents"]],
        changes=[_admin_course_change(c) for c in data["changes"]],
        currentCodes=data["currentCodes"],
    )


@router.get(
    "/course-changes/conflicts",
    response_model=list[AdminCourseConflict],
    response_model_exclude_unset=True,
)
def list_course_conflicts(
    request: Request, conn: Conn, principal: AuditorOrAdmin,
    collection_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> list[AdminCourseConflict]:
    _operation(principal, "courses.read")
    rows = CourseMaintenanceService.list_conflicts(
        conn,
        collection_id=_course_collection(request, conn, collection_id),
        limit=limit,
    )
    return [
        AdminCourseConflict(
            changeId=row["change_id"],
            code=row["code"],
            changeKind=row["change_kind"],
            status=row["status"],
            conflictNote=row.get("conflict_note"),
            createdAt=iso(row["created_at"]),
        )
        for row in rows
    ]


@router.post("/course-changes", response_model=AdminCourseChange, status_code=201)
def create_course_change(
    body: AdminCourseChangeCreate, request: Request, conn: Conn, principal: Admin,
    collection_id: UUID | None = None,
) -> AdminCourseChange:
    _operation(principal, "courses.manage")
    change = CourseMaintenanceService.create_change(
        conn,
        collection_id=_course_collection(request, conn, collection_id),
        payload=body.model_dump(),
        actor_user_id=principal.user_id,
    )
    AdminStore(conn, principal).record_audit(
        "course.change_created", "course", change["code"],
        {"change_id": str(change["change_id"]), "kind": change["change_kind"]},
    )
    conn.commit()
    return _admin_course_change(change)


@router.get(
    "/course-changes/{change_id}/preview",
    response_model=AdminCoursePreviewResponse,
    response_model_exclude_unset=True,
)
def preview_course_change(
    change_id: UUID, conn: Conn, principal: AuditorOrAdmin,
) -> AdminCoursePreviewResponse:
    _operation(principal, "courses.read")
    data = CourseMaintenanceService.preview_change(conn, change_id=change_id)
    parsed = data["parsed"]
    return AdminCoursePreviewResponse(
        change=_admin_course_change(data["change"]),
        current=_course_state(data["current"]),
        changedFields=data["changedFields"],
        parsed=(
            AdminCourseParsedRequisites(
                status=parsed["status"],
                parseStatus=parsed["parseStatus"],
                mentions=[
                    AdminCourseParsedMention(
                        kind=m["kind"], targetCode=m["targetCode"], resolved=m["resolved"]
                    )
                    for m in parsed["mentions"]
                ],
                unresolved=parsed["unresolved"],
                leftover=parsed["leftover"],
            )
            if parsed is not None
            else None
        ),
        programs=[_admin_course_program(p) for p in data["programs"]],
        dependents=[_admin_course_dependent(d) for d in data["dependents"]],
        staleAuditCount=data["staleAuditCount"],
        activeTopicBuilds=data["activeTopicBuilds"],
        warnings=data["warnings"],
    )


@router.post("/course-changes/{change_id}/publish", response_model=AdminCoursePublishResponse)
def publish_course_change(
    change_id: UUID, conn: Conn, principal: Admin,
) -> AdminCoursePublishResponse:
    _operation(principal, "courses.manage")
    data = CourseMaintenanceService.publish_change(
        conn, change_id=change_id, actor_user_id=principal.user_id
    )
    AdminStore(conn, principal).record_audit(
        "course.change_published", "course", data["change"]["code"],
        {
            "change_id": str(change_id),
            "kind": data["change"]["change_kind"],
            "revision": data["change"].get("published_revision"),
        },
    )
    conn.commit()
    return AdminCoursePublishResponse(change=_admin_course_change(data["change"]))


@router.post("/course-changes/{change_id}/reject", response_model=AdminCourseChange)
def reject_course_change(
    change_id: UUID, body: AdminCourseChangeReject, conn: Conn, principal: Admin,
) -> AdminCourseChange:
    _operation(principal, "courses.manage")
    CourseMaintenanceService.reject_change(
        conn, change_id=change_id, actor_user_id=principal.user_id, reason=body.reason
    )
    change = CourseMaintenanceService.get_change(conn, change_id=change_id)
    AdminStore(conn, principal).record_audit(
        "course.change_rejected", "course", change["code"],
        {"change_id": str(change_id), "reason": body.reason},
    )
    conn.commit()
    return _admin_course_change(change)


