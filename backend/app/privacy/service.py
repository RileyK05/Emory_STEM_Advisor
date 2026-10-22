"""Privacy service: student data inventory, export bundling, and fenced deletion."""

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app.conversations.store import ConversationStore
from app.db import queries
from app.privacy.fence import require_live_workspace
from app.util.localpath import io_path

_DELETE_STEPS = (
    "delete_workspace_comparison_members",
    "delete_workspace_comparisons",
    "delete_workspace_feedback_events",
    "delete_workspace_feedback",
    "delete_workspace_research",
    "delete_workspace_summaries",
    # Capture legacy trace ids through turn evidence before conversations go.
    "delete_workspace_legacy_retrieval_traces",
    "delete_workspace_retrieval_traces",
    "delete_workspace_conversations",
    "delete_workspace_artifacts",
    "delete_workspace_gaps",
    "delete_workspace_gap_observations",
    "delete_workspace_debug_experiments",
    "delete_workspace_claim_reviews",
    "delete_workspace_decisions",
    "delete_workspace_provider_batch_items",
    "delete_workspace_provider_batches",
    "delete_workspace_tool_executions",
    "delete_workspace_tool_execution_operations",
    # Jobs own artifact-run plans/units; remove them before operations because
    # artifact_run_unit_calls restricts operation_calls.
    "delete_workspace_jobs",
    # Private uploads, after the jobs whose run plans pinned them.
    "delete_workspace_sources",
    "delete_workspace_operations",
    "delete_workspace_academic_records",
    "delete_workspace_quota_settlements",
    "delete_workspace_quota_buckets",
    "delete_workspace_privacy_exports",
)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)


def _plain_row(row) -> dict[str, Any]:
    item = dict(row)
    for key, value in list(item.items()):
        if isinstance(value, UUID):
            item[key] = str(value)
        elif isinstance(value, datetime):
            item[key] = _iso(value)
    return item


def privacy_export_digest(manifest: Any) -> str:
    """Checksum of the stored export manifest. The digest is not part of a bearer token."""
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _token_matches(stored: str | None, presented: str | None) -> bool:
    if not stored or not presented or not presented.isascii() or len(stored) != len(presented):
        return False
    return secrets.compare_digest(stored, presented)


def _inventory(conn, workspace_key: str) -> dict[str, Any]:
    row = conn.execute(
        queries.get("count_workspace_inventory"),
        {"workspace_key": workspace_key},
    ).fetchone()
    return dict(row) if row else {}


def _original_paths(conn, workspace_key: str) -> list[str]:
    """Stored files the workspace owns: artifact originals and private source uploads."""
    paths: list[str] = []
    for name in ("list_workspace_artifact_originals", "list_workspace_source_originals"):
        rows = conn.execute(queries.get(name), {"workspace_key": workspace_key}).fetchall()
        paths.extend(row["original_path"] for row in rows if row["original_path"])
    return paths


def _purge_originals(paths: list[str], data_dir: Path | None) -> None:
    """Remove local originals. Missing files stay missing; nothing is written back."""
    roots = ([(Path(data_dir) / sub).resolve() for sub in ("artifacts/originals", "raw")]
             if data_dir is not None else None)
    for raw in paths:
        path = Path(raw)
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if roots is not None and not any(resolved.is_relative_to(root) for root in roots):
            continue
        target = io_path(resolved)
        if target.is_file():
            target.unlink()


