"""Feature flag evaluation engine, cohort scoping, and kill switches."""

from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException

from app.config import Settings, load_settings
from app.db import queries
from app.util.json import iso

DEFAULT_FLAGS = [
    ("query_planning", "Plan multi-step retrieval queries", "QUERY_PLANNING_ENABLED", True),
    ("advising_research", "Research controller and multi-program comparison", "ADVISING_RESEARCH_ENABLED", True),
    ("tools", "Sandbox and calculation tools", "TOOLS_ENABLED", True),
    ("citation_repair", "Answer citation repair", "ANSWER_CITATION_REPAIR_ENABLED", True),
    ("conversation_compaction", "Compacting long conversations", "CONVERSATION_COMPACTION_ENABLED", True),
    ("advising_summary_export", "Advising plan and summary export", None, True),
    ("memory_inspector", "Conversation memory inspector and reset", None, True),
]

EXECUTION_FLAGS = {
    "query_planning": "query_planning_enabled",
    "advising_research": "advising_research_enabled",
    "tools": "tools_enabled",
    "citation_repair": "answer_citation_repair_enabled",
    "conversation_compaction": "conversation_compaction_enabled",
}


def _check_expected(row, timestamp: str | None):
    if timestamp is None:
        return
    try:
        parsed = datetime.fromisoformat(timestamp)
        if parsed.utcoffset() is None:
            raise ValueError("timezone required")
    except ValueError as exc:
        raise HTTPException(422, "expected revision must be an ISO timestamp with timezone") from exc
    if row is None or parsed != row["updated_at"]:
        raise HTTPException(409, "feature policy changed; refresh and review before saving")


