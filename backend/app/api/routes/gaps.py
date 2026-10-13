"""Private API for reviewing retrieval gaps and recording human resolution."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from psycopg import Connection
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api import deps
from app.auth.dependencies import get_principal
from app.auth.models import Principal
from app.gaps.store import GapConflict, GapNotFound, GapStore

router = APIRouter(prefix="/api/gaps", tags=["gaps"])
Conn = Annotated[Connection, Depends(deps.get_conn)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]


class GapResolution(BaseModel):
    sourceIds: list[UUID]
    note: str
    resolvedAt: datetime


class GapSummary(BaseModel):
    gapId: UUID
    collectionId: UUID
    query: str
    sourceIds: list[UUID] | None
    state: Literal["open", "resolved"]
    revision: int
    occurrenceCount: int
    recurrenceCount: int
    successfulRecurrenceCount: int
    firstSeenAt: datetime
    lastSeenAt: datetime
    resolution: GapResolution | None


class GapMetrics(BaseModel):
    repeatedGapObservations: int
    successfulRepeatedObservations: int
    recurrenceRate: float | None


class GapListResponse(BaseModel):
    gaps: list[GapSummary]
    metrics: GapMetrics


class ResolveGapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedRevision: int = Field(ge=1, strict=True)
    sourceIds: list[UUID] = Field(min_length=1, max_length=20)
    note: str = Field(default="", max_length=1000)

    @field_validator("sourceIds")
    @classmethod
    def distinct_sources(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("sourceIds must be unique")
        return value


@router.get("", response_model=GapListResponse)
def list_gaps(
    conn: Conn,
    principal: PrincipalDep,
    collectionId: UUID | None = None,
) -> GapListResponse:
    return GapListResponse.model_validate(
        GapStore(conn, principal.workspace_key).list(collection_id=collectionId)
    )


@router.post("/{gap_id}/resolve", response_model=GapSummary)
def resolve_gap(
    gap_id: UUID,
    body: ResolveGapRequest,
    conn: Conn,
    principal: PrincipalDep,
) -> GapSummary:
    try:
        result = GapStore(conn, principal.workspace_key).resolve(
            gap_id=gap_id,
            expected_revision=body.expectedRevision,
            source_ids=body.sourceIds,
            note=body.note,
            resolved_by=principal.subject,
        )
    except GapNotFound:
        raise HTTPException(404, "Gap or eligible source not found.") from None
    except GapConflict:
        raise HTTPException(409, "Gap changed; refresh before resolving it.") from None
    return GapSummary.model_validate(result)
