"""Data helpers for reviewed course changes.

Kept free of atlas-parser imports so the catalog importer can queue a baseline
conflict without creating an import cycle.
"""

from uuid import uuid4

from psycopg.types.json import Jsonb

from app.db import queries


def record_baseline_conflict(
    conn,
    *,
    collection_id,
    code: str,
    course_id,
    base_authority: str | None,
    base_value: dict,
    incoming_value: dict,
    note: str,
) -> bool:
    """Queue one conflict when a new baseline disagrees with a live correction.

    Returns True when a new conflict row was written. The published correction
    stays active; the import must not overwrite it.
    """
    existing = conn.execute(
        queries.get("get_conflicted_change_for_course"),
        {"collection_id": collection_id, "code": code},
    ).fetchone()
    if existing is not None:
        return False
    conn.execute(
        queries.get("insert_course_change"),
        {
            "change_id": uuid4(),
            "collection_id": collection_id,
            "course_id": course_id,
            "code": code,
            "change_kind": "baseline_conflict",
            "status": "conflicted",
            "expected_revision": 0,
            "base_authority": base_authority,
            "base_value": Jsonb(base_value),
            "proposed_value": Jsonb(incoming_value),
            "reason": "A later baseline import disagrees with the published correction.",
            "actor_user_id": None,
            "conflict_note": note,
        },
    )
    return True
