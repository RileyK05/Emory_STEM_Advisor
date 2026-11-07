"""Shared artifact-run admission for HTTP requests and chat publication."""

from __future__ import annotations

from typing import Any

from app.api.admission import AdmissionLimits
from app.jobs.store import JobStore


def admit_artifact_run(
    store: JobStore,
    *,
    kind: str,
    inputs: dict[str, Any],
    scope_key: str,
    idempotency_key: str,
    settings: Any,
    conn=None,
):
    """Create one artifact run with the queue admission used by POST /api/runs.

    Passing ``conn`` inserts inside the caller's transaction, so a conversation
    turn and its dependent run commit together.
    """
    admission = AdmissionLimits(
        getattr(settings, "admission_max_pending_workspace", 0),
        getattr(settings, "admission_max_pending_global", 0),
    )
    options: dict[str, Any] = {"settings": settings}
    if admission.enabled:
        options["admission"] = admission
    payload = {"kind": kind, "inputs": inputs}
    if conn is not None:
        return store.create_uncommitted(
            conn, kind="artifact.run", scope_key=scope_key,
            idempotency_key=idempotency_key, payload=payload, **options,
        )
    return store.create(
        kind="artifact.run", scope_key=scope_key,
        idempotency_key=idempotency_key, payload=payload, **options,
    )
