"""Workspace-scoped gap observations and human resolution records."""

from __future__ import annotations

import re
import unicodedata
from uuid import UUID, uuid4

from app.db import queries
from app.util.digest import json_digest
from app.util.workspace import is_valid_workspace_key

_FAILURE_KINDS = frozenset({"retrieval_refusal", "generation_withheld"})
_EVENT_KINDS = _FAILURE_KINDS | {"retrieval_success"}
_WHITESPACE = re.compile(r"\s+")


class GapError(Exception):
    """Base class for safe, expected gap-store errors."""


class GapNotFound(GapError):
    pass


class GapConflict(GapError):
    pass


class GapInvalidObservation(GapError):
    pass


def normalize_gap_query(query: str) -> str:
    """Fold Unicode/case/whitespace while preserving punctuation and numbers."""
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", query).casefold()).strip()


def _canonical_sources(source_ids: list[UUID] | None) -> list[UUID] | None:
    if source_ids is None:
        return None
    return sorted(set(source_ids), key=str)


def _identity_key(workspace_key, collection_id, normalized_query, source_ids):
    value = {
        "workspace": workspace_key,
        "collection": str(collection_id),
        "query": normalized_query,
        "sourceIds": None if source_ids is None else [str(item) for item in source_ids],
    }
    return json_digest(value)


def public_gap(row) -> dict:
    resolution = None
    if row["state"] == "resolved":
        resolution = {
            "sourceIds": row["resolution_source_ids"],
            "note": row["resolution_note"] or "",
            "resolvedAt": row["resolved_at"],
        }
    return {
        "gapId": row["gap_id"],
        "collectionId": row["collection_id"],
        "query": row["query"],
        "sourceIds": row["scope_source_ids"],
        "state": row["state"],
        "revision": row["revision"],
        "occurrenceCount": row["occurrence_count"],
        "recurrenceCount": row["recurrence_count"],
        "successfulRecurrenceCount": row["successful_recurrence_count"],
        "firstSeenAt": row["first_seen_at"],
        "lastSeenAt": row["last_seen_at"],
        "resolution": resolution,
    }


