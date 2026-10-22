"""Deletion fences shared by private stores and job publication."""

from fastapi import HTTPException

from app.db import queries


def require_live_workspace(conn, workspace_key: str, *, lock: bool = False) -> None:
    """Reject a deleted workspace; serialize short writes with fence creation.

    A worker may check without a lock while computing. Publication and store
    mutations hold the transaction lock until their caller commits or rolls back.
    """
    if lock:
        conn.execute(queries.get("lock_workspace_privacy"), {"workspace_key": workspace_key})
    deleted = conn.execute(queries.get("check_deletion_tombstone"), {
        "workspace_key": workspace_key, "resource_type": "workspace", "resource_id": workspace_key,
    }).fetchone()
    if deleted is not None:
        raise HTTPException(410, "workspace has been deleted")
