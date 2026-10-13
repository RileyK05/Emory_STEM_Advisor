"""Private, idempotent endpoint for native binary classifications."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api import deps
from app.api.schemas import NativeDecision
from app.auth.dependencies import get_principal
from app.auth.models import Principal
from app.generation.decisions import (
    DecisionConflict,
    DecisionInput,
    DecisionNotFound,
    DecisionService,
    DecisionUnavailable,
)

router = APIRouter(prefix="/api/decisions", tags=["decisions"])
PrincipalDep = Annotated[Principal, Depends(get_principal)]


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decisionId: UUID
    task: str = Field(min_length=1, max_length=1000)
    predicate: str = Field(min_length=1, max_length=1000)
    input: str | dict


def _service(request) -> DecisionService:
    return DecisionService(settings=request.app.state.settings,
                           profiles=request.app.state.connection_profiles,
                           runtime=request.app.state.connection_runtime)


def _http_error(exc):
    if isinstance(exc, DecisionNotFound):
        raise HTTPException(404, "Decision not found.") from None
    if isinstance(exc, DecisionConflict):
        raise HTTPException(409, str(exc)) from None
    if isinstance(exc, DecisionUnavailable):
        raise HTTPException(503, str(exc)) from None
    raise exc


@router.post("", response_model=NativeDecision)
def submit(body: DecisionBody, request: Request, conn: Annotated[object, Depends(deps.get_conn)],
           principal: PrincipalDep):
    try:
        return _service(request).submit(
            conn, principal.workspace_key,
            DecisionInput(body.decisionId, body.task, body.predicate, body.input),
            data_scope="user_supplied_data" if principal.mode == "development" else "private_workspace",
        )
    except (DecisionConflict, DecisionNotFound, DecisionUnavailable) as exc:
        _http_error(exc)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "Invalid decision request.") from exc


@router.get("/{decision_id}", response_model=NativeDecision)
def get_decision(decision_id: UUID, request: Request,
                 conn: Annotated[object, Depends(deps.get_conn)],
                 principal: PrincipalDep):
    try:
        return _service(request).get(conn, principal.workspace_key, decision_id)
    except (DecisionNotFound, DecisionConflict) as exc:
        _http_error(exc)


@router.post("/{decision_id}/abandon", response_model=NativeDecision)
def abandon(decision_id: UUID, request: Request,
            conn: Annotated[object, Depends(deps.get_conn)],
            principal: PrincipalDep):
    try:
        return _service(request).abandon(conn, principal.workspace_key, decision_id)
    except DecisionNotFound as exc:
        _http_error(exc)
