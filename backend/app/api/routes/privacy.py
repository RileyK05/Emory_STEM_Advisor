"""Owner-scoped privacy APIs: student data export and fenced deletion."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.api import deps
from app.api.schemas import (
    PrivacyDeletionRequest,
    PrivacyDeletionResponse,
    PrivacyExportDownloadResponse,
    PrivacyExportResponse,
)
from app.auth.dependencies import get_principal
from app.auth.models import Principal
from app.privacy.service import PrivacyService

router = APIRouter(prefix="/api/privacy", tags=["privacy"])
Conn = Annotated[object, Depends(deps.get_conn)]
AuthPrincipal = Annotated[Principal, Depends(get_principal)]


@router.post("/exports", response_model=PrivacyExportResponse, response_model_exclude_unset=True, status_code=201)
def create_export(conn: Conn, principal: AuthPrincipal):
    return PrivacyService.start_export(conn, principal.workspace_key, principal.user_id)


@router.get("/exports", response_model=list[PrivacyExportResponse], response_model_exclude_unset=True)
def list_exports(conn: Conn, principal: AuthPrincipal):
    return PrivacyService.list_exports(conn, principal.workspace_key)


@router.get("/exports/{export_id}", response_model=PrivacyExportResponse, response_model_exclude_unset=True)
def get_export(export_id: UUID, conn: Conn, principal: AuthPrincipal):
    return PrivacyService.get_export(conn, export_id, principal.workspace_key)


@router.get(
    "/exports/{export_id}/download",
    response_model=PrivacyExportDownloadResponse,
    response_model_exclude_unset=True,
)
def download_export(
    export_id: UUID,
    conn: Conn,
    principal: AuthPrincipal,
    export_token: Annotated[str | None, Header(alias="X-Export-Token")] = None,
):
    return PrivacyService.download_export(
        conn, export_id, principal.workspace_key, export_token
    )


@router.post("/deletions", response_model=PrivacyDeletionResponse, response_model_exclude_unset=True)
def delete_data(body: PrivacyDeletionRequest, request: Request, conn: Conn, principal: AuthPrincipal):
    if body.confirmation.strip() != "DELETE MY DATA":
        raise HTTPException(
            422,
            "confirmation string must match 'DELETE MY DATA' to execute permanent deletion",
        )
    return PrivacyService.delete_workspace(
        conn,
        principal.workspace_key,
        principal.user_id,
        reason=body.reason or "user_request",
        data_dir=request.app.state.settings.data_dir,
    )


@router.post("/deletions/{deletion_id}/resume", response_model=PrivacyDeletionResponse, response_model_exclude_unset=True)
def resume_deletion(deletion_id: UUID, request: Request, conn: Conn, principal: AuthPrincipal):
    return PrivacyService.resume_deletion(
        conn,
        principal.workspace_key,
        deletion_id,
        data_dir=request.app.state.settings.data_dir,
    )

