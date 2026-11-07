import base64
from datetime import UTC, datetime
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
        if role not in {"student", "admin"}:
            raise HTTPException(422, "role must be student or admin")
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

