"""Feature flags and gradual rollout controls API (Item 17)."""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from psycopg.types.json import Jsonb

from app.api import deps
from app.api.schemas import (
    CohortAssignmentRequest,
    CohortAssignmentResponse,
    CohortMember,
    CohortSummary,
    EffectiveFlagsResponse,
    FeatureFlagKillSwitchRequest,
    FeatureFlagOverride,
    FeatureFlagOverrideRequest,
    FeatureFlagSummary,
)
from app.auth.dependencies import get_admin_principal, get_auditor_or_admin_principal, get_principal
from app.auth.models import Principal
from app.db import queries
from app.flags.service import FeatureFlagService

router = APIRouter(tags=["feature flags"])
Conn = Annotated[object, Depends(deps.get_conn)]


def _admin(
    request: Request,
    principal: Annotated[Principal, Depends(get_admin_principal)],
) -> Principal:
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


@router.get(
    "/api/flags/effective",
    response_model=EffectiveFlagsResponse,
    response_model_exclude_unset=True,
)
def get_effective_flags(
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    conn: Conn,
) -> dict:
    settings = getattr(request.app.state, "settings", None)
    flags = FeatureFlagService.evaluate_all(
        conn,
        workspace_key=principal.workspace_key,
        user_id=principal.user_id,
        settings=settings,
    )
    return {"flags": flags}


@router.get(
    "/api/admin/flags",
    response_model=list[FeatureFlagSummary],
    response_model_exclude_unset=True,
)
def list_admin_flags(
    _admin_user: AuditorOrAdmin,
    conn: Conn,
) -> list[dict]:
    return FeatureFlagService.list_flags(conn)


@router.get(
    "/api/admin/flags/{flag_name}/overrides",
    response_model=list[FeatureFlagOverride],
    response_model_exclude_unset=True,
)
def list_admin_flag_overrides(
    flag_name: str,
    _admin_user: AuditorOrAdmin,
    conn: Conn,
) -> list[dict]:
    return FeatureFlagService.list_overrides(conn, flag_name)


@router.post(
    "/api/admin/flags/{flag_name}/overrides",
    response_model=FeatureFlagOverride,
    response_model_exclude_unset=True,
)
def set_admin_flag_override(
    flag_name: str,
    body: FeatureFlagOverrideRequest,
    admin_user: Admin,
    conn: Conn,
) -> dict:
    result = FeatureFlagService.set_override(
        conn,
        flag_name,
        body.scopeType,
        body.scopeId,
        body.enabled,
        actor_user_id=admin_user.user_id,
        reason=body.reason,
        commit=False,
        expected_updated_at=body.expectedUpdatedAt,
        require_absent=body.requireAbsent,
    )
    conn.execute(
        queries.get("admin_insert_audit"),
        {
            "audit_id": uuid4(),
            "actor_user_id": admin_user.user_id,
            "actor_workspace": admin_user.workspace_key,
            "action": "admin.flag_override_set",
            "target_type": "feature_flag",
            "target_id": flag_name,
            "detail": Jsonb({
                "scope_type": body.scopeType,
                "scope_id": body.scopeId,
                "enabled": body.enabled,
                "reason": body.reason,
            }),
        },
    )
    conn.commit()
    return result


@router.delete("/api/admin/flags/{flag_name}/overrides/{scope_type}/{scope_id}",
               status_code=204, response_class=Response,
               responses={204: {"description": "Feature override removed."}})
def delete_admin_flag_override(
    flag_name: str,
    scope_type: str,
    scope_id: str,
    admin_user: Admin,
    conn: Conn,
    expectedUpdatedAt: str | None = None,
) -> Response:
    FeatureFlagService.delete_override(conn, flag_name, scope_type, scope_id, commit=False,
                                       expected_updated_at=expectedUpdatedAt)
    conn.execute(
        queries.get("admin_insert_audit"),
        {
            "audit_id": uuid4(),
            "actor_user_id": admin_user.user_id,
            "actor_workspace": admin_user.workspace_key,
            "action": "admin.flag_override_deleted",
            "target_type": "feature_flag",
            "target_id": flag_name,
            "detail": Jsonb({"scope_type": scope_type, "scope_id": scope_id}),
        },
    )
    conn.commit()
    return Response(status_code=204)


