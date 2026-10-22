"""Human prompt versions and suite promotion. A model is not a release authority."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from app.admin.prompt_registry import PromptRegistry
from app.api import deps
from app.api.schemas import (
    PromptFixtureCreateRequest,
    PromptFixtureResponse,
    PromptPromotionRequest,
    PromptPromotionResponse,
    PromptVersionCreateRequest,
    PromptVersionResponse,
)
from app.auth.dependencies import get_admin_principal
from app.auth.models import Principal
from app.util.json import iso

router = APIRouter(prefix="/api/admin/prompts", tags=["prompt registry"])
Conn = Annotated[object, Depends(deps.get_conn)]


def _admin(request: Request,
           principal: Annotated[Principal, Depends(get_admin_principal)]) -> Principal:
    if not request.app.state.settings.admin_panel_enabled:
        raise HTTPException(404, "institutional administration is disabled")
    return principal


Admin = Annotated[Principal, Depends(_admin)]


def _version(row: dict) -> PromptVersionResponse:
    return PromptVersionResponse(
        versionId=row["version_id"],
        name=row["name"],
        templateText=row["template_text"],
        contentDigest=row["content_digest"],
        reviewState=row["review_state"],
        createdAt=iso(row["created_at"]),
    )


@router.post("", response_model=PromptVersionResponse, response_model_exclude_unset=True)
def register_prompt(body: PromptVersionCreateRequest, conn: Conn, principal: Admin):
    row = PromptRegistry.register(
        conn, principal, name=body.name, template_text=body.templateText, version_id=body.versionId,
    )
    conn.commit()
    return _version(row)


@router.post("/{version_id}/approve", response_model=PromptVersionResponse,
             response_model_exclude_unset=True)
def approve_prompt(version_id: UUID, conn: Conn, principal: Admin):
    row = PromptRegistry.approve(conn, principal, version_id)
    conn.commit()
    return _version(row)


@router.post("/fixtures", response_model=PromptFixtureResponse, response_model_exclude_unset=True)
def put_fixture(body: PromptFixtureCreateRequest, conn: Conn, principal: Admin):
    row = PromptRegistry.put_fixture(
        conn, principal, suite_name=body.suiteName, fixture_text=body.fixtureText,
    )
    conn.commit()
    return PromptFixtureResponse(
        fixtureId=row["fixture_id"],
        suiteName=row["suite_name"],
        fixtureDigest=row["fixture_digest"],
        present=row["present"],
        createdAt=iso(row["created_at"]),
    )


@router.post("/{version_id}/promote", response_model=PromptPromotionResponse,
             response_model_exclude_unset=True)
def promote_prompt(version_id: UUID, body: PromptPromotionRequest, conn: Conn, principal: Admin):
    row = PromptRegistry.promote(conn, principal, version_id=version_id, suite_name=body.suiteName)
    conn.commit()
    return PromptPromotionResponse(
        promotionId=row["promotion_id"],
        versionId=row["version_id"],
        suiteName=row["suite_name"],
        fixtureDigest=row["fixture_digest"],
        status=row["status"],
        createdAt=iso(row["created_at"]),
    )
