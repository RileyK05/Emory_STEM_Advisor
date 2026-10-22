"""Leased, idempotent consumption of source invalidation events."""

from uuid import uuid4

from app.db import queries


def consume_source_invalidations(conn, worker_id: str, *, limit: int = 20, lease_seconds: int = 60) -> int:
    """Mark dependents stale and complete each claimed outbox row once.

    A second call does not claim a completed row. A row whose lease is still
    held by another worker is left alone. The caller commits.
    """
    if not worker_id or len(worker_id) > 200:
        raise ValueError("worker_id is required")
    lease_token = uuid4()
    claimed = conn.execute(queries.get("claim_source_invalidation_outbox"), {
        "lease_token": lease_token,
        "lease_seconds": lease_seconds,
        "limit": limit,
    }).fetchall()
    completed = 0
    for row in claimed:
        try:
            with conn.transaction():
                conn.execute(queries.get("mark_audits_stale_for_source"), {
                    "source_id": row["source_id"],
                })
                done = conn.execute(queries.get("complete_source_invalidation_outbox"), {
                    "outbox_id": row["outbox_id"],
                    "lease_token": lease_token,
                }).fetchone()
            if done is not None:
                completed += 1
        except Exception as exc:  # noqa: BLE001 - leave the row for a later lease
            conn.execute(queries.get("release_source_invalidation_lease"), {
                "outbox_id": row["outbox_id"],
                "lease_token": lease_token,
                "last_error": type(exc).__name__,
            })
    return completed
