"""Staged catalog refresh. Cancel leaves the published edition and topic build in place."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app.auth.capabilities import require_operation
from app.auth.models import Principal
from app.db import queries


def _edition(row) -> dict[str, Any]:
    return {
        "edition_id": row["edition_id"],
        "revision": row["revision"],
        "edition_key": row["edition_key"],
        "is_current": row["is_current"],
        "topic_build_id": row["topic_build_id"],
        "source_ids": list(row["source_ids"] or []),
    }


def _detail(conn, batch_id: UUID) -> dict[str, Any]:
    batch = conn.execute(
        queries.get("get_catalog_refresh_batch"),
        {"batch_id": batch_id},
    ).fetchone()
    if not batch:
        raise HTTPException(404, f"catalog refresh {batch_id} not found")
    current = conn.execute(
        queries.get("get_current_catalog_edition"),
        {"collection_id": batch["collection_id"]},
    ).fetchone()
    staged = conn.execute(
        queries.get("get_catalog_edition"),
        {"edition_id": batch["staged_edition_id"]},
    ).fetchone()
    return {
        "batch_id": batch["batch_id"],
        "collection_id": batch["collection_id"],
        "edition_key": batch["edition_key"],
        "status": batch["status"],
        "base_revision": batch["base_revision"],
        "staged_revision": batch["staged_revision"],
        "compatible": batch["compatible"],
        "current_edition": _edition(current),
        "staged_edition": _edition(staged),
    }


class CatalogRefreshService:
    @staticmethod
    def _reviewer(actor: Principal) -> None:
        require_operation(actor, "sources.review")

    @classmethod
    def start(cls, conn, collection_id: UUID, edition_key: str, compatible: bool, actor: Principal):
        cls._reviewer(actor)
        current = conn.execute(
            queries.get("lock_current_catalog_edition"),
            {"collection_id": collection_id},
        ).fetchone()
        if not current:
            raise HTTPException(409, "no published catalog edition")
        staged_id = uuid4()
        conn.execute(
            queries.get("insert_catalog_edition"),
            {
                "edition_id": staged_id,
                "collection_id": collection_id,
                "revision": current["revision"] + 1,
                "edition_key": edition_key,
                "source_ids": list(current["source_ids"] or []),
                "topic_build_id": current["topic_build_id"],
                "is_current": False,
            },
        )
        batch_id = uuid4()
        conn.execute(
            queries.get("insert_catalog_refresh_batch"),
            {
                "batch_id": batch_id,
                "collection_id": collection_id,
                "edition_key": edition_key,
                "base_revision": current["revision"],
                "staged_revision": current["revision"] + 1,
                "staged_manifest": Jsonb({"compatible": compatible, "editionKey": edition_key}),
                "compatible": compatible,
                "staged_edition_id": staged_id,
            },
        )
        return _detail(conn, batch_id)

    @classmethod
    def cancel(cls, conn, batch_id: UUID, actor: Principal):
        cls._reviewer(actor)
        batch = conn.execute(
            queries.get("get_catalog_refresh_batch"),
            {"batch_id": batch_id},
        ).fetchone()
        if not batch:
            raise HTTPException(404, f"catalog refresh {batch_id} not found")
        conn.execute(
            queries.get("lock_current_catalog_edition"),
            {"collection_id": batch["collection_id"]},
        )
        cancelled = conn.execute(
            queries.get("cancel_catalog_refresh_batch"),
            {"batch_id": batch_id},
        ).fetchone()
        if not cancelled:
            raise HTTPException(409, "catalog refresh is not staging")
        return _detail(conn, batch_id)

    @classmethod
    def publish(cls, conn, batch_id: UUID, expected_revision: int, actor: Principal):
        cls._reviewer(actor)
        batch = conn.execute(
            queries.get("get_catalog_refresh_batch"),
            {"batch_id": batch_id},
        ).fetchone()
        if not batch:
            raise HTTPException(404, f"catalog refresh {batch_id} not found")
        if not batch["compatible"]:
            raise HTTPException(409, "incompatible topic graph is not rebuilt by this batch")
        current = conn.execute(
            queries.get("lock_current_catalog_edition"),
            {"collection_id": batch["collection_id"]},
        ).fetchone()
        if not current or current["revision"] != expected_revision or batch["base_revision"] != expected_revision:
            raise HTTPException(409, "published catalog revision changed")
        published = conn.execute(
            queries.get("publish_catalog_refresh_batch"),
            {"batch_id": batch_id},
        ).fetchone()
        if not published:
            raise HTTPException(409, "catalog refresh is not staging")
        conn.execute(
            queries.get("mark_catalog_edition_current"),
            {"collection_id": batch["collection_id"], "edition_id": batch["staged_edition_id"]},
        )
        return _detail(conn, batch_id)
