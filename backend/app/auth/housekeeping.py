"""Delete expired login transactions and expired or long-revoked sessions.

Lookups already ignore these rows, so purging is storage hygiene only.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.db import queries

REVOKED_SESSION_RETENTION = timedelta(days=30)
PURGE_INTERVAL_SECONDS = 3600.0


def purge_expired_auth(conn, *, now: datetime | None = None,
                       revoked_retention: timedelta = REVOKED_SESSION_RETENTION) -> dict[str, int]:
    """Purge in one statement and commit; returns the deleted row counts."""
    current = now or datetime.now(UTC)
    row = conn.execute(queries.get("purge_auth_records"), {
        "now": current, "revoked_before": current - revoked_retention,
    }).fetchone()
    # Debug sessions are bearer secrets; expired/revoked rows are storage only.
    conn.execute(queries.get("debug_purge_expired"))
    conn.commit()
    return {"transactions": int(row["transactions"]), "sessions": int(row["sessions"])}
