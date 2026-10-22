"""Quota and rate limiting API routes (Item 27)."""

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends
from psycopg.types.json import Jsonb

from app.api import deps
from app.api.schemas import (
    EffectiveQuotaResponse,
    QuotaPolicySummary,
    QuotaPolicyUpsertRequest,
)
from app.auth.dependencies import get_principal
from app.auth.models import Principal
from app.db import queries
from app.quotas.service import QuotaService

router = APIRouter(tags=["quotas"])
Conn = Annotated[object, Depends(deps.get_conn)]




Admin = deps.PanelAdmin
AuditorOrAdmin = deps.PanelAuditorOrAdmin


@router.get(
    "/api/quotas/effective",
    response_model=EffectiveQuotaResponse,
    response_model_exclude_unset=True,
)
def get_effective_quota(
    principal: Annotated[Principal, Depends(get_principal)],
    conn: Conn,
) -> dict:
    return QuotaService.get_effective_quota(
        conn,
        workspace_key=principal.workspace_key,
        user_id=principal.user_id,
    )


@router.get(
    "/api/admin/quotas",
    response_model=list[QuotaPolicySummary],
    response_model_exclude_unset=True,
)
def list_admin_quotas(
    _admin_user: AuditorOrAdmin,
    conn: Conn,
) -> list[dict]:
    return QuotaService.list_policies(conn)


@router.post(
    "/api/admin/quotas",
    response_model=QuotaPolicySummary,
    response_model_exclude_unset=True,
)
def upsert_admin_quota(
    body: QuotaPolicyUpsertRequest,
    admin_user: Admin,
    conn: Conn,
) -> dict:
    result = QuotaService.upsert_policy(
        conn,
        body.scopeType,
        body.scopeId,
        queries_per_minute=body.queriesPerMinute,
        queries_per_day=body.queriesPerDay,
        tokens_per_day=body.tokensPerDay,
        spend_usd_per_day=body.spendUsdPerDay,
        max_active_jobs=body.maxActiveJobs,
        commit=False,
    )
    conn.execute(
        queries.get("admin_insert_audit"),
        {
            "audit_id": uuid4(),
            "actor_user_id": admin_user.user_id,
            "actor_workspace": admin_user.workspace_key,
            "action": "admin.quota_policy_upserted",
            "target_type": "quota_policy",
            "target_id": f"{body.scopeType}:{body.scopeId}",
            "detail": Jsonb({
                "scope_type": body.scopeType,
                "scope_id": body.scopeId,
                "queries_per_minute": body.queriesPerMinute,
                "queries_per_day": body.queriesPerDay,
                "tokens_per_day": body.tokensPerDay,
                "spend_usd_per_day": body.spendUsdPerDay,
                "max_active_jobs": body.maxActiveJobs,
            }),
        },
    )
    conn.commit()
    return result

