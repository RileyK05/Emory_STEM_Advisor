"""Authorized debug experiments. Saved-request replay does not create one."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app.auth.capabilities import require_operation
from app.auth.models import Principal
from app.conversations.store import ConversationStore, fingerprint
from app.db import queries


class ExperimentService:
    @classmethod
    def start(
        cls,
        conn,
        actor: Principal,
        conversation_id: UUID,
        request_id: UUID,
        snapshot: dict,
    ) -> dict[str, Any]:
        """Record an experiment id for an immutable snapshot. Do not append a turn."""
        require_operation(actor, "debug.manage")
        store = ConversationStore(conn, actor.workspace_key)
        store.get(conversation_id)
        row = conn.execute(queries.get("get_conversation_request"), {
            "conversation_id": conversation_id,
            "request_id": request_id,
        }).fetchone()
        if row is None:
            raise HTTPException(404, "conversation request not found")
        if row["state"] == "pending" or row["result"] is None:
            raise HTTPException(409, "unknown prior provider outcome is not replayed")
        if fingerprint(snapshot) != row["fingerprint"]:
            raise HTTPException(409, "experiment snapshot does not match the saved request")
        saved = row["input_snapshot"] or {}
        request = saved.get("request", saved)
        if not isinstance(request, dict) or not str(request.get("query") or "").strip():
            raise HTTPException(409, "historical inputs are missing; the snapshot is not replayable")
        source_ids = request.get("sourceIds")
        pinned = {
            "query": request.get("query"),
            "collectionId": request.get("collectionId"),
            "sourceIds": source_ids if isinstance(source_ids, list) else None,
            "fingerprint": row["fingerprint"],
        }
        experiment_id = uuid4()
        stored = conn.execute(queries.get("insert_debug_experiment"), {
            "experiment_id": experiment_id,
            "workspace_key": actor.workspace_key,
            "source_conversation_id": conversation_id,
            "source_request_id": request_id,
            "snapshot": Jsonb(pinned),
            "state": "recorded",
        }).fetchone()
        result = dict(stored)
        result["snapshot"] = pinned
        return result