class PrivacyService:
    @staticmethod
    def start_export(
        conn,
        workspace_key: str,
        user_id: UUID | None = None,
        *,
        retention_hours: int = 24,
    ) -> dict[str, Any]:
        """Generate a documented export manifest and data bundle for an owned workspace."""
        tombstone = conn.execute(
            queries.get("check_deletion_tombstone"),
            {
                "workspace_key": workspace_key,
                "resource_type": "workspace",
                "resource_id": workspace_key,
            },
        ).fetchone()
        if tombstone is not None:
            raise HTTPException(410, "workspace has been deleted")

        export_id = uuid4()
        now = datetime.now(UTC)
        expires_at = now + timedelta(hours=retention_hours)
        download_token = secrets.token_urlsafe(32)
        inventory = _inventory(conn, workspace_key)

        conv_rows = conn.execute(
            queries.get("list_conversations"),
            {"workspace_key": workspace_key},
        ).fetchall()
        conv_store = ConversationStore(conn, workspace_key)
        conversations_data = []
        for c_row in conv_rows:
            conversations_data.append(conv_store.turns(c_row["conversation_id"]))

        art_rows = conn.execute(
            queries.get("list_artifacts"),
            {"workspace_key": workspace_key},
        ).fetchall()
        artifacts_data = [_plain_row(row) for row in art_rows]
        record = conn.execute(
            queries.get("get_academic_record_by_workspace"),
            {"workspace_key": workspace_key},
        ).fetchone()
        attempts = []
        if record is not None:
            attempts = conn.execute(
                queries.get("get_academic_record_attempts"),
                {"record_id": record["record_id"]},
            ).fetchall()
        feedback_rows = conn.execute(
            queries.get("list_workspace_feedback_for_export"),
            {"workspace_key": workspace_key},
        ).fetchall()
        audit_rows = conn.execute(
            queries.get("list_workspace_audits_for_export"),
            {"workspace_key": workspace_key},
        ).fetchall()

        manifest = {
            "version": "1.0",
            "workspaceKey": workspace_key,
            "exportedAt": now.isoformat(),
            "inventory": inventory,
            "data": {
                "conversations": conversations_data,
                "artifacts": artifacts_data,
                "academicRecord": None if record is None else _plain_row(record),
                "attempts": [_plain_row(row) for row in attempts],
                "feedback": [_plain_row(row) for row in feedback_rows],
                "audits": [_plain_row(row) for row in audit_rows],
            },
        }
        manifest["contentDigest"] = privacy_export_digest(
            {key: value for key, value in manifest.items() if key != "contentDigest"}
        )

        require_live_workspace(conn, workspace_key, lock=True)
        row = conn.execute(
            queries.get("insert_privacy_export"),
            {
                "export_id": export_id,
                "workspace_key": workspace_key,
                "user_id": user_id,
                "status": "completed",
                "manifest": Jsonb(manifest, dumps=_json_dumps),
                "download_token": download_token,
                "expires_at": expires_at,
            },
        ).fetchone()
        conn.commit()
        return PrivacyService._export_view(row, include_token=True)

    @staticmethod
    def get_export(conn, export_id: UUID, workspace_key: str) -> dict[str, Any]:
        row = PrivacyService._owned_export(conn, export_id, workspace_key)
        conn.commit()
        return PrivacyService._export_view(row, include_token=False)

    @staticmethod
    def list_exports(conn, workspace_key: str, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = conn.execute(
            queries.get("list_privacy_exports"),
            {"workspace_key": workspace_key, "limit": limit},
        ).fetchall()
        conn.commit()
        return [PrivacyService._export_view(row, include_token=False) for row in rows]

    @staticmethod
    def prune_expired_exports(conn, *, now: datetime | None = None) -> int:
        """Remove expired export rows and their full private-data copies."""
        cutoff = now or datetime.now(UTC)
        cursor = conn.execute(
            queries.get("delete_expired_privacy_exports"), {"cutoff": cutoff}
        )
        conn.commit()
        return max(0, cursor.rowcount or 0)

    @staticmethod
    def download_export(
        conn,
        export_id: UUID,
        workspace_key: str,
        token: str | None,
    ) -> dict[str, Any]:
        """Owner-scoped download. A workspace key alone is not the credential."""
        row = PrivacyService._owned_export(conn, export_id, workspace_key)
        expires_at = row["expires_at"]
        if expires_at is None or expires_at <= datetime.now(UTC):
            conn.commit()
            raise HTTPException(410, "export download expired")
        if not _token_matches(row["download_token"], token):
            conn.commit()
            raise HTTPException(403, "export download token is not valid")
        conn.commit()
        manifest = row["manifest"]
        viewed = PrivacyService._export_view(row, include_token=False)
        viewed["contentDigest"] = privacy_export_digest(manifest)
        viewed["manifest"] = manifest
        return viewed

    @staticmethod
    def delete_workspace(
        conn,
        workspace_key: str,
        user_id: UUID | None = None,
        reason: str = "user_request",
        *,
        data_dir: Path | None = None,
    ) -> dict[str, Any]:
        """Fence the workspace, remove local originals, then delete owned rows.

        The fence commits before the row delete. A later failure stays resumable
        and does not write the removed files back.
        """
        deletion_id = uuid4()
        now = datetime.now(UTC)
        with conn.transaction():
            conn.execute(queries.get("lock_workspace_privacy"), {"workspace_key": workspace_key})
            counts = _inventory(conn, workspace_key)
            paths = _original_paths(conn, workspace_key)
            conn.execute(
                queries.get("insert_privacy_deletion"),
                {
                    "deletion_id": deletion_id,
                    "workspace_key": workspace_key,
                    "user_id": user_id,
                    "status": "running",
                    "fenced_at": now,
                    "stage": "fenced",
                },
            )
            conn.execute(
                queries.get("admin_revoke_workspace_sessions"),
                {"workspace_key": workspace_key},
            )
            conn.execute(
                queries.get("insert_deletion_tombstone"),
                {
                    "tombstone_id": uuid4(),
                    "workspace_key": workspace_key,
                    "resource_type": "workspace",
                    "resource_id": workspace_key,
                    "reason": reason,
                },
            )
        conn.commit()
        payload = {**counts, "purgedOriginals": paths}
        try:
            # Publishers may hold a job-row lock before checking the privacy
            # fence. Release the workspace lock before cancelling those rows.
            conn.execute(queries.get("cancel_workspace_jobs"), {"workspace_key": workspace_key})
            conn.commit()
            _purge_originals(paths, data_dir)
            PrivacyService._write_deletion(
                conn,
                deletion_id,
                status="running",
                stage="local_purged",
                payload=payload,
                finished_at=None,
                error=None,
            )
            conn.commit()
            PrivacyService._delete_owned_rows(conn, workspace_key)
            finished = PrivacyService._finish_deletion(
                conn, deletion_id, workspace_key, user_id, reason, payload
            )
        except Exception as exc:
            conn.rollback()
            PrivacyService._write_deletion(
                conn,
                deletion_id,
                status="failed",
                stage="failed",
                payload=payload,
                finished_at=datetime.now(UTC),
                error=str(exc)[:500],
            )
            conn.commit()
            raise
        return finished

    @staticmethod
    def resume_deletion(
        conn,
        workspace_key: str,
        deletion_id: UUID,
        *,
        data_dir: Path | None = None,
    ) -> dict[str, Any]:
        """Continue a fenced deletion. Completed work is not rewritten."""
        row = conn.execute(
            queries.get("get_privacy_deletion"),
            {"deletion_id": deletion_id},
        ).fetchone()
        if row is None or row["workspace_key"] != workspace_key:
            raise HTTPException(404, "privacy deletion not found")
        if row["status"] == "completed":
            conn.commit()
            return PrivacyService._deletion_view(row)
        tombstone = conn.execute(
            queries.get("check_deletion_tombstone"),
            {
                "workspace_key": workspace_key,
                "resource_type": "workspace",
                "resource_id": workspace_key,
            },
        ).fetchone()
        if tombstone is None:
            raise HTTPException(409, "deletion fence is missing")
        stored = row["deleted_counts"] or {}
        paths = list(stored.get("purgedOriginals") or [])
        paths.extend(path for path in _original_paths(conn, workspace_key) if path not in paths)
        payload = dict(stored)
        payload["purgedOriginals"] = paths
        _purge_originals(paths, data_dir)
        PrivacyService._delete_owned_rows(conn, workspace_key)
        return PrivacyService._finish_deletion(
            conn,
            deletion_id,
            workspace_key,
            row["user_id"],
            tombstone["reason"],
            payload,
        )

    @staticmethod
    def _delete_owned_rows(conn, workspace_key: str) -> None:
        params = {"workspace_key": workspace_key}
        with conn.transaction():
            for name in _DELETE_STEPS:
                conn.execute(queries.get(name), params)

    @staticmethod
    def _finish_deletion(conn, deletion_id, workspace_key, user_id, reason, payload) -> dict[str, Any]:
        finished_at = datetime.now(UTC)
        updated = PrivacyService._write_deletion(
            conn,
            deletion_id,
            status="completed",
            stage="completed",
            payload=payload,
            finished_at=finished_at,
            error=None,
        )
        counts = {key: value for key, value in payload.items() if key != "purgedOriginals"}
        conn.execute(
            queries.get("admin_insert_audit"),
            {
                "audit_id": uuid4(),
                "actor_user_id": user_id,
                "actor_workspace": workspace_key,
                "action": "privacy.workspace_deleted",
                "target_type": "workspace",
                "target_id": workspace_key,
                "detail": Jsonb({"reason": reason, "deleted_counts": counts}),
            },
        )
        conn.commit()
        return PrivacyService._deletion_view(updated, counts)

    @staticmethod
    def _write_deletion(conn, deletion_id, *, status, stage, payload, finished_at, error):
        return conn.execute(
            queries.get("update_privacy_deletion"),
            {
                "deletion_id": deletion_id,
                "status": status,
                "stage": stage,
                "deleted_counts": Jsonb(payload, dumps=_json_dumps),
                "finished_at": finished_at,
                "error": error,
            },
        ).fetchone()

    @staticmethod
    def _owned_export(conn, export_id: UUID, workspace_key: str):
        row = conn.execute(
            queries.get("get_privacy_export"),
            {"export_id": export_id},
        ).fetchone()
        if row is None or row["workspace_key"] != workspace_key:
            raise HTTPException(404, "privacy export not found")
        return row

    @staticmethod
    def _export_view(row, *, include_token: bool) -> dict[str, Any]:
        viewed = dict(row)
        viewed["exportId"] = str(viewed["export_id"])
        viewed["workspaceKey"] = viewed["workspace_key"]
        viewed["expiresAt"] = _iso(viewed["expires_at"])
        viewed["createdAt"] = _iso(viewed["created_at"])
        if include_token:
            viewed["downloadToken"] = viewed["download_token"]
        else:
            viewed.pop("download_token", None)
        return viewed

    @staticmethod
    def _deletion_view(row, counts: dict[str, Any] | None = None) -> dict[str, Any]:
        viewed = dict(row)
        stored = viewed.get("deleted_counts") or {}
        if counts is None:
            counts = {key: value for key, value in stored.items() if key != "purgedOriginals"}
        viewed["deletionId"] = str(viewed["deletion_id"])
        viewed["workspaceKey"] = viewed["workspace_key"]
        viewed["deletedCounts"] = counts
        viewed["createdAt"] = _iso(viewed["created_at"])
        viewed["finishedAt"] = _iso(viewed.get("finished_at"))
        viewed["message"] = (
            "All workspace-owned conversations, artifacts, and private data have been deleted. "
            "Tombstones have been recorded to prevent replay or restore."
        )
        return viewed
