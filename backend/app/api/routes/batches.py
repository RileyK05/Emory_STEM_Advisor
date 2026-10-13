"""Workspace-scoped API for native artifact provider batches."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api import deps
from app.api.schemas import ArtifactBatchRecord, ArtifactBatchSummary
from app.auth.dependencies import get_workspace_key
from app.connections.models import ConnectionUnavailable
from app.db.connection import connector
from app.jobs.native_artifact_batches import (
    ArtifactBatchInput,
    cancel_and_account,
    create_artifact_batch,
    current_snapshot,
    poll_and_publish,
)
from app.jobs.provider_batches import (
    ProviderBatchConflict,
    ProviderBatchError,
    ProviderBatchInProgress,
    ProviderBatchNotFound,
    ProviderBatchProfileChanged,
    ProviderBatchService,
    ProviderBatchUnknown,
)
from app.operations.budget import OperationBudgetError

router = APIRouter(prefix="/api/artifact-batches", tags=["artifact-batches"])
WorkspaceDep = Annotated[str, Depends(get_workspace_key)]


def _connect_factory(request: Request):
    configured = getattr(request.app.state, "batch_connect_factory", None)
    if configured is not None:
        return configured
    database_url = request.app.state.settings.database_url
    if not database_url:
        raise HTTPException(503, detail="Batch storage is unavailable.")
    def fresh_connection():
        conn = connector(request.app.state.settings)()
        try:
            deps.ensure_database_ready(request.app.state, conn)
            return conn
        except Exception:
            conn.close()
            raise

    return fresh_connection


def _public(snapshot):
    return {
        "batchId": str(snapshot.batch_id),
        "state": snapshot.state,
        "itemCount": snapshot.item_count,
        "totalCount": snapshot.total_count,
        "completedCount": snapshot.completed_count,
        "failedCount": snapshot.failed_count,
        "cancelRequested": getattr(snapshot, "cancel_requested", False),
        "items": [{
            "itemId": item.correlation_id,
            "state": item.state,
            "publicationState": item.publication_state,
            "artifactId": str(item.artifact_id) if item.artifact_id else None,
            "revisionId": str(item.revision_id) if item.revision_id else None,
            "errorCode": item.error_code,
        } for item in snapshot.items],
    }


def _provider_for_batch(request: Request, snapshot):
    if snapshot.profile_id is None or snapshot.route_role is None:
        raise HTTPException(409, detail="Batch connection binding is unavailable.")
    connect_factory = _connect_factory(request)
    conn = connect_factory()
    try:
        profile = request.app.state.connection_profiles.get(conn, snapshot.profile_id)
        conn.commit()
    finally:
        conn.close()
    if profile is None:
        raise HTTPException(409, detail="Batch connection is unavailable.")
    try:
        return request.app.state.connection_runtime.existing_batch_provider(
            profile, expected_provider=snapshot.provider)
    except Exception as exc:
        raise HTTPException(409, detail="Batch connection is unavailable.") from exc


def _load_batch(request: Request, batch_id: UUID, workspace: str):
    try:
        return current_snapshot(batch_id, workspace, connect=_connect_factory(request))
    except ProviderBatchNotFound as exc:
        raise HTTPException(404, detail="Batch not found.") from exc


@router.post("", response_model=ArtifactBatchRecord, response_model_exclude_unset=True)
def submit_batch(body: ArtifactBatchInput, request: Request, workspace: WorkspaceDep):
    settings = request.app.state.settings
    connect_factory = _connect_factory(request)
    try:
        snapshot = create_artifact_batch(
            body, settings=settings, config=request.app.state.config,
            embedder=request.app.state.embedder,
            profiles=request.app.state.connection_profiles,
            runtime=request.app.state.connection_runtime,
            connect=connect_factory, workspace_key=workspace,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    except OperationBudgetError as exc:
        raise HTTPException(429, detail=str(exc)) from exc
    except ConnectionUnavailable as exc:
        raise HTTPException(503, detail=str(exc)) from exc
    except ProviderBatchInProgress as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    except ProviderBatchConflict as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    except ProviderBatchUnknown as exc:
        raise HTTPException(503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, detail="Batch submission is temporarily unavailable.") from exc
    return _public(snapshot)


@router.get("", response_model=list[ArtifactBatchSummary])
def list_batches(request: Request, workspace: WorkspaceDep):
    summaries = ProviderBatchService(_connect_factory(request)).list(workspace)
    return [{
        "batchId": str(item["batch_id"]),
        "state": item["state"],
        "cancelRequested": item["cancel_requested"],
        "itemCount": item["item_count"],
        "totalCount": item["total_count"],
        "completedCount": item["completed_count"],
        "failedCount": item["failed_count"],
        "createdAt": item["created_at"].isoformat(),
        "updatedAt": item["updated_at"].isoformat(),
    } for item in summaries]


@router.get("/{batch_id}", response_model=ArtifactBatchRecord, response_model_exclude_unset=True)
def get_batch(batch_id: UUID, request: Request, workspace: WorkspaceDep):
    return _public(_load_batch(request, batch_id, workspace))


@router.post("/{batch_id}/poll", response_model=ArtifactBatchRecord,
             response_model_exclude_unset=True)
def poll_batch(batch_id: UUID, request: Request, workspace: WorkspaceDep):
    snapshot = _load_batch(request, batch_id, workspace)
    provider = _provider_for_batch(request, snapshot)
    try:
        result = poll_and_publish(
            batch_id, workspace, provider=provider,
            connect=_connect_factory(request), data_dir=request.app.state.settings.data_dir,
        )
    except ProviderBatchNotFound as exc:
        raise HTTPException(404, detail="Batch not found.") from exc
    except (ProviderBatchConflict, ProviderBatchProfileChanged) as exc:
        raise HTTPException(409, detail="Batch can no longer be processed with this connection.") from exc
    except ProviderBatchError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, detail="Batch status is temporarily unavailable.") from exc
    return _public(result)


@router.post("/{batch_id}/cancel", response_model=ArtifactBatchRecord,
             response_model_exclude_unset=True)
def cancel_batch(batch_id: UUID, request: Request, workspace: WorkspaceDep):
    snapshot = _load_batch(request, batch_id, workspace)
    try:
        snapshot = ProviderBatchService(_connect_factory(request)).request_cancel(batch_id, workspace)
    except ProviderBatchNotFound as exc:
        raise HTTPException(404, detail="Batch not found.") from exc
    provider = _provider_for_batch(request, snapshot)
    try:
        result = cancel_and_account(
            batch_id, workspace, provider=provider,
            connect=_connect_factory(request), data_dir=request.app.state.settings.data_dir,
        )
    except ProviderBatchNotFound as exc:
        raise HTTPException(404, detail="Batch not found.") from exc
    except ProviderBatchConflict as exc:
        raise HTTPException(409, detail="Batch cannot be cancelled with this connection.") from exc
    except Exception as exc:
        raise HTTPException(503, detail="Batch cancellation is temporarily unavailable.") from exc
    return _public(result)
