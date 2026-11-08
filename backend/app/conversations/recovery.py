"""Reconcile conversation requests whose execution can no longer publish.

A streamed turn is terminal in two places: the job row and the conversation
request.  Worker failure hooks cover the common path, but a crash, a lost
lease, or a publication failure can still leave the request pending.  This
module settles only requests whose job is already terminal (or whose job was
never created and whose synchronous deadline has passed), so it cannot race a
live model call.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from app.conversations.store import ConversationStore
from app.db import queries

# Synchronous requests have no durable job to inspect.  The provider and
# operation budgets are far below this, so a request this old cannot still be
# making progress in a healthy process.
STALE_SYNC_REQUEST_SECONDS = 900


def reconcile_pending_requests(
    conn,
    *,
    now: datetime | None = None,
    older_than_seconds: int = STALE_SYNC_REQUEST_SECONDS,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Settle orphaned pending requests and return the settled identifiers.

    Must run in its own short transaction before a backup snapshot.  It never
    replays a provider call and never publishes an unknown result as an answer.
    """
    if not 1 <= older_than_seconds <= 86_400:
        raise ValueError("older_than_seconds must be between 1 and 86400")
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    current = now or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("reconciliation clock must be timezone-aware")
    conn.commit()
    settled: list[dict[str, Any]] = []
    with conn.transaction():
        # Candidates are read without a row lock. Each is claimed below by
        # locking the job and then the conversation, which is the order success
        # publication uses, so recovery cannot deadlock with it. A candidate
        # another path settled in between fails the conversation check.
        rows = conn.execute(
            queries.get("reconcile_pending_conversation_requests"),
            {
                "now": current,
                "older_than_seconds": older_than_seconds,
                "limit": limit,
            },
        ).fetchall()
        for row in rows:
            job_status = row["job_status"]
            if row["job_id"] is not None:
                # Lock the job row first. FOR UPDATE cannot target the nullable
                # side of the LEFT JOIN above.
                job = conn.execute(
                    queries.get("lock_job_for_recovery"),
                    {"job_id": row["job_id"]},
                ).fetchone()
                if job is None:
                    job_status = None
                else:
                    job_status = job["status"]
                    if job_status == "running" and (
                            job["lease_expires_at"] is None
                            or job["lease_expires_at"] > current):
                        # Lease was renewed or the job is live again; skip.
                        continue
                    if job_status not in {"succeeded", "failed", "cancelled", "running"}:
                        continue
            conversation = conn.execute(
                queries.get("lock_conversation"),
                {
                    "conversation_id": row["conversation_id"],
                    "workspace_key": row["workspace_key"],
                },
            ).fetchone()
            if conversation is None or conversation["pending_request_id"] != row["request_id"]:
                # Another path settled this request or started a newer turn.
                continue
            store = ConversationStore(conn, row["workspace_key"])
            try:
                store.finish_uncommitted(
                    row["conversation_id"],
                    row["request_id"],
                    {
                        "type": "failed",
                        "reason": (
                            "The advisor couldn't answer that. Try again."
                        ),
                    },
                    state="failed",
                )
            except HTTPException:
                # A concurrent abandon/completion won; leave that settlement alone.
                continue
            settled.append({
                "conversationId": str(row["conversation_id"]),
                "requestId": str(row["request_id"]),
                "jobId": str(row["job_id"]) if row["job_id"] is not None else None,
                "jobStatus": job_status,
            })
    return settled
