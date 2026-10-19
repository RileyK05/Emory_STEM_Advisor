"""Institutional Admin API: users, roles, audit, and audited content inspection."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.admin.store import AdminStore
from app.api import deps
from app.api.schemas import (
    AdminArtifact,
    AdminAuditEntry,
    AdminCollection,
    AdminCollectionWrite,
    AdminConversation,
    AdminJob,
    AdminOverview,
    AdminRevokedSessions,
    AdminSource,
    AdminTranscript,
    AdminUser,
    AdminUserSession,
    AdminUserUpdate,
)
from app.auth.dependencies import get_admin_principal
from app.auth.models import Principal

router = APIRouter(prefix="/api/admin", tags=["institutional admin"])
Conn = Annotated[object, Depends(deps.get_conn)]


def _admin(request: Request,
           principal: Annotated[Principal, Depends(get_admin_principal)]) -> Principal:
    if not request.app.state.settings.admin_panel_enabled:
        raise HTTPException(404, "institutional administration is disabled")
    return principal


Admin = Annotated[Principal, Depends(_admin)]


def _iso(value):
    return value.isoformat() if value is not None else None


def _user(row) -> AdminUser:
    return AdminUser(
        userId=row["user_id"], issuer=row["issuer"], subject=row["subject"],
        workspaceKey=row["workspace_key"], displayName=row["display_name"],
        email=row["email"], role=row["role"], status=row["status"],
        conversationCount=row.get("conversation_count", 0),
        activeSessions=row.get("active_sessions", 0),
        createdAt=_iso(row["created_at"]), updatedAt=_iso(row["updated_at"]),
        lastSeenAt=_iso(row["last_seen_at"]),
    )


def _conversation(row) -> AdminConversation:
    return AdminConversation(
        conversationId=row["conversation_id"], workspaceKey=row["workspace_key"],
        title=row["title"], lastSequence=row["last_sequence"],
        pendingRequestId=row["pending_request_id"],
        turnCount=row.get("turn_count", 0), summaryCount=row.get("summary_count", 0),
        createdAt=_iso(row["created_at"]), updatedAt=_iso(row["updated_at"]),
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
        createdAt=_iso(row["created_at"]), expiresAt=_iso(row["expires_at"]),
        revokedAt=_iso(row["revoked_at"]),
    ) for row in rows]


@router.patch("/users/{user_id}", response_model=AdminUser)
def update_user(user_id: UUID, body: AdminUserUpdate, conn: Conn, principal: Admin):
    store = AdminStore(conn, principal)
    row = store.update_user(user_id, role=body.role, status=body.status)
    return _user(store.user(user_id) | {"role": row["role"], "status": row["status"]})


@router.post("/users/{user_id}/revoke-sessions", response_model=AdminRevokedSessions)
def revoke_user_sessions(user_id: UUID, conn: Conn, principal: Admin):
    store = AdminStore(conn, principal)
    row = store.user(user_id)
    return AdminRevokedSessions(revoked=store.revoke_sessions(row["workspace_key"]))


@router.get("/audit", response_model=list[AdminAuditEntry])
def audit(conn: Conn, principal: Admin, limit: int = Query(100, ge=1, le=500)):
    rows = AdminStore(conn, principal).audit_log(limit=limit)
    return [AdminAuditEntry(
        auditId=row["audit_id"], actorUserId=row["actor_user_id"],
        actorWorkspace=row["actor_workspace"], action=row["action"],
        targetType=row["target_type"], targetId=row["target_id"],
        detail=row["detail"], createdAt=_iso(row["created_at"]),
        actorEmail=row["actor_email"], actorDisplayName=row["actor_display_name"],
    ) for row in rows]


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
        error=row["error"], createdAt=_iso(row["created_at"]),
        updatedAt=_iso(row["updated_at"]), finishedAt=_iso(row["finished_at"]),
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
        isCurrent=row["is_current"], createdAt=_iso(row["created_at"]),
        updatedAt=_iso(row["updated_at"]),
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
        createdAt=_iso(row["created_at"]),
    ) for row in rows]


def _collection(row) -> AdminCollection:
    return AdminCollection(
        collectionId=row["collection_id"], name=row["name"],
        sourceCount=row["source_count"], createdAt=_iso(row["created_at"]),
    )


@router.get("/collections", response_model=list[AdminCollection])
def collections(conn: Conn, principal: Admin, limit: int = Query(100, ge=1, le=500),
                offset: int = 0):
    rows = AdminStore(conn, principal).collections(limit=limit, offset=offset)
    return [_collection(row) for row in rows]


@router.post("/collections", response_model=AdminCollection, status_code=201)
def create_collection(body: AdminCollectionWrite, conn: Conn, principal: Admin):
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