class FeatureFlagService:
    @staticmethod
    def ensure_seed_flags(conn, *, commit: bool = True) -> None:
        for name, desc, env_key, default_val in DEFAULT_FLAGS:
            conn.execute(
                queries.get("seed_feature_flags"),
                {
                    "flag_name": name,
                    "description": desc,
                    "env_ceiling_key": env_key,
                    "default_enabled": default_val,
                },
            )
        if commit:
            conn.commit()

    @staticmethod
    def evaluate(
        conn,
        flag_name: str,
        *,
        workspace_key: str | None = None,
        user_id: UUID | None = None,
        settings: Settings | None = None,
    ) -> bool:
        """Evaluate a feature flag using deterministic precedence:
        1. Flag existence check
        2. Immediate kill switch
        3. Global environment ceiling (if env is disabled, flag can NEVER be enabled)
        4. Workspace override
        5. Cohort override
        6. Global override
        7. Default enabled
        """
        flag = conn.execute(queries.get("get_feature_flag"), {"flag_name": flag_name}).fetchone()
        if flag is None:
            default = next((item for item in DEFAULT_FLAGS if item[0] == flag_name), None)
            if default is None:
                return False
            flag = {"kill_switch_active": False, "env_ceiling_key": default[2],
                    "default_enabled": default[3]}

        # 1. Kill switch
        if flag["kill_switch_active"]:
            return False

        # 2. Environment ceiling
        ceiling_key = flag["env_ceiling_key"]
        if ceiling_key:
            ceiling_enabled = bool(getattr(settings or load_settings(), ceiling_key.lower(), False))
            if not ceiling_enabled:
                return False

        # 3. Overrides lookup
        overrides = conn.execute(
            queries.get("list_feature_flag_overrides"),
            {"flag_name": flag_name},
        ).fetchall()

        # Check workspace override
        if workspace_key:
            for ov in overrides:
                if ov["scope_type"] == "workspace" and ov["scope_id"] == workspace_key:
                    return bool(ov["enabled"])

        # Check cohort overrides
        if user_id:
            cohort_rows = conn.execute(
                queries.get("get_user_cohorts"),
                {"user_id": user_id},
            ).fetchall()
            user_cohorts = {r["cohort_name"] for r in cohort_rows}
            matching_cohort_overrides = [
                ov for ov in overrides if ov["scope_type"] == "cohort" and ov["scope_id"] in user_cohorts
            ]
            if matching_cohort_overrides:
                # If all matching cohorts enable it, True; if any explicitly disables, False wins
                return all(ov["enabled"] for ov in matching_cohort_overrides)

        # Check global override
        for ov in overrides:
            if ov["scope_type"] == "global" and ov["scope_id"] in ("*", "global"):
                return bool(ov["enabled"])

        # 4. Fallback to default
        return bool(flag["default_enabled"])

    @staticmethod
    def evaluate_all(
        conn,
        *,
        workspace_key: str | None = None,
        user_id: UUID | None = None,
        settings: Settings | None = None,
    ) -> dict[str, bool]:
        """Evaluate all defined feature flags for the given context."""
        flags = conn.execute(queries.get("list_feature_flags")).fetchall()
        result = {}
        for name in sorted({item[0] for item in DEFAULT_FLAGS} | {f["flag_name"] for f in flags}):
            result[name] = FeatureFlagService.evaluate(
                conn,
                name,
                workspace_key=workspace_key,
                user_id=user_id,
                settings=settings,
            )
        if not result.get("tools", False):
            result["advising_research"] = False
        return result

    @staticmethod
    def execution_settings(conn, settings: Settings, workspace_key: str, *, ceilings=None):
        """Read current policy without committing the caller's transaction.

        Process settings are ceilings. Debug/profile settings may narrow them,
        but cannot enable a feature the process or rollout policy has disabled.
        """
        from app.auth.capabilities import require_workspace_execution

        user = require_workspace_execution(conn, workspace_key)
        flags = FeatureFlagService.evaluate_all(
            conn, workspace_key=workspace_key, user_id=user["user_id"] if user else None,
            settings=ceilings or settings)
        values = {attr: bool(getattr(settings, attr) and flags.get(name, False))
                  for name, attr in EXECUTION_FLAGS.items()}
        values["advising_research_enabled"] &= values["tools_enabled"]
        return replace(settings, **values)

    @staticmethod
    def require_enabled(conn, flag_name: str, *, workspace_key: str, settings: Settings | None,
                        lock: bool = False):
        if lock:
            conn.execute(queries.get("share_feature_flag"), {"flag_name": flag_name}).fetchone()
        user = conn.execute(queries.get("feature_flag_workspace_user"),
                            {"workspace_key": workspace_key}).fetchone()
        if not FeatureFlagService.evaluate(
                conn, flag_name, workspace_key=workspace_key,
                user_id=user["user_id"] if user else None, settings=settings):
            raise HTTPException(403, "this feature is unavailable for this workspace")

    @staticmethod
    def list_flags(conn) -> list[dict[str, Any]]:
        FeatureFlagService.ensure_seed_flags(conn)
        rows = conn.execute(queries.get("list_feature_flags")).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["flagName"] = d["flag_name"]
            d["envCeilingKey"] = d["env_ceiling_key"]
            d["defaultEnabled"] = d["default_enabled"]
            d["killSwitchActive"] = d["kill_switch_active"]
            d["killReason"] = d["kill_reason"]
            d["overridesCount"] = d["overrides_count"]
            d["createdAt"] = iso(d["created_at"])
            d["updatedAt"] = iso(d["updated_at"])
            out.append(d)
        return out

    @staticmethod
    def list_overrides(conn, flag_name: str) -> list[dict[str, Any]]:
        rows = conn.execute(
            queries.get("list_feature_flag_overrides"),
            {"flag_name": flag_name},
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["overrideId"] = str(d["override_id"])
            d["flagName"] = d["flag_name"]
            d["scopeType"] = d["scope_type"]
            d["scopeId"] = d["scope_id"]
            d["actorUserId"] = str(d["actor_user_id"]) if d["actor_user_id"] else None
            d["updatedAt"] = iso(d["updated_at"])
            out.append(d)
        return out

    @staticmethod
    def set_override(
        conn,
        flag_name: str,
        scope_type: str,
        scope_id: str,
        enabled: bool,
        *,
        actor_user_id: UUID | None = None,
        reason: str | None = None,
        commit: bool = True,
        expected_updated_at: str | None = None,
        require_absent: bool = False,
    ) -> dict[str, Any]:
        if scope_type not in ("global", "cohort", "workspace"):
            raise HTTPException(422, f"invalid scope_type: {scope_type}")
        scope_id = scope_id.strip()
        if not scope_id:
            raise HTTPException(422, "override scope must contain text")
        if scope_type == "global":
            if scope_id not in {"*", "global"}:
                raise HTTPException(422, "global scope must be '*'")
            scope_id = "*"

        FeatureFlagService.ensure_seed_flags(conn, commit=False)
        if conn.execute(queries.get("lock_feature_flag"), {"flag_name": flag_name}).fetchone() is None:
            raise HTTPException(404, "feature flag not found")
        existing = next((row for row in conn.execute(
            queries.get("list_feature_flag_overrides"), {"flag_name": flag_name}).fetchall()
            if row["scope_type"] == scope_type and row["scope_id"] == scope_id), None)
        if require_absent and existing:
            raise HTTPException(409, "an override for this scope already exists; refresh and review it")
        _check_expected(existing, expected_updated_at)
        override_id = uuid4()
        row = conn.execute(
            queries.get("upsert_feature_flag_override"),
            {
                "override_id": override_id,
                "flag_name": flag_name,
                "scope_type": scope_type,
                "scope_id": scope_id,
                "enabled": enabled,
                "reason": reason,
                "actor_user_id": actor_user_id,
            },
        ).fetchone()
        if commit:
            conn.commit()
        d = dict(row)
        d["overrideId"] = str(d["override_id"])
        d["flagName"] = d["flag_name"]
        d["scopeType"] = d["scope_type"]
        d["scopeId"] = d["scope_id"]
        d["actorUserId"] = str(d["actor_user_id"]) if d["actor_user_id"] else None
        d["updatedAt"] = iso(d["updated_at"])
        return d

    @staticmethod
    def delete_override(conn, flag_name: str, scope_type: str, scope_id: str, *, commit=True,
                        expected_updated_at: str | None = None) -> None:
        if scope_type == "global" and scope_id == "global":
            scope_id = "*"
        conn.execute(queries.get("lock_feature_flag"), {"flag_name": flag_name})
        existing = next((row for row in conn.execute(
            queries.get("list_feature_flag_overrides"), {"flag_name": flag_name}).fetchall()
            if row["scope_type"] == scope_type and row["scope_id"] == scope_id), None)
        _check_expected(existing, expected_updated_at)
        conn.execute(
            queries.get("delete_feature_flag_override"),
            {"flag_name": flag_name, "scope_type": scope_type, "scope_id": scope_id},
        )
        if commit:
            conn.commit()

    @staticmethod
    def set_kill_switch(
        conn,
        flag_name: str,
        kill_switch_active: bool,
        reason: str | None = None,
        *, commit: bool = True, expected_updated_at: str | None = None,
    ) -> dict[str, Any]:
        FeatureFlagService.ensure_seed_flags(conn, commit=False)
        current = conn.execute(queries.get("lock_feature_flag"), {"flag_name": flag_name}).fetchone()
        if current is None:
            raise HTTPException(404, "feature flag not found")
        _check_expected(current, expected_updated_at)
        row = conn.execute(
            queries.get("set_feature_flag_kill_switch"),
            {
                "flag_name": flag_name,
                "kill_switch_active": kill_switch_active,
                "kill_reason": reason,
            },
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"feature flag not found: {flag_name}")
        if commit:
            conn.commit()
        d = dict(row)
        d["flagName"] = d["flag_name"]
        d["envCeilingKey"] = d["env_ceiling_key"]
        d["defaultEnabled"] = d["default_enabled"]
        d["killSwitchActive"] = d["kill_switch_active"]
        d["killReason"] = d["kill_reason"]
        d["overridesCount"] = len(FeatureFlagService.list_overrides(conn, flag_name))
        d["updatedAt"] = iso(d["updated_at"])
        return d

    @staticmethod
    def list_cohorts(conn) -> list[dict[str, Any]]:
        rows = conn.execute(queries.get("list_cohorts")).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["cohortName"] = d["cohort_name"]
            d["memberCount"] = d["member_count"]
            out.append(d)
        return out

    @staticmethod
    def list_cohort_members(conn, cohort_name: str, *, limit=50, offset=0):
        rows = conn.execute(queries.get("list_cohort_members"), {
            "cohort_name": cohort_name, "limit": limit, "offset": offset}).fetchall()
        return [{"userId": row["user_id"], "displayName": row["display_name"],
                 "email": row["email"], "role": row["role"],
                 "assignedAt": iso(row["created_at"])} for row in rows]

    @staticmethod
    def assign_cohort(conn, user_id: UUID, cohort_name: str, assigned_by: UUID | None = None,
                      *, commit: bool = True) -> None:
        cohort_name = cohort_name.strip()
        if not cohort_name or len(cohort_name) > 100:
            raise HTTPException(422, "cohort name must contain 1 to 100 characters")
        if conn.execute(queries.get("admin_get_user"), {"user_id": user_id}).fetchone() is None:
            raise HTTPException(404, "user not found")
        conn.execute(
            queries.get("add_user_cohort"),
            {"user_id": user_id, "cohort_name": cohort_name, "assigned_by": assigned_by},
        )
        if commit:
            conn.commit()

    @staticmethod
    def remove_cohort(conn, user_id: UUID, cohort_name: str, *, commit: bool = True) -> None:
        conn.execute(
            queries.get("remove_user_cohort"),
            {"user_id": user_id, "cohort_name": cohort_name},
        )
        if commit:
            conn.commit()
