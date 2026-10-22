import logging
from enum import StrEnum
from typing import Literal
from uuid import uuid4

import psycopg
from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app.auth.models import Principal
from app.db import queries

logger = logging.getLogger(__name__)

Role = Literal["student", "advisor", "admin", "auditor"]
GrantType = Literal["advisor_access", "support_access"]
ResourceType = Literal["conversation", "plan", "all_records"]


class Capability(StrEnum):
    READ_OWN_WORKSPACE = "workspace:read_own"
    WRITE_OWN_WORKSPACE = "workspace:write_own"
    READ_AUDIT = "audit:read"
    EXPORT_AUDIT = "audit:export"
    VIEW_TELEMETRY = "telemetry:view"
    MANAGE_USERS = "users:manage"
    MANAGE_COLLECTIONS = "collections:manage"
    MANAGE_DEBUG = "debug:manage"
    MANAGE_CONNECTIONS = "connections:manage"
    MANAGE_PRICE_SCHEDULES = "pricing:manage"
    GRANT_SUPPORT_ACCESS = "grants:support_create"
    REVOKE_GRANTS = "grants:revoke"
    SOURCE_REVIEW = "sources:review"
    PROGRAM_RULE_REVIEW = "programs:review"
    COURSE_MAINTENANCE = "courses:maintain"
    VERIFY_ACADEMIC_RECORD = "records:verify"


ROLE_CAPABILITIES: dict[str, set[Capability]] = {
    "student": {
        Capability.READ_OWN_WORKSPACE,
        Capability.WRITE_OWN_WORKSPACE,
    },
    "advisor": {
        Capability.READ_OWN_WORKSPACE,
        Capability.WRITE_OWN_WORKSPACE,
        Capability.VERIFY_ACADEMIC_RECORD,
    },
    "auditor": {
        Capability.READ_OWN_WORKSPACE,
        Capability.READ_AUDIT,
        Capability.EXPORT_AUDIT,
        Capability.VIEW_TELEMETRY,
    },
    "admin": {
        Capability.READ_OWN_WORKSPACE,
        Capability.WRITE_OWN_WORKSPACE,
        Capability.READ_AUDIT,
        Capability.EXPORT_AUDIT,
        Capability.VIEW_TELEMETRY,
        Capability.MANAGE_USERS,
        Capability.MANAGE_COLLECTIONS,
        Capability.MANAGE_DEBUG,
        Capability.MANAGE_CONNECTIONS,
        Capability.MANAGE_PRICE_SCHEDULES,
        Capability.GRANT_SUPPORT_ACCESS,
        Capability.REVOKE_GRANTS,
        Capability.SOURCE_REVIEW,
        Capability.PROGRAM_RULE_REVIEW,
        Capability.COURSE_MAINTENANCE,
        Capability.VERIFY_ACADEMIC_RECORD,
    },
}


# Operation names are the server's authorization matrix. Routes and workers call
# require_operation so a role name or a caller-supplied workspace is not enough.
OPERATION_CAPABILITIES: dict[str, Capability] = {
    "workspace.read": Capability.READ_OWN_WORKSPACE,
    "workspace.write": Capability.WRITE_OWN_WORKSPACE,
    "audit.read": Capability.READ_AUDIT,
    "audit.export": Capability.EXPORT_AUDIT,
    "telemetry.view": Capability.VIEW_TELEMETRY,
    "users.manage": Capability.MANAGE_USERS,
    "collections.manage": Capability.MANAGE_COLLECTIONS,
    "debug.manage": Capability.MANAGE_DEBUG,
    "connections.manage": Capability.MANAGE_CONNECTIONS,
    "pricing.manage": Capability.MANAGE_PRICE_SCHEDULES,
    "grants.request": Capability.GRANT_SUPPORT_ACCESS,
    "grants.approve": Capability.GRANT_SUPPORT_ACCESS,
    "grants.revoke": Capability.REVOKE_GRANTS,
    "sources.review": Capability.SOURCE_REVIEW,
    "programs.review": Capability.PROGRAM_RULE_REVIEW,
    "courses.read": Capability.READ_AUDIT,
    "courses.manage": Capability.COURSE_MAINTENANCE,
    "records.verify": Capability.VERIFY_ACADEMIC_RECORD,
}


def has_capability(role: str, capability: Capability) -> bool:
    return capability in ROLE_CAPABILITIES.get(role, set())


def require_operation(principal: Principal, operation: str) -> None:
    """Deny when the caller's role does not hold the capability for this operation."""
    capability = OPERATION_CAPABILITIES.get(operation)
    if capability is None:
        raise HTTPException(403, "operation is not authorized")
    require_capability(principal, capability)


def require_workspace_execution(conn, workspace_key: str, *, lock: bool = False):
    """Recheck persisted role/status at worker boundaries; local CLI has no user row."""
    from app.privacy.fence import require_live_workspace

    require_live_workspace(conn, workspace_key, lock=lock)
    row = conn.execute(queries.get("lock_workspace_execution_user" if lock
                                   else "workspace_execution_user"),
                       {"workspace_key": workspace_key}).fetchone()
    if row and (row["status"] != "active" or not has_capability(
            row["role"], Capability.WRITE_OWN_WORKSPACE)):
        raise HTTPException(403, "this workspace cannot execute new work")
    return row


def require_capability(principal: Principal, capability: Capability) -> None:
    if principal.status != "active":
        raise HTTPException(403, "user account is disabled")
    if not has_capability(principal.role, capability):
        raise HTTPException(
            403,
            f"role '{principal.role}' does not possess capability '{capability.value}'",
        )


def check_workspace_access(
    conn,
    principal: Principal,
    target_workspace_key: str,
    resource_type: str,
    resource_id: str | None = None,
    action: str = "read",
    *,
    record_audit_on_denial: bool = True,
) -> bool:
    """Verify whether principal can access target_workspace_key either by ownership or active grant."""
    if principal.status != "active":
        raise HTTPException(403, "user account is disabled")

    # Own workspace is always permitted
    if principal.workspace_key == target_workspace_key:
        if action != "read":
            return has_capability(principal.role, Capability.WRITE_OWN_WORKSPACE)
        return has_capability(principal.role, Capability.READ_OWN_WORKSPACE)

    # If actor has no user_id (e.g. dev CLI without user row), cross-workspace is denied
    if principal.user_id is None:
        return False

    row = conn.execute(
        queries.get("check_active_resource_grant"),
        {
            "actor_user_id": principal.user_id,
            "target_workspace_key": target_workspace_key,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "action": action,
        },
    ).fetchone()

    if row is not None:
        return True

    # Access denied
    if record_audit_on_denial:
        try:
            # A savepoint (or a short independent transaction when idle) records
            # the denial without committing whatever the caller had pending.
            with conn.transaction():
                conn.execute(
                    queries.get("admin_insert_audit"),
                    {
                        "audit_id": uuid4(),
                        "actor_user_id": principal.user_id,
                        "actor_workspace": principal.workspace_key,
                        "action": "access.denied",
                        "target_type": resource_type,
                        "target_id": resource_id,
                        "detail": Jsonb({
                            "target_workspace": target_workspace_key,
                            "action": action,
                            "reason": "missing_or_expired_grant",
                        }),
                    },
                )
        except psycopg.Error as exc:
            logger.warning("Failed to record access.denied audit event: %s", exc)

    return False

