import base64
import csv
import io
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app.auth.models import Principal
from app.db import queries


def _limit(value: int, *, maximum: int = 200) -> int:
    if not 1 <= value <= maximum:
        raise HTTPException(422, f"limit must be between 1 and {maximum}")
    return value


def encode_audit_cursor(created_at: datetime, audit_id: UUID) -> str:
    raw = f"{created_at.isoformat()}|{audit_id}"
    return base64.urlsafe_b64encode(raw.encode("ascii")).decode("ascii")


def decode_audit_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        dt_str, id_str = raw.split("|", 1)
        return datetime.fromisoformat(dt_str), UUID(id_str)
    except (ValueError, TypeError, UnicodeDecodeError):
        raise HTTPException(422, "invalid audit cursor") from None



def sanitize_csv_cell(value: Any) -> str:
    """Neutralize spreadsheet-formula prefixes in CSV cells (CWE-1236)."""
    if value is None:
        return ""
    text = str(value)
    if text and text[0] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{text}"
    return text


def export_audit_csv(rows: list[dict]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "audit_id", "created_at", "actor_user_id", "actor_email",
        "actor_display_name", "actor_workspace", "action", "target_type",
        "target_id", "detail",
    ])
    for row in rows:
        writer.writerow([
            sanitize_csv_cell(row["audit_id"]),
            sanitize_csv_cell(row["created_at"].isoformat() if row.get("created_at") else ""),
            sanitize_csv_cell(row.get("actor_user_id")),
            sanitize_csv_cell(row.get("actor_email")),
            sanitize_csv_cell(row.get("actor_display_name")),
            sanitize_csv_cell(row.get("actor_workspace")),
            sanitize_csv_cell(row.get("action")),
            sanitize_csv_cell(row.get("target_type")),
            sanitize_csv_cell(row.get("target_id")),
            sanitize_csv_cell(json.dumps(row.get("detail", {}), sort_keys=True)),
        ])
    return output.getvalue()


def export_audit_json(rows: list[dict]) -> str:
    entries = []
    for row in rows:
        entries.append({
            "auditId": str(row["audit_id"]),
            "actorUserId": str(row["actor_user_id"]) if row.get("actor_user_id") else None,
            "actorWorkspace": row["actor_workspace"],
            "action": row["action"],
            "targetType": row["target_type"],
            "targetId": row.get("target_id"),
            "detail": row.get("detail", {}),
            "createdAt": row["created_at"].isoformat() if row.get("created_at") else None,
            "actorEmail": row.get("actor_email"),
            "actorDisplayName": row.get("actor_display_name"),
        })
    return json.dumps(entries, indent=2)


