"""Private, idempotent model assessment of the claims in a saved answer.

A review is explicit and opt-in: it spends the answer's own operation budget,
needs an evaluated structured-output profile, and never marks a claim as
human-verified.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from app.api import deps
from app.api.schemas import ClaimReview
from app.auth.dependencies import get_principal
from app.auth.models import Principal
from app.connections.models import ConnectionUnavailable, CredentialUnreadable, MissingCredential
from app.connections.selection import resolve
from app.db import queries
from app.generation.claim_review import (
    ClaimReviewConflict,
    ClaimReviewNotFound,
    ClaimReviewService,
    ClaimReviewUnavailable,
    ReviewRequest,
)

router = APIRouter(prefix="/api/claim-reviews", tags=["claim-reviews"])
PrincipalDep = Annotated[Principal, Depends(get_principal)]
ConnDep = Annotated[object, Depends(deps.get_conn)]
_SCOPES = frozenset({"user_supplied_data", "private_workspace"})


class ClaimReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewId: UUID
    traceId: UUID


def _errors(call):
    try:
        return call()
    except ClaimReviewNotFound:
        raise HTTPException(404, "Claim review or answer not found.") from None
    except ClaimReviewConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except ClaimReviewUnavailable as exc:
        raise HTTPException(503, str(exc)) from None


@router.post("", response_model=ClaimReview)
def submit(body: ClaimReviewBody, request: Request, conn: ConnDep, principal: PrincipalDep):
    trace = {"trace_id": body.traceId}
    response = conn.execute(queries.get("get_response_for_trace"), trace).fetchone()
    operation = conn.execute(queries.get("get_operation_for_trace"), trace).fetchone()
    conn.commit()
    if response is None or operation is None:
        raise HTTPException(404, "Claim review or answer not found.")
    state = request.app.state

    def select(snapshot):
        try:
            return resolve(
                state.settings, conn, query=snapshot["answer"], job_kind="claim_review",
                data_scope="private_workspace", required_capabilities={"structured_output"},
                required_data_scopes=_SCOPES, profiles=state.connection_profiles,
                runtime=state.connection_runtime,
            )
        except (ConnectionUnavailable, CredentialUnreadable, MissingCredential) as exc:
            raise ClaimReviewUnavailable(
                "No evaluated scoped structured-output profile is available.") from exc

    review = ReviewRequest(review_id=body.reviewId, response_id=response["response_id"],
                           operation_id=operation["operation_id"])
    return _errors(lambda: ClaimReviewService(state.settings).submit(
        conn, workspace_key=principal.workspace_key, request=review, selection_factory=select))


@router.get("/{review_id}", response_model=ClaimReview)
def get_review(review_id: UUID, request: Request, conn: ConnDep, principal: PrincipalDep):
    service = ClaimReviewService(request.app.state.settings)
    return _errors(lambda: service.get(conn, principal.workspace_key, review_id))


@router.post("/{review_id}/abandon", response_model=ClaimReview)
def abandon(review_id: UUID, request: Request, conn: ConnDep, principal: PrincipalDep):
    service = ClaimReviewService(request.app.state.settings)
    return _errors(lambda: service.abandon(conn, principal.workspace_key, review_id))
