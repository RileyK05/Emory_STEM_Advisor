"""Admin-scoped reads and writes with an explicit audit trail."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from app.auth.models import Principal
from app.conversations.store import public_turn
from app.db import queries


def _limit(value: int, *, maximum: int = 200) -> int:
    if not 1 <= value <= maximum:
        raise HTTPException(422, f"limit must be between 1 and {maximum}")
    return value


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

    def audit_log(self, *, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(queries.get("admin_list_audit"), {
            "limit": _limit(limit, maximum=500),
        }).fetchall()
        self.conn.commit()
        return [dict(row) for row in rows]

    def conversations(self, *, workspace: str = "", query: str = "",
                      limit: int = 50, offset: int = 0) -> list[dict]:
        if offset < 0:
            raise HTTPException(422, "offset must be non-negative")
        normalized = query.strip()
        rows = self.conn.execute(queries.get("admin_list_conversations"), {
            "workspace": workspace, "query_like": f"%{normalized.lower()}%" if normalized else "",
            "limit": _limit(limit), "offset": offset,
        }).fetchall()
        self.conn.commit()
        return [dict(row) for row in rows]

    def transcript(self, conversation_id: UUID) -> dict:
        conversation = self.conn.execute(queries.get("admin_get_conversation"), {
            "conversation_id": conversation_id,
        }).fetchone()
        if conversation is None:
            raise HTTPException(404, "conversation not found")
        rows = self.conn.execute(queries.get("admin_list_conversation_turns"), {
            "conversation_id": conversation_id,
        }).fetchall()
        # Content access is an audited action, distinct from metadata listing.
        self.record_audit("conversation.read", "conversation", str(conversation_id),
                          {"workspace": conversation["workspace_key"]})
        self.conn.commit()
        return {"conversation": dict(conversation),
                "turns": [public_turn(row) for row in rows]}

    def delete_conversation(self, conversation_id: UUID) -> dict:
        with self.conn.transaction():
            conversation = self.conn.execute(queries.get("admin_get_conversation"), {
                "conversation_id": conversation_id,
            }).fetchone()
            if conversation is None:
                raise HTTPException(404, "conversation not found")
            turns = self.conn.execute(queries.get("admin_delete_conversation_turns"), {
                "conversation_id": conversation_id,
            }).rowcount
            self.conn.execute(queries.get("admin_delete_conversation_summaries"), {
                "conversation_id": conversation_id,
            })
            requests = self.conn.execute(queries.get("admin_delete_conversation_requests"), {
                "conversation_id": conversation_id,
            }).fetchall()
            job_ids = [row["job_id"] for row in requests if row["job_id"] is not None]
            if job_ids:
                self.conn.execute(queries.get("admin_delete_jobs"), {"job_ids": job_ids})
            deleted = self.conn.execute(queries.get("admin_delete_conversation"), {
                "conversation_id": conversation_id,
            }).fetchone()
            if deleted is None:
                raise HTTPException(404, "conversation not found")
            self.record_audit(
                "conversation.deleted", "conversation", str(conversation_id),
                {"workspace": conversation["workspace_key"], "turns": turns,
                 "jobs": len(job_ids)},
            )
        return {"turns": turns, "jobs": len(job_ids)}

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

    def artifacts(self, *, limit: int = 100, offset: int = 0) -> list[dict]:
        if offset < 0:
            raise HTTPException(422, "offset must be non-negative")
        rows = self.conn.execute(queries.get("admin_list_artifacts"), {
            "limit": _limit(limit, maximum=500), "offset": offset,
        }).fetchall()
        self.conn.commit()
        return [dict(row) for row in rows]

    def collections(self, *, limit: int = 100, offset: int = 0) -> list[dict]:
        if offset < 0:
            raise HTTPException(422, "offset must be non-negative")
        rows = self.conn.execute(queries.get("admin_list_collections"), {
            "limit": _limit(limit, maximum=500), "offset": offset,
        }).fetchall()
        self.conn.commit()
        return [dict(row) for row in rows]

    @staticmethod
    def _collection_name(value: str) -> str:
        name = value.strip()
        if not 1 <= len(name) <= 200:
            raise HTTPException(422, "collection name must be between 1 and 200 characters")
        return name

    def create_collection(self, name: str) -> dict:
        name = self._collection_name(name)
        collection_id = uuid4()
        try:
            with self.conn.transaction():
                row = self.conn.execute(queries.get("admin_create_collection"), {
                    "collection_id": collection_id, "name": name,
                }).fetchone()
                self.record_audit("collection.created", "collection", str(collection_id),
                                  {"name": name})
        except UniqueViolation:
            raise HTTPException(409, "a collection with that name already exists") from None
        return {**dict(row), "source_count": 0}

    def rename_collection(self, collection_id: UUID, name: str,
                          *, protected_name: str | None = None) -> dict:
        name = self._collection_name(name)
        current = self.conn.execute(queries.get("admin_get_collection"), {
            "collection_id": collection_id,
        }).fetchone()
        if current is None:
            self.conn.commit()
            raise HTTPException(404, "collection not found")
        if protected_name and current["name"] == protected_name:
            self.conn.commit()
            raise HTTPException(409, "the default collection cannot be renamed")
        try:
            with self.conn.transaction():
                row = self.conn.execute(queries.get("admin_rename_collection"), {
                    "collection_id": collection_id, "name": name,
                }).fetchone()
                if row is None:
                    raise HTTPException(404, "collection not found")
                self.record_audit("collection.renamed", "collection", str(collection_id),
                                  {"name": name})
        except UniqueViolation:
            raise HTTPException(409, "a collection with that name already exists") from None
        count = self.conn.execute(queries.get("admin_collection_source_count"), {
            "collection_id": collection_id,
        }).fetchone()["sources"]
        self.conn.commit()
        return {**dict(row), "source_count": count}

    def delete_collection(self, collection_id: UUID,
                          *, protected_name: str | None = None) -> None:
        current = self.conn.execute(queries.get("admin_get_collection"), {
            "collection_id": collection_id,
        }).fetchone()
        if current is None:
            self.conn.commit()
            raise HTTPException(404, "collection not found")
        if protected_name and current["name"] == protected_name:
            self.conn.commit()
            raise HTTPException(409, "the default collection cannot be deleted")
        with self.conn.transaction():
            sources = self.conn.execute(queries.get("admin_collection_source_count"), {
                "collection_id": collection_id,
            }).fetchone()["sources"]
            plans = self.conn.execute(queries.get("admin_collection_plan_count"), {
                "collection_id": collection_id,
            }).fetchone()["plans"]
            if sources or plans:
                raise HTTPException(
                    409,
                    "collection is not empty; move or remove its sources and artifact runs first",
                )
            row = self.conn.execute(queries.get("admin_delete_collection"), {
                "collection_id": collection_id,
            }).fetchone()
            if row is None:
                raise HTTPException(404, "collection not found")
            self.record_audit("collection.deleted", "collection", str(collection_id), {})
        self.conn.commit()
