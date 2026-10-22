import base64
import csv
import io
import json
from datetime import UTC, datetime
from typing import Any
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
        if conversation["workspace_key"] != self.principal.workspace_key:
            from app.auth.capabilities import check_workspace_access
            if not check_workspace_access(
                self.conn,
                self.principal,
                conversation["workspace_key"],
                resource_type="conversation",
                resource_id=str(conversation_id),
                action="read",
            ):
                raise HTTPException(
                    403,
                    "access to student transcript requires an active support grant approved by another administrator",
                )
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

    def create_grant(
        self,
        *,
        grant_type: str,
        actor_user_id: UUID,
        target_workspace_key: str,
        resource_type: str,
        resource_id: str | None = None,
        reason: str,
        expires_at: datetime,
        allowed_actions: list[str] | None = None,
    ) -> dict:
        if grant_type not in {"advisor_access", "support_access"}:
            raise HTTPException(422, "invalid grant type")
        if resource_type not in {"conversation", "plan", "all_records"}:
            raise HTTPException(422, "invalid resource type")
        if grant_type == "support_access" and self.principal.user_id == actor_user_id:
            raise HTTPException(409, "cannot self-approve support access grants")
        if self.principal.user_id is None:
            raise HTTPException(403, "authenticated actor required to issue grants")
        actions = allowed_actions or ["read"]
        if not actions or set(actions) - {"read", "write", "verify"}:
            raise HTTPException(422, "invalid grant action")
        if grant_type == "support_access" and set(actions) != {"read"}:
            raise HTTPException(422, "support access is read-only")
        if "verify" in actions and resource_type != "all_records":
            raise HTTPException(422, "verification grants must target academic records")
        if expires_at <= datetime.now(UTC):
            raise HTTPException(422, "grant expiry must be in the future")
        recipient = self.conn.execute(queries.get("admin_get_user"), {
            "user_id": actor_user_id,
        }).fetchone()
        permitted_roles = {"advisor", "admin"} if grant_type == "advisor_access" else {"admin"}
        if not recipient or recipient["status"] != "active" or recipient["role"] not in permitted_roles:
            raise HTTPException(422, "access grants require an active authorized staff recipient")
        grant_id = uuid4()
        with self.conn.transaction():
            self.conn.execute(queries.get("insert_resource_grant"), {
                "grant_id": grant_id,
                "grant_type": grant_type,
                "actor_user_id": actor_user_id,
                "target_workspace_key": target_workspace_key,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "allowed_actions": actions,
                "reason": reason,
                "approved_by_user_id": self.principal.user_id,
                "expires_at": expires_at,
            })
            self.record_audit(
                "grant.created", "resource_grant", str(grant_id),
                {
                    "actor_user_id": str(actor_user_id),
                    "target_workspace": target_workspace_key,
                    "grant_type": grant_type,
                    "expires_at": expires_at.isoformat(),
                },
            )
        full = self.conn.execute(queries.get("get_resource_grant"), {"grant_id": grant_id}).fetchone()
        return dict(full)

    def list_grants(
        self,
        *,
        target_workspace_key: str = "",
        actor_user_id: UUID | None = None,
        grant_type: str = "",
        active_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        limit = _limit(limit, maximum=200)
        rows = self.conn.execute(queries.get("list_resource_grants"), {
            "target_workspace_key": target_workspace_key,
            "actor_user_id": actor_user_id,
            "grant_type": grant_type,
            "active_only": active_only,
            "limit": limit,
            "offset": offset,
        }).fetchall()
        self.conn.commit()
        return [dict(row) for row in rows]

    def revoke_grant(self, grant_id: UUID) -> dict:
        with self.conn.transaction():
            row = self.conn.execute(queries.get("revoke_resource_grant"), {
                "grant_id": grant_id,
                "revoked_by_user_id": self.principal.user_id,
            }).fetchone()
            if row is None:
                raise HTTPException(404, "grant not found or already revoked")
            self.record_audit("grant.revoked", "resource_grant", str(grant_id))
        full = self.conn.execute(queries.get("get_resource_grant"), {"grant_id": grant_id}).fetchone()
        return dict(full)

    def _support_recipient(self, actor_user_id: UUID) -> None:
        recipient = self.conn.execute(queries.get("admin_get_user"), {
            "user_id": actor_user_id,
        }).fetchone()
        if not recipient or recipient["status"] != "active" or recipient["role"] != "admin":
            raise HTTPException(422, "support access requires an active administrator recipient")

    def request_support_grant(
        self,
        *,
        target_workspace_key: str,
        resource_type: str,
        resource_id: str | None,
        reason: str,
        expires_at: datetime,
    ) -> dict:
        if self.principal.user_id is None:
            raise HTTPException(403, "authenticated actor required to request support access")
        if resource_type not in {"conversation", "plan", "all_records"}:
            raise HTTPException(422, "invalid resource type")
        if expires_at <= datetime.now(UTC):
            raise HTTPException(422, "grant expiry must be in the future")
        self._support_recipient(self.principal.user_id)
        request_id = uuid4()
        with self.conn.transaction():
            self.conn.execute(queries.get("insert_support_grant_request"), {
                "request_id": request_id,
                "requester_user_id": self.principal.user_id,
                "actor_user_id": self.principal.user_id,
                "target_workspace_key": target_workspace_key,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "allowed_actions": ["read"],
                "reason": reason,
                "expires_at": expires_at,
            })
            self.record_audit(
                "grant.requested", "support_grant_request", str(request_id),
                {"target_workspace": target_workspace_key, "resource_type": resource_type},
            )
        return dict(self.conn.execute(
            queries.get("get_support_grant_request"), {"request_id": request_id},
        ).fetchone())

    def list_support_grant_requests(
        self, *, status: str = "", limit: int = 50, offset: int = 0,
    ) -> list[dict]:
        rows = self.conn.execute(queries.get("list_support_grant_requests"), {
            "status": status,
            "limit": _limit(limit),
            "offset": offset,
        }).fetchall()
        self.conn.commit()
        return [dict(row) for row in rows]

    def update_support_grant_request(
        self,
        request_id: UUID,
        *,
        target_workspace_key: str,
        resource_type: str,
        resource_id: str | None,
        reason: str,
        expires_at: datetime,
        expected_revision: int,
    ) -> dict:
        if resource_type not in {"conversation", "plan", "all_records"}:
            raise HTTPException(422, "invalid resource type")
        if expires_at <= datetime.now(UTC):
            raise HTTPException(422, "grant expiry must be in the future")
        with self.conn.transaction():
            current = self.conn.execute(
                queries.get("get_support_grant_request_for_update"),
                {"request_id": request_id},
            ).fetchone()
            if current is None:
                raise HTTPException(404, "support grant request not found")
            if current["status"] != "pending":
                raise HTTPException(409, "support grant request is no longer pending")
            if current["actor_user_id"] != self.principal.user_id:
                raise HTTPException(403, "only the requesting administrator can edit this request")
            if current["revision"] != expected_revision:
                raise HTTPException(
                    409,
                    "support grant request changed; refresh and review the pending request",
                )
            updated = self.conn.execute(queries.get("update_support_grant_request"), {
                "request_id": request_id,
                "reason": reason,
                "expires_at": expires_at,
                "target_workspace_key": target_workspace_key,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "expected_revision": expected_revision,
            }).fetchone()
            if updated is None:
                raise HTTPException(
                    409,
                    "support grant request changed; refresh and review the pending request",
                )
            self.record_audit("grant.request_updated", "support_grant_request", str(request_id))
        return dict(self.conn.execute(
            queries.get("get_support_grant_request"), {"request_id": request_id},
        ).fetchone())

    def approve_support_grant_request(
        self, request_id: UUID, *, expected_revision: int,
    ) -> dict:
        with self.conn.transaction():
            current = self.conn.execute(
                queries.get("get_support_grant_request_for_update"),
                {"request_id": request_id},
            ).fetchone()
            if current is None:
                raise HTTPException(404, "support grant request not found")
            if current["status"] != "pending":
                raise HTTPException(409, "support grant request is no longer pending")
            if current["revision"] != expected_revision:
                raise HTTPException(
                    409,
                    "support grant request changed; refresh and review the pending request",
                )
            grant = self.create_grant(
                grant_type="support_access",
                actor_user_id=current["actor_user_id"],
                target_workspace_key=current["target_workspace_key"],
                resource_type=current["resource_type"],
                resource_id=current["resource_id"],
                reason=current["reason"],
                expires_at=current["expires_at"],
                allowed_actions=["read"],
            )
            decided = self.conn.execute(queries.get("decide_support_grant_request"), {
                "request_id": request_id,
                "status": "approved",
                "decision_note": None,
                "decided_by_user_id": self.principal.user_id,
                "grant_id": grant["grant_id"],
                "expected_revision": expected_revision,
            }).fetchone()
            if decided is None:
                raise HTTPException(
                    409,
                    "support grant request changed; refresh and review the pending request",
                )
            self.record_audit(
                "grant.request_approved", "support_grant_request", str(request_id),
                {"grant_id": str(grant["grant_id"])},
            )
        return dict(self.conn.execute(
            queries.get("get_support_grant_request"), {"request_id": request_id},
        ).fetchone())

    def reject_support_grant_request(
        self, request_id: UUID, *, expected_revision: int, note: str,
    ) -> dict:
        if self.principal.user_id is None:
            raise HTTPException(403, "authenticated actor required to reject support access")
        with self.conn.transaction():
            current = self.conn.execute(
                queries.get("get_support_grant_request_for_update"),
                {"request_id": request_id},
            ).fetchone()
            if current is None:
                raise HTTPException(404, "support grant request not found")
            if current["status"] != "pending":
                raise HTTPException(409, "support grant request is no longer pending")
            if current["revision"] != expected_revision:
                raise HTTPException(
                    409,
                    "support grant request changed; refresh and review the pending request",
                )
            if current["actor_user_id"] == self.principal.user_id:
                raise HTTPException(409, "cannot self-approve support access grants")
            decided = self.conn.execute(queries.get("decide_support_grant_request"), {
                "request_id": request_id,
                "status": "rejected",
                "decision_note": note,
                "decided_by_user_id": self.principal.user_id,
                "grant_id": None,
                "expected_revision": expected_revision,
            }).fetchone()
            if decided is None:
                raise HTTPException(
                    409,
                    "support grant request changed; refresh and review the pending request",
                )
            self.record_audit("grant.request_rejected", "support_grant_request", str(request_id))
        return dict(self.conn.execute(
            queries.get("get_support_grant_request"), {"request_id": request_id},
        ).fetchone())

    def transcript_download(self, conversation_id: UUID) -> str:
        """Return transcript text only after the same grant check as a live read."""
        value = self.transcript(conversation_id)
        lines = [value["conversation"]["title"] or "Conversation"]
        for turn in value["turns"]:
            role = turn.get("role") or turn.get("kind") or "turn"
            text = turn.get("text") or turn.get("content") or ""
            lines.append(f"{role}: {text}")
        return "\n".join(lines) + "\n"

    def stage_granted_transcript(self, conversation_id: UUID) -> dict:
        if self.principal.user_id is None:
            raise HTTPException(403, "authenticated actor required to stage a granted result")
        conversation = self.conn.execute(queries.get("admin_get_conversation"), {
            "conversation_id": conversation_id,
        }).fetchone()
        if conversation is None:
            raise HTTPException(404, "conversation not found")
        if conversation["workspace_key"] == self.principal.workspace_key:
            raise HTTPException(422, "granted publication is only for another workspace")
        grant = self.conn.execute(queries.get("find_active_support_grant"), {
            "actor_user_id": self.principal.user_id,
            "target_workspace_key": conversation["workspace_key"],
            "resource_type": "conversation",
            "resource_id": str(conversation_id),
        }).fetchone()
        if grant is None:
            raise HTTPException(
                403,
                "access to student transcript requires an active support grant approved by another administrator",
            )
        staging_id = uuid4()
        with self.conn.transaction():
            row = self.conn.execute(queries.get("insert_granted_result_staging"), {
                "staging_id": staging_id,
                "grant_id": grant["grant_id"],
                "actor_user_id": self.principal.user_id,
                "target_workspace_key": conversation["workspace_key"],
                "resource_type": "conversation",
                "resource_id": str(conversation_id),
            }).fetchone()
            self.record_audit(
                "grant.result_staged", "granted_result", str(staging_id),
                {"grant_id": str(grant["grant_id"]), "conversation_id": str(conversation_id)},
            )
        return dict(row)

    def publish_granted_result(self, staging_id: UUID) -> str:
        """Release a staged result only when its support grant is still active."""
        from app.auth.grants import assert_support_grant_publishable

        with self.conn.transaction():
            staged = self.conn.execute(
                queries.get("get_granted_result_staging_for_update"),
                {"staging_id": staging_id},
            ).fetchone()
            if staged is None:
                raise HTTPException(404, "staged result not found")
            if staged["actor_user_id"] != self.principal.user_id:
                raise HTTPException(403, "staged result belongs to another administrator")
            if staged["status"] != "staged":
                raise HTTPException(409, f"staged result is already {staged['status']}")
            try:
                assert_support_grant_publishable(self.conn, staged["grant_id"])
            except HTTPException:
                self.conn.execute(queries.get("mark_granted_result_staging"), {
                    "staging_id": staging_id, "status": "withheld",
                })
                self.record_audit(
                    "grant.result_withheld", "granted_result", str(staging_id),
                    {"grant_id": str(staged["grant_id"])},
                )
                withheld = True
            else:
                withheld = False
                self.conn.execute(queries.get("mark_granted_result_staging"), {
                    "staging_id": staging_id, "status": "published",
                })
                self.record_audit("grant.result_published", "granted_result", str(staging_id))
        if withheld:
            raise HTTPException(403, "support grant is missing, expired, or revoked")
        return self.transcript_download(UUID(staged["resource_id"]))
