"""Active support-grant checks shared by Admin delivery and worker publication."""

from uuid import UUID

from fastapi import HTTPException

from app.db import queries


def support_grant_id_from_payload(payload: dict | None) -> UUID | None:
    """Return a job's pinned support grant, if publication must recheck one."""
    if not isinstance(payload, dict):
        return None
    raw = payload.get("supportGrantId")
    inputs = payload.get("inputs")
    if raw is None and isinstance(inputs, dict):
        raw = inputs.get("supportGrantId")
    if not raw:
        return None
    try:
        return UUID(str(raw))
    except ValueError as exc:
        raise HTTPException(403, "support grant is missing, expired, or revoked") from exc


def assert_support_grant_publishable(conn, grant_id: UUID) -> None:
    """Refuse publication unless this grant is still active for its recipient."""
    row = conn.execute(
        queries.get("lock_support_grant_for_publication"),
        {"grant_id": grant_id},
    ).fetchone()
    if row is None:
        raise HTTPException(403, "support grant is missing, expired, or revoked")


def assert_job_support_grant(conn, job) -> None:
    grant_id = support_grant_id_from_payload(getattr(job, "payload", None))
    if grant_id is not None:
        assert_support_grant_publishable(conn, grant_id)