class AdminStore:
    def __init__(self, conn, principal: Principal):
        self.conn = conn
        self.principal = principal

    def record_audit(self, action: str, target_type: str, target_id: str | None = None,
                     detail: dict | None = None) -> None:
        self.conn.execute(queries.get("admin_insert_audit"), {
            "audit_id": uuid4(), "actor_user_id": self.principal.user_id,
            "actor_workspace": self.principal.workspace_key, "action": action,
            "target_type": target_type, "target_id": target_id,
            "detail": Jsonb(detail or {}),
        })

    def overview(self) -> dict:
        row = self.conn.execute(queries.get("admin_overview")).fetchone()
        self.conn.commit()
        return dict(row)

    def users(self, *, query: str = "", role: str = "", status: str = "",
              limit: int = 50, offset: int = 0) -> list[dict]:
        limit = _limit(limit)
        if offset < 0:
            raise HTTPException(422, "offset must be non-negative")
        normalized = query.strip()
        rows = self.conn.execute(queries.get("admin_list_users"), {
            "query_like": f"%{normalized.lower()}%" if normalized else "",
            "query_exact": normalized, "role": role, "status": status,
            "limit": limit, "offset": offset,
        }).fetchall()
        self.conn.commit()
        return [dict(row) for row in rows]

    def user(self, user_id: UUID) -> dict:
        row = self.conn.execute(queries.get("admin_get_user"), {"user_id": user_id}).fetchone()
        self.conn.commit()
        if row is None:
            raise HTTPException(404, "user not found")
        return dict(row)

    def user_sessions(self, user_id: UUID) -> list[dict]:
        user = self.user(user_id)
        rows = self.conn.execute(queries.get("admin_list_user_sessions"), {
            "workspace_key": user["workspace_key"],
        }).fetchall()
        self.conn.commit()
        return [dict(row) for row in rows]

    def update_user(self, user_id: UUID, *, role: str, status: str) -> dict:
        if role not in {"student", "advisor", "admin", "auditor"}:
            raise HTTPException(422, "role must be student, advisor, admin, or auditor")
        if status not in {"active", "disabled"}:
            raise HTTPException(422, "status must be active or disabled")
        with self.conn.transaction():
            # The active-admin count is protected by the table lock, not by a
            # lock on only the target row: two concurrent demotions otherwise
            # each observe two admins and both commit.
            self.conn.execute(queries.get("admin_lock_user_table"))
            row = self.conn.execute(queries.get("admin_lock_user"), {"user_id": user_id}).fetchone()
            if row is None:
                raise HTTPException(404, "user not found")
            removing_admin = (row["role"] == "admin" and row["status"] == "active"
                              and (role != "admin" or status != "active"))
            if removing_admin:
                admins = self.conn.execute(
                    queries.get("admin_count_active_admins")).fetchone()["admins"]
                if admins <= 1:
                    raise HTTPException(409, "the last active administrator cannot be demoted")
            if self.principal.user_id == user_id and (role != "admin" or status != "active"):
                raise HTTPException(409, "you cannot demote or disable your own account")
            updated = self.conn.execute(queries.get("admin_update_user"), {
                "user_id": user_id, "role": role, "status": status,
            }).fetchone()
            self.record_audit(
                "user.role_status", "user", str(user_id),
                {"before": {"role": row["role"], "status": row["status"]},
                 "after": {"role": role, "status": status}},
            )
        return dict(updated)

    def revoke_sessions(self, workspace_key: str) -> int:
        rows = self.conn.execute(queries.get("admin_revoke_workspace_sessions"), {
            "workspace_key": workspace_key,
        }).fetchall()
        self.record_audit("user.sessions_revoked", "workspace", workspace_key,
                          {"sessions": len(rows)})
        self.conn.commit()
        return len(rows)

    def audit_log(
        self,
        *,
        action: str = "",
        target_type: str = "",
        target_id: str = "",
        actor: str = "",
        since: datetime | None = None,
        until: datetime | None = None,
        cursor: str = "",
        limit: int = 100,
    ) -> tuple[list[dict], str | None]:
        limit = _limit(limit, maximum=500)
        cursor_created_at: datetime | None = None
        cursor_audit_id: UUID | None = None
        if cursor.strip():
            cursor_created_at, cursor_audit_id = decode_audit_cursor(cursor.strip())

        actor_str = actor.strip()
        actor_user_id: UUID | None = None
        if actor_str:
            try:
                actor_user_id = UUID(actor_str)
            except ValueError:
                pass

        params = {
            "action": action.strip(),
            "target_type": target_type.strip(),
            "target_id": target_id.strip(),
            "actor": actor_str,
            "actor_user_id": actor_user_id,
            "actor_like": f"%{actor_str.lower()}%" if actor_str else "",
            "since": since,
            "until": until,
            "cursor_created_at": cursor_created_at,
            "cursor_audit_id": cursor_audit_id,
            "limit": limit,
        }
        rows = self.conn.execute(queries.get("admin_list_audit_filtered"), params).fetchall()
        self.conn.commit()
        entries = [dict(row) for row in rows]
        next_cursor = None
        if len(entries) == limit:
            last = entries[-1]
            next_cursor = encode_audit_cursor(last["created_at"], last["audit_id"])
        return entries, next_cursor

    def export_audit(
        self,
        *,
        format: str = "csv",
        action: str = "",
        target_type: str = "",
        target_id: str = "",
        actor: str = "",
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 5000,
    ) -> tuple[str, str, datetime]:
        if format not in {"csv", "json"}:
            raise HTTPException(422, "format must be csv or json")
        limit = _limit(limit, maximum=10000)
        snapshot_until = until or datetime.now(UTC)

        actor_str = actor.strip()
        actor_user_id: UUID | None = None
        if actor_str:
            try:
                actor_user_id = UUID(actor_str)
            except ValueError:
                pass

        params = {
            "action": action.strip(),
            "target_type": target_type.strip(),
            "target_id": target_id.strip(),
            "actor": actor_str,
            "actor_user_id": actor_user_id,
            "actor_like": f"%{actor_str.lower()}%" if actor_str else "",
            "since": since,
            "until": snapshot_until,
            "cursor_created_at": None,
            "cursor_audit_id": None,
            "limit": limit,
        }
        rows = self.conn.execute(queries.get("admin_list_audit_filtered"), params).fetchall()
        entries = [dict(row) for row in rows]

        # Audit export action without creating recursive export churn:
        # snapshot_until bounds the export before this row was created.
        self.record_audit(
            "audit.export",
            "audit",
            None,
            {
                "format": format,
                "count": len(entries),
                "action": action,
                "target_type": target_type,
                "since": since.isoformat() if since else None,
                "until": snapshot_until.isoformat(),
            },
        )
        self.conn.commit()

        if format == "csv":
            content = export_audit_csv(entries)
            media_type = "text/csv; charset=utf-8"
        else:
            content = export_audit_json(entries)
            media_type = "application/json"
        return content, media_type, snapshot_until


    def jobs(self, *, status: str = "", limit: int = 100,
             offset: int = 0) -> list[dict]:
        if offset < 0:
            raise HTTPException(422, "offset must be non-negative")
        rows = self.conn.execute(queries.get("admin_list_jobs"), {
            "status": status, "limit": _limit(limit, maximum=500), "offset": offset,
        }).fetchall()
        self.conn.commit()
        return [dict(row) for row in rows]

    def cancel_job(self, job_id: UUID) -> dict:
        now = datetime.now(UTC)
        row = self.conn.execute(queries.get("request_job_cancel"), {
            "job_id": job_id, "now": now,
        }).fetchone()
        if row is None:
            existing = self.conn.execute(queries.get("get_job"), {"job_id": job_id}).fetchone()
            if existing is None:
                raise HTTPException(404, "job not found")
            self.conn.commit()
            return dict(existing)
        event = "cancelled" if row["status"] == "cancelled" else "cancel_requested"
        self.conn.execute(queries.get("append_job_event"), {
            "job_id": job_id, "event_type": event,
            "payload": Jsonb({"status": row["status"]}), "now": now,
        })
        self.record_audit("job.cancel", "job", str(job_id), {"status": row["status"]})
        self.conn.commit()
        return dict(row)

    def sources(self, *, workspace: str = "", limit: int = 100,
                offset: int = 0) -> list[dict]:
        if offset < 0:
            raise HTTPException(422, "offset must be non-negative")
        rows = self.conn.execute(queries.get("admin_list_sources"), {
            "workspace": workspace, "limit": _limit(limit, maximum=500),
            "offset": offset,
        }).fetchall()
        self.conn.commit()
        return [dict(row) for row in rows]

