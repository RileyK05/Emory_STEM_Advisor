"""Scoped HTTP facade over the durable run/job store."""

from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.api.deps import request_job_store
from app.api.schemas import ArtifactRun, ArtifactRunEvent, ArtifactRunLimits
from app.auth.dependencies import get_workspace_key
from app.jobs.store import JobNotFound, JobStore
from app.jobs.types import Job, JobEvent, JobStatus

router = APIRouter(prefix="/api/runs", tags=["runs"])
WorkspaceDep = Annotated[str, Depends(get_workspace_key)]
_MAX_INPUT_BYTES = 64 * 1024
_PROGRESS_STAGES = frozenset({
    "planning", "retrieving", "routing", "generating", "repairing", "assembling",
    "validating", "validated", "rendering",
    "recalculating", "publishing", "tool_started", "tool_completed", "tool_reused", "tool_replayed",
})


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["document", "workbook", "html"]
    inputs: dict[str, Any]
    idempotencyKey: str = Field(min_length=1, max_length=200)

    @field_validator("idempotencyKey")
    @classmethod
    def valid_idempotency_key(cls, value: str) -> str:
        if not value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("idempotencyKey is invalid")
        return value

    @field_validator("inputs")
    @classmethod
    def bounded_inputs(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            size = len(
                json.dumps(
                    value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
                ).encode("utf-8")
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("inputs must be bounded JSON data") from exc
        if size > _MAX_INPUT_BYTES:
            raise ValueError("inputs exceed the run request limit")
        return value


def _store(request: Request) -> JobStore:
    return request_job_store(request, state_attr="job_store",
                             unavailable_detail="Job storage is unavailable.")


def _get_scoped(store: JobStore, job_id: UUID, scope_key: str) -> Job:
    try:
        job = store.get(job_id)
    except JobNotFound as exc:
        raise HTTPException(404, detail="Run not found.") from exc
    if job.scope_key != scope_key:
        raise HTTPException(404, detail="Run not found.")
    return job


def _artifact_ids(value: Any) -> list[str] | None:
    if not isinstance(value, dict):
        return None
    values = value.get("artifactIds")
    if not isinstance(values, list) or len(values) > 100:
        return None
    result = []
    for item in values:
        try:
            result.append(str(UUID(str(item))))
        except (TypeError, ValueError):
            return None
    return result


def _artifact_revisions(value: Any) -> list[dict[str, str]] | None:
    if not isinstance(value, dict):
        return None
    values = value.get("artifactRevisions")
    if not isinstance(values, list) or len(values) > 100:
        return None
    result = []
    for item in values:
        if not isinstance(item, dict) or set(item) != {"artifactId", "revisionId"}:
            return None
        try:
            artifact_id = str(UUID(str(item["artifactId"])))
            revision_id = str(UUID(str(item["revisionId"])))
        except (TypeError, ValueError):
            return None
        result.append({"artifactId": artifact_id, "revisionId": revision_id})
    return result


def _safe_error(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    message = value.get("message")
    if not isinstance(message, str) or not message or len(message) > 500:
        return "Run failed."
    if value.get("type") in {"execution_failed", "lease_expired"}:
        return message
    code = value.get("type")
    if (
        isinstance(code, str)
        and 1 <= len(code) <= 80
        and all(char.isalnum() or char in "_-" for char in code)
        and not any(ord(char) < 32 for char in message)
        and not re.search(r"(?i)(bearer\s+|sk-[a-z0-9]{8}|api[_-]?key|password|secret)", message)
    ):
        return message
    return "Run failed."


def _public_run(job: Job) -> dict[str, Any]:
    result = {
        "runId": str(job.job_id),
        "state": job.status.value,
    }
    artifact_ids = _artifact_ids(job.result)
    if artifact_ids is not None:
        result["artifactIds"] = artifact_ids
    artifact_revisions = _artifact_revisions(job.result)
    if artifact_revisions is not None:
        result["artifactRevisions"] = artifact_revisions
    if job.status is JobStatus.FAILED:
        result["error"] = _safe_error(job.error) or "Run failed."
    elif job.status is JobStatus.CANCELLED:
        result["error"] = None
    return result


def _event_state(event: JobEvent, current: Job) -> JobStatus:
    if event.event_type == "claimed":
        return JobStatus.RUNNING
    if event.event_type in {"retry_scheduled", "lease_recovered"}:
        return JobStatus.QUEUED
    if event.event_type in {"succeeded", "failed", "cancelled"}:
        return JobStatus(event.event_type)
    if event.event_type == "queued":
        return JobStatus.QUEUED
    if event.event_type == "cancel_requested":
        return current.status
    raw = event.payload.get("status")
    try:
        return JobStatus(raw)
    except (ValueError, TypeError):
        return current.status


@router.post("", response_model=ArtifactRun, response_model_exclude_unset=True)
def create_run(body: CreateRunRequest, request: Request, workspace: WorkspaceDep) -> dict[str, Any]:
    from app.generation.artifacts import ArtifactGenerationInput

    inputs_payload = body.inputs
    if "outputPlan" in body.inputs:
        try:
            inputs = ArtifactGenerationInput.model_validate(body.inputs)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="Artifact run inputs are invalid.") from exc
        if inputs.outputPlan is None:
            raise HTTPException(status_code=422, detail="The output plan is invalid.")
        if inputs.outputPlan.kind != body.kind:
            raise HTTPException(status_code=422, detail="The output plan kind must match the run kind.")
        from app.generation.planned_artifacts import validate_operation_limits

        settings = request.app.state.settings
        try:
            validate_operation_limits(
                inputs.outputPlan, max_calls=settings.operation_max_calls,
                total_token_budget=settings.operation_token_budget,
                per_call_output_cap=settings.llm_max_output_tokens,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        inputs_payload = inputs.model_dump(mode="json", by_alias=True, exclude_none=True)
    from app.api.admission import AdmissionLimits

    scope_key = workspace
    settings = getattr(request.app.state, "settings", None)
    admission = AdmissionLimits(
        getattr(settings, "admission_max_pending_workspace", 0),
        getattr(settings, "admission_max_pending_global", 0),
    )
    options = {"admission": admission} if admission.enabled else {}
    job = _store(request).create(
        kind="artifact.run",
        scope_key=scope_key,
        idempotency_key=body.idempotencyKey,
        payload={"kind": body.kind, "inputs": inputs_payload},
        **options,
    )
    return _public_run(job)


@router.get("/limits", response_model=ArtifactRunLimits)
def get_run_limits(request: Request, _workspace: WorkspaceDep) -> dict[str, int]:
    settings = request.app.state.settings
    return {
        "maxCalls": settings.operation_max_calls,
        "totalTokenBudget": settings.operation_token_budget,
        "perCallOutputCap": settings.llm_max_output_tokens,
    }


@router.get("/{run_id}", response_model=ArtifactRun, response_model_exclude_unset=True)
def get_run(run_id: UUID, request: Request, workspace: WorkspaceDep) -> dict[str, Any]:
    job = _get_scoped(_store(request), run_id, workspace)
    return _public_run(job)


@router.get("/{run_id}/events", response_model=list[ArtifactRunEvent],
            response_model_exclude_unset=True)
def list_run_events(
    run_id: UUID,
    request: Request,
    workspace: WorkspaceDep,
    after_event_id: int = Query(default=0, ge=0, alias="afterEventId"),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    store = _store(request)
    scope_key = workspace
    job = _get_scoped(store, run_id, scope_key)
    try:
        events = store.events(run_id, after_event_id=after_event_id, limit=limit)
    except JobNotFound as exc:
        raise HTTPException(404, detail="Run not found.") from exc
    result = []
    for event in events:
        state = _event_state(event, job)
        item: dict[str, Any] = {
            "eventId": event.event_id,
            "runId": str(run_id),
            "state": state.value,
        }
        ids = _artifact_ids(event.payload)
        if ids is None and event.event_type == "succeeded":
            ids = _artifact_ids(job.result)
        if ids is not None:
            item["artifactIds"] = ids
        artifact_revisions = _artifact_revisions(event.payload)
        if artifact_revisions is None and event.event_type == "succeeded":
            artifact_revisions = _artifact_revisions(job.result)
        if artifact_revisions is not None:
            item["artifactRevisions"] = artifact_revisions
        if event.event_type == "failed":
            item["error"] = _safe_error(event.payload.get("error")) or "Run failed."
        stage = event.payload.get("stage")
        if isinstance(stage, str) and stage in _PROGRESS_STAGES:
            item["stage"] = stage
        unit = event.payload.get("unit")
        if isinstance(unit, str) and len(unit) <= 120:
            item["unit"] = unit
        for field in ("requestedOutputTokenLimit", "outputTokenLimit"):
            value = event.payload.get(field)
            if type(value) is int and 1 <= value <= 32_000:
                item[field] = value
        result.append(item)
    return result


@router.post("/{run_id}/cancel", response_model=ArtifactRun, response_model_exclude_unset=True)
def cancel_run(run_id: UUID, request: Request, workspace: WorkspaceDep) -> dict[str, Any]:
    store = _store(request)
    scope_key = workspace
    _get_scoped(store, run_id, scope_key)
    try:
        job = store.cancel(run_id)
    except JobNotFound as exc:
        raise HTTPException(404, detail="Run not found.") from exc
    if job.scope_key != scope_key:
        raise HTTPException(404, detail="Run not found.")
    return _public_run(job)