class GapStore:
    def __init__(self, conn, workspace_key: str):
        if not is_valid_workspace_key(workspace_key):
            raise ValueError("invalid workspace key")
        self.conn = conn
        self.workspace_key = workspace_key

    def observe(
        self,
        *,
        collection_id: UUID,
        query: str,
        source_ids: list[UUID] | None,
        trace_id: UUID,
        event_kind: str,
    ) -> dict | None:
        """Record a typed retrieval observation; successes never create work items."""
        if event_kind not in _EVENT_KINDS:
            raise ValueError("unsupported gap observation kind")
        normalized = normalize_gap_query(query)
        if not normalized or len(normalized) > 20000:
            raise ValueError("query must contain 1-20000 normalized characters")
        canonical_sources = _canonical_sources(source_ids)
        query_key = _identity_key(
            self.workspace_key, collection_id, normalized, canonical_sources
        )
        params = {
            "workspace_key": self.workspace_key,
            "collection_id": collection_id,
            "query": query.strip(),
            "normalized_query": normalized,
            "query_key": query_key,
            "scope_source_ids": canonical_sources,
            "trace_id": trace_id,
            "event_kind": event_kind,
        }

        self.conn.commit()
        with self.conn.transaction():
            self._authorize(collection_id, trace_id, canonical_sources)
            if event_kind in _FAILURE_KINDS:
                self.conn.execute(queries.get("ensure_gap_identity"), {
                    **params, "gap_id": uuid4(),
                })
            gap = self.conn.execute(
                queries.get("get_gap_identity_for_update"),
                {"workspace_key": self.workspace_key, "collection_id": collection_id,
                 "query_key": query_key},
            ).fetchone()
            if gap is None:
                # A successful retrieval is only useful as a recurrence metric
                # for a previously captured gap.
                return None
            if gap["scope_source_ids"] != canonical_sources:
                raise GapConflict
            updated = self._count_observation(gap, trace_id, event_kind)
            if updated is None:
                return public_gap(gap)  # This trace was already counted for the gap.
        self.conn.commit()
        return public_gap(updated)

    def _authorize(self, collection_id: UUID, trace_id: UUID, canonical_sources) -> None:
        """The trace must belong to the collection, and every scoped source to this workspace."""
        trace = self.conn.execute(
            queries.get("get_gap_trace_for_collection"), {"trace_id": trace_id}
        ).fetchone()
        if trace is None or trace["collection_id"] != collection_id:
            raise GapInvalidObservation
        if canonical_sources:
            authorized_rows = self.conn.execute(
                queries.get("list_gap_scope_sources"),
                {"collection_id": collection_id, "source_ids": canonical_sources,
                 "workspace_key": self.workspace_key},
            ).fetchall()
            if {row["source_id"] for row in authorized_rows} != set(canonical_sources):
                raise GapInvalidObservation

    def _count_observation(self, gap, trace_id: UUID, event_kind: str):
        """Count one trace against a gap: the updated gap, or None if this exact observation exists."""
        other_observations = self.conn.execute(
            queries.get("get_gap_observations_for_trace"),
            {"workspace_key": self.workspace_key, "trace_id": trace_id},
        ).fetchall()
        if any(row["gap_id"] != gap["gap_id"] for row in other_observations):
            raise GapConflict
        key = {"workspace_key": self.workspace_key, "trace_id": trace_id, "event_kind": event_kind}
        inserted = self.conn.execute(
            queries.get("insert_gap_observation"), {**key, "gap_id": gap["gap_id"]},
        ).fetchone()
        if inserted is None:
            existing = self.conn.execute(queries.get("get_gap_observation_identity"), key).fetchone()
            if existing is None or existing["gap_id"] != gap["gap_id"]:
                raise GapConflict
            return None
        updated = self.conn.execute(
            queries.get("increment_gap_observation"),
            {"workspace_key": self.workspace_key, "gap_id": gap["gap_id"], "event_kind": event_kind},
        ).fetchone()
        if updated is None:
            raise GapNotFound
        return updated

    def list(self, *, collection_id: UUID | None = None) -> dict:
        rows = self.conn.execute(queries.get("list_gaps_for_workspace"), {
            "workspace_key": self.workspace_key,
            "collection_id": collection_id,
        }).fetchall()
        metrics = self.conn.execute(queries.get("gap_metrics_for_workspace"), {
            "workspace_key": self.workspace_key,
            "collection_id": collection_id,
        }).fetchone()
        self.conn.commit()
        total = metrics["repeated_gap_observations"]
        successful = metrics["successful_repeated_observations"]
        return {
            "gaps": [public_gap(row) for row in rows],
            "metrics": {
                "repeatedGapObservations": total,
                "successfulRepeatedObservations": successful,
                "recurrenceRate": successful / total if total else None,
            },
        }

    def resolve(
        self,
        *,
        gap_id: UUID,
        expected_revision: int,
        source_ids: list[UUID],
        note: str,
        resolved_by: str,
    ) -> dict:
        canonical_sources = _canonical_sources(source_ids)
        if not canonical_sources:
            raise GapConflict
        clean_note = note.strip()
        if len(clean_note) > 1000:
            raise ValueError("resolution note too long")
        if not resolved_by or len(resolved_by) > 200:
            raise ValueError("invalid resolver identity")

        self.conn.commit()
        with self.conn.transaction():
            gap = self.conn.execute(queries.get("get_gap_for_workspace_update"), {
                "gap_id": gap_id, "workspace_key": self.workspace_key,
            }).fetchone()
            if gap is None:
                raise GapNotFound
            if gap["state"] != "open" or gap["revision"] != expected_revision:
                raise GapConflict

            allowed_scope = gap["scope_source_ids"]
            if allowed_scope is not None and not set(canonical_sources) <= set(allowed_scope):
                raise GapNotFound
            proofs = self.conn.execute(queries.get("prove_gap_resolution_sources"), {
                "source_ids": canonical_sources,
                "collection_id": gap["collection_id"],
                "workspace_key": self.workspace_key,
            }).fetchall()
            proofs_by_id = {row["source_id"]: row for row in proofs}
            if set(proofs_by_id) != set(canonical_sources) or any(
                row["chunk_count"] < 1 or row["locator_count"] < 1
                for row in proofs_by_id.values()
            ):
                raise GapNotFound

            next_revision = expected_revision + 1
            self.conn.execute(queries.get("insert_gap_resolution_history"), {
                "gap_id": gap_id,
                "revision": next_revision,
                "source_ids": canonical_sources,
                "note": clean_note,
                "resolved_by": resolved_by,
            })
            resolved = self.conn.execute(queries.get("resolve_gap"), {
                "gap_id": gap_id,
                "workspace_key": self.workspace_key,
                "expected_revision": expected_revision,
                "resolution_source_ids": canonical_sources,
                "resolution_note": clean_note,
                "resolved_by": resolved_by,
            }).fetchone()
            if resolved is None:
                raise GapConflict
        self.conn.commit()
        return public_gap(resolved)
