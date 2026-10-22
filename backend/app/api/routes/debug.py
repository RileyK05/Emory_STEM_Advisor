"""Admin-only debug settings and scoped override sessions."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response

from app.api import deps
from app.api.schemas import (
    DebugExperimentCreateRequest,
    DebugExperimentResponse,
    DebugSession,
    DebugSessionCreate,
    DebugSessionCreated,
    DebugSessionUpdate,
    DebugSettingsResponse,
)
from app.auth.dependencies import get_admin_principal
from app.auth.models import Principal
from app.conversations.experiments import ExperimentService
from app.debug.registry import (
    DEFAULT_SYSTEM_PROMPT,
    OverrideError,
    retrieval_snapshot,
    settings_snapshot,
)
from app.debug.store import AppliedDebug, DebugStore

router = APIRouter(prefix="/api/debug", tags=["debug"])
Conn = Annotated[object, Depends(deps.get_conn)]


def _admin(request: Request,
           principal: Annotated[Principal, Depends(get_admin_principal)]) -> Principal:
    if not (request.app.state.settings.admin_panel_enabled
            and request.app.state.settings.debug_panel_enabled):
        raise HTTPException(404, "debugging is disabled")
    return principal


Admin = Annotated[Principal, Depends(_admin)]


def _debug(
    request: Request,
    principal: Admin,
    conn: Conn,
    token: Annotated[str | None, Header(alias="X-Debug-Session")] = None,
) -> AppliedDebug:
    if not token:
        raise HTTPException(401, "debug session required")
    settings = request.app.state.settings
    try:
        value = DebugStore(conn, principal).resolve(
            token, settings=settings, config=request.app.state.config)
    except OverrideError as exc:
        raise HTTPException(422, str(exc)) from None
    if value is None:
        raise HTTPException(401, "debug session is invalid or expired")
    return value


Debug = Annotated[AppliedDebug, Depends(_debug)]


def _session_body(value: AppliedDebug) -> DebugSession:
    return DebugSession(
        debugSessionId=value.session_id,
        expiresAt=value.expires_at.isoformat(),
        overrides=value.overrides,
        systemPrompt=value.system_prompt,
        createdAt=value.created_at.isoformat(),
        updatedAt=value.updated_at.isoformat(),
    )


@router.get("/settings", response_model=DebugSettingsResponse)
def settings(request: Request, principal: Admin):
    return DebugSettingsResponse(
        settings=settings_snapshot(request.app.state.settings),
        retrieval=retrieval_snapshot(request.app.state.config),
        systemPrompt=DEFAULT_SYSTEM_PROMPT,
        defaultSystemPrompt=DEFAULT_SYSTEM_PROMPT,
    )


@router.post("/sessions", response_model=DebugSessionCreated)
def create_session(body: DebugSessionCreate, request: Request, conn: Conn, principal: Admin):
    try:
        row, token = DebugStore(conn, principal).create(
            overrides=body.overrides, system_prompt=body.systemPrompt,
            settings=request.app.state.settings, config=request.app.state.config,
            ttl_seconds=request.app.state.settings.debug_session_ttl_seconds,
        )
    except OverrideError as exc:
        raise HTTPException(422, str(exc)) from None
    return DebugSessionCreated(
        debugSessionId=row["debug_session_id"], token=token,
        expiresAt=row["expires_at"].isoformat(), overrides=row["overrides"],
        systemPrompt=row["system_prompt"], createdAt=row["created_at"].isoformat(),
        updatedAt=row["updated_at"].isoformat(),
    )


@router.get("/session", response_model=DebugSession)
def current_session(value: Debug):
    return _session_body(value)


@router.patch("/session", response_model=DebugSession)
def update_session(body: DebugSessionUpdate, request: Request, conn: Conn,
                   principal: Admin, value: Debug):
    overrides = (body.overrides if "overrides" in body.model_fields_set
                 else value.overrides)
    prompt = (body.systemPrompt if "systemPrompt" in body.model_fields_set
              else value.system_prompt)
    try:
        row = DebugStore(conn, principal).update(
            value.session_id, overrides=overrides or {}, system_prompt=prompt,
            settings=request.app.state.settings, config=request.app.state.config,
        )
    except OverrideError as exc:
        raise HTTPException(422, str(exc)) from None
    return DebugSession(
        debugSessionId=row["debug_session_id"], expiresAt=row["expires_at"].isoformat(),
        overrides=row["overrides"], systemPrompt=row["system_prompt"],
        createdAt=row["created_at"].isoformat(), updatedAt=row["updated_at"].isoformat(),
    )


@router.delete("/session", status_code=204, response_class=Response,
               responses={204: {"description": "Debug session revoked."}})
def delete_session(conn: Conn, principal: Admin, value: Debug):
    DebugStore(conn, principal).revoke(value.session_id)
    return Response(status_code=204)


@router.post("/experiments", response_model=DebugExperimentResponse, response_model_exclude_unset=True)
def start_experiment(body: DebugExperimentCreateRequest, conn: Conn, principal: Admin):
    row = ExperimentService.start(
        conn, principal, body.conversationId, body.requestId, body.snapshot,
    )
    conn.commit()
    return DebugExperimentResponse(
        experimentId=row["experiment_id"],
        sourceConversationId=row["source_conversation_id"],
        sourceRequestId=row["source_request_id"],
        state=row["state"],
        createdAt=row["created_at"].isoformat(),
    )