@router.post(
    "/api/admin/flags/{flag_name}/kill",
    response_model=FeatureFlagSummary,
    response_model_exclude_unset=True,
)
def toggle_admin_flag_kill_switch(
    flag_name: str,
    body: FeatureFlagKillSwitchRequest,
    admin_user: Admin,
    conn: Conn,
) -> dict:
    result = FeatureFlagService.set_kill_switch(
        conn,
        flag_name,
        body.killSwitchActive,
        reason=body.reason,
        commit=False,
        expected_updated_at=body.expectedUpdatedAt,
    )
    conn.execute(
        queries.get("admin_insert_audit"),
        {
            "audit_id": uuid4(),
            "actor_user_id": admin_user.user_id,
            "actor_workspace": admin_user.workspace_key,
            "action": "admin.flag_kill_switch_toggled",
            "target_type": "feature_flag",
            "target_id": flag_name,
            "detail": Jsonb({"kill_switch_active": body.killSwitchActive, "reason": body.reason}),
        },
    )
    conn.commit()
    return result


@router.get(
    "/api/admin/cohorts",
    response_model=list[CohortSummary],
    response_model_exclude_unset=True,
)
def list_admin_cohorts(
    _admin_user: AuditorOrAdmin,
    conn: Conn,
) -> list[dict]:
    return FeatureFlagService.list_cohorts(conn)


@router.get("/api/admin/cohorts/{cohort_name}/users", response_model=list[CohortMember],
            response_model_exclude_unset=True)
def list_cohort_members(cohort_name: str, _admin_user: AuditorOrAdmin, conn: Conn,
                        limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    return FeatureFlagService.list_cohort_members(conn, cohort_name, limit=limit, offset=offset)


@router.post("/api/admin/cohorts/{cohort_name}/users", response_model=CohortAssignmentResponse,
             response_model_exclude_unset=True)
def assign_cohort_user(
    cohort_name: str,
    body: CohortAssignmentRequest,
    admin_user: Admin,
    conn: Conn,
) -> dict:
    FeatureFlagService.assign_cohort(conn, body.userId, cohort_name, assigned_by=admin_user.user_id,
                                     commit=False)
    conn.execute(
        queries.get("admin_insert_audit"),
        {
            "audit_id": uuid4(),
            "actor_user_id": admin_user.user_id,
            "actor_workspace": admin_user.workspace_key,
            "action": "admin.cohort_user_assigned",
            "target_type": "user_cohort",
            "target_id": f"{body.userId}:{cohort_name}",
            "detail": Jsonb({"user_id": str(body.userId), "cohort_name": cohort_name}),
        },
    )
    conn.commit()
    return {"status": "assigned", "cohortName": cohort_name, "userId": str(body.userId)}


@router.delete("/api/admin/cohorts/{cohort_name}/users/{user_id}",
               status_code=204, response_class=Response,
               responses={204: {"description": "User removed from cohort."}})
def remove_cohort_user(
    cohort_name: str,
    user_id: UUID,
    admin_user: Admin,
    conn: Conn,
) -> Response:
    FeatureFlagService.remove_cohort(conn, user_id, cohort_name, commit=False)
    conn.execute(
        queries.get("admin_insert_audit"),
        {
            "audit_id": uuid4(),
            "actor_user_id": admin_user.user_id,
            "actor_workspace": admin_user.workspace_key,
            "action": "admin.cohort_user_removed",
            "target_type": "user_cohort",
            "target_id": f"{user_id}:{cohort_name}",
            "detail": Jsonb({"user_id": str(user_id), "cohort_name": cohort_name}),
        },
    )
    conn.commit()
    return Response(status_code=204)

