"""Collection lookup shared by the API, background jobs, and workers."""

from __future__ import annotations

from uuid import UUID

from app.config import Settings
from app.db import queries


class UnknownCollection(ValueError):
    """A named collection does not exist."""


def resolve_collection(conn, settings: Settings, name: str | None) -> tuple[UUID, str]:
    """Map a collection name (or the configured default) to its id.

    A named collection that does not exist is an error, never an empty result.
    """
    resolved = name or settings.default_collection
    row = conn.execute(queries.get("get_collection_by_name"), {"name": resolved}).fetchone()
    if row is None:
        raise UnknownCollection(f"unknown collection {resolved!r}")
    return row["collection_id"], resolved
