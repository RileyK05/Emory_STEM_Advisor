"""Immutable turns, exact input snapshots, and fail-closed request replay."""

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app.db import queries
from app.privacy.fence import require_live_workspace
from app.quotas.service import QuotaService
from app.util.digest import json_digest


def _json_size(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


MAX_CONVERSATIONS_PER_WORKSPACE = 500


# A busy model gets two more tries before the turn fails.
TURN_MAX_ATTEMPTS = 3

class ConversationStore:
    def __init__(self, conn, workspace_key: str,
                 max_conversations: int = MAX_CONVERSATIONS_PER_WORKSPACE):
        self.conn = conn
        self.workspace_key = workspace_key
        self.max_conversations = max_conversations

    def _params(self, conversation_id: UUID) -> dict:
        return {"conversation_id": conversation_id, "workspace_key": self.workspace_key}

    def get(self, conversation_id: UUID, *, lock=False):
        if lock:
            require_live_workspace(self.conn, self.workspace_key, lock=True)
        row = self.conn.execute(queries.get("lock_conversation" if lock else "get_conversation"),
                                self._params(conversation_id)).fetchone()
        if row is None:
            raise HTTPException(404, "conversation not found")
        return row

    def create(self, title: str):
        with self.conn.transaction():
            require_live_workspace(self.conn, self.workspace_key, lock=True)
            count = self.conn.execute(
                queries.get("count_conversations_for_workspace"),
                {"workspace_key": self.workspace_key},
            ).fetchone()
            if count is not None and count["count"] >= self.max_conversations:
                raise HTTPException(429, "conversation limit reached")
            row = self.conn.execute(queries.get("create_conversation"), {
                "conversation_id": uuid4(), "workspace_key": self.workspace_key, "title": title,
            }).fetchone()
        self.conn.commit()
        return public_conversation(row)

    def list(self, *, archived: bool = False, limit: int = 500, offset: int = 0):
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if not 0 <= offset <= 10_000:
            raise ValueError("offset must be between 0 and 10000")
        query = "list_archived_conversations" if archived else "list_conversations"
        rows = self.conn.execute(queries.get(query), {
            "workspace_key": self.workspace_key, "limit": limit, "offset": offset,
        }).fetchall()
        self.conn.commit()
        return [public_conversation(row) for row in rows]

    def archive(self, conversation_id: UUID):
        """Reversibly remove a conversation from the active quota."""
        self.conn.commit()
        with self.conn.transaction():
            conversation = self.get(conversation_id, lock=True)
            if conversation["archived_at"] is not None:
                return public_conversation(conversation)
            if conversation["pending_request_id"] is not None:
                raise HTTPException(409, "Cannot archive a conversation while a turn is pending.")
            row = self.conn.execute(queries.get("archive_conversation"),
                                    self._params(conversation_id)).fetchone()
            if row is None:
                raise HTTPException(409, "conversation archive state changed; reload")
        return public_conversation(row)

    def restore(self, conversation_id: UUID):
        """Restore an archived conversation only when active capacity exists."""
        self.conn.commit()
        with self.conn.transaction():
            require_live_workspace(self.conn, self.workspace_key, lock=True)
            conversation = self.get(conversation_id, lock=True)
            if conversation["archived_at"] is None:
                return public_conversation(conversation)
            count = self.conn.execute(
                queries.get("count_conversations_for_workspace"),
                {"workspace_key": self.workspace_key},
            ).fetchone()
            if count is not None and count["count"] >= self.max_conversations:
                raise HTTPException(429, "conversation limit reached; archive another conversation first")
            row = self.conn.execute(queries.get("restore_conversation"),
                                    self._params(conversation_id)).fetchone()
            if row is None:
                raise HTTPException(409, "conversation archive state changed; reload")
        return public_conversation(row)

    def turns(self, conversation_id, *, after=0, limit=200):
        conversation = self.get(conversation_id)
        rows = self.conn.execute(queries.get("conversation_turns"), {
            "conversation_id": conversation_id, "after": after, "limit": limit}).fetchall()
        self.conn.commit()
        return {**public_conversation(conversation), "turns": [public_turn(row) for row in rows]}

    def context(self, conversation_id):
        """The recent turns research reads; older turns are paged by advisor_read_conversation."""
        self.get(conversation_id)
        rows = self.conn.execute(queries.get("conversation_recent_context"), {
            "conversation_id": conversation_id}).fetchall()
        self.conn.commit()
        return list(reversed(rows))

    def replay(self, conversation_id, request_id, snapshot):
        self.get(conversation_id)  # Authorize before reading a request or fingerprint.
        row = self.conn.execute(queries.get("get_conversation_request"), {
            "conversation_id": conversation_id, "request_id": request_id}).fetchone()
        self.conn.commit()
        if row is None:
            return None
        self._check_replay(row, snapshot)
        return row["result"]

    def last_advising_context(self, conversation_id):
        row = self.conn.execute(queries.get("last_advising_context"), {
            **self._params(conversation_id)}).fetchone()
        self.conn.commit()
        return (row["effective"] or {}) if row else {}

    @staticmethod
    def _check_same_inputs(row, snapshot):
        if row["fingerprint"] != fingerprint(snapshot):
            raise HTTPException(409, "request identity is already bound to different inputs")

    @classmethod
    def _check_replay(cls, row, snapshot):
        cls._check_same_inputs(row, snapshot)
        if row["state"] == "pending":
            raise HTTPException(409, "request outcome is pending or unknown; inspect or abandon it before sending another request")

    def _lock_request_slot(self, conversation_id, request_id, expected_sequence):
        """Lock the conversation and return this request's prior row, or None for a new request.

        A new request must start exactly at `expected_sequence` with no pending request;
        otherwise the conversation changed since the caller loaded it. Must run inside the
        caller's transaction.
        """
        row = self.get(conversation_id, lock=True)
        if row.get("archived_at") is not None:
            raise HTTPException(409, "conversation is archived; restore it before sending")
        prior = self.conn.execute(queries.get("get_conversation_request"), {
            "conversation_id": conversation_id, "request_id": request_id}).fetchone()
        if prior:
            return prior
        if row["last_sequence"] != expected_sequence or row["pending_request_id"] is not None:
            raise HTTPException(409, "conversation changed or has a pending request; refresh before sending")
        return None

    def _append_user_turn(self, conversation_id, request_id, expected_sequence, snapshot):
        self.conn.execute(queries.get("insert_conversation_turn"), {
            "conversation_id": conversation_id, "request_id": request_id,
            "sequence": expected_sequence + 1, "role": "user", "text": snapshot["query"],
            "status": "input", "evidence": Jsonb(snapshot)})
        self.conn.execute(queries.get("advance_conversation"), {
            "conversation_id": conversation_id, "sequence": expected_sequence + 1,
            "pending_request_id": request_id})

    def begin(self, conversation_id, request_id, expected_sequence, snapshot):
        self.conn.commit()
        with self.conn.transaction():
            prior = self._lock_request_slot(conversation_id, request_id, expected_sequence)
            if prior:
                self._check_replay(prior, snapshot)
                return prior["result"]
            self.conn.execute(queries.get("create_conversation_request"), {
                "conversation_id": conversation_id, "request_id": request_id,
                "fingerprint": fingerprint(snapshot), "input_snapshot": Jsonb(snapshot),
            })
            self._append_user_turn(conversation_id, request_id, expected_sequence, snapshot)
        return None

    def begin_stream(self, conversation_id, request_id, expected_sequence, snapshot,
                     admission=None, settings=None):
        """Atomically bind the immutable request, pending turn, one queued job, and its charge.

        The quota policy is read in the transaction. The settlement row and bucket
        increment commit only with that job. A stale sequence, admission refusal,
        or quota refusal rolls the job back and leaves the bucket unchanged.
        """
        self.conn.commit()
        with self.conn.transaction():
            prior = self._lock_request_slot(conversation_id, request_id, expected_sequence)
            if prior:
                self._check_same_inputs(prior, snapshot)
                return prior

            policy = QuotaService.resolve_policy(
                self.conn, workspace_key=self.workspace_key, settings=settings
            )

            if admission is not None:
                admission.check(self.conn, self.workspace_key)
            job_id = uuid4()
            now = datetime.now(UTC)
            idempotency_key = f"conversation:{conversation_id}:{request_id}"
            job = self.conn.execute(queries.get("insert_job"), {
                "job_id": job_id, "kind": "conversation.turn", "scope_key": self.workspace_key,
                "idempotency_key": idempotency_key,
                "payload": Jsonb({"conversationId": str(conversation_id), "requestId": str(request_id)}),
                # Retried only when the model was busy; see ConversationTurnExecutor.
                "max_attempts": TURN_MAX_ATTEMPTS, "available_at": now,
            }).fetchone()
            if job is None:
                raise HTTPException(409, "conversation request identity is already in use")
            self.conn.execute(queries.get("append_job_event"), {
                "job_id": job_id, "event_type": "queued",
                "payload": Jsonb({"status": "queued"}), "now": now,
            })
            self.conn.execute(queries.get("create_conversation_stream_request"), {
                "conversation_id": conversation_id, "request_id": request_id,
                "fingerprint": fingerprint(snapshot), "input_snapshot": Jsonb(snapshot),
                "job_id": job_id,
            })
            self._append_user_turn(conversation_id, request_id, expected_sequence, snapshot)
            QuotaService.settle_generation(
                self.conn, workspace_key=self.workspace_key, request_id=request_id,
                policy=policy, commit=False,
            )
            return {"job_id": job_id, "state": "pending", "result": None}

    def request_for_owner(self, conversation_id, request_id):
        self.get(conversation_id)
        row = self.conn.execute(queries.get("get_conversation_request"), {
            "conversation_id": conversation_id, "request_id": request_id,
        }).fetchone()
        self.conn.commit()
        if row is None:
            raise HTTPException(404, "conversation request not found")
        return row

    def finish(self, conversation_id, request_id, result, *, state="completed"):
        self.conn.commit()
        with self.conn.transaction():
            return self.finish_uncommitted(conversation_id, request_id, result, state=state)

    def finish_uncommitted(self, conversation_id, request_id, result, *, state="completed"):
        """Publish a final assistant turn inside the caller's existing transaction."""
        row = self.get(conversation_id, lock=True)
        if row["pending_request_id"] != request_id:
            raise HTTPException(409, "request is no longer current; completion was withheld")
        research = result.get("research")
        if research:
            from app.advising.research_repository import ScopeChanged, verify_manifest
            from app.advising.research_store import ResearchStore, RunClosed

            try:
                runs = ResearchStore(self.conn, self.workspace_key)
                runs._lock(UUID(research["runId"]))
                verify_manifest(self.conn, runs._manifest(UUID(research["runId"])))
            except (ScopeChanged, RunClosed) as exc:
                raise HTTPException(409, "research sources or run changed; completion was withheld") from exc
        sequence = row["last_sequence"] + 1
        text = result.get("answer", result.get("reason", ""))
        self.conn.execute(queries.get("insert_conversation_turn"), {
            "conversation_id": conversation_id, "request_id": request_id,
            "sequence": sequence, "role": "assistant", "text": text,
            "status": result["type"], "evidence": Jsonb({
                "type": result["type"], "citations": result.get("citations", []),
                "contextBlock": result.get("trace", {}).get("contextBlock", []),
                "traceId": result.get("trace", {}).get("traceId"),
                "turnReferences": result.get("turnReferences", []),
                **({"research": result["research"]} if result.get("research") else {}),
                **({"applicability": result["applicability"]} if result.get("applicability") else {}),
                **({"artifactRun": result["artifactRun"]} if result.get("artifactRun") else {}),
                **({"artifactNotice": result["artifactNotice"]} if result.get("artifactNotice") else {}),
            })})
        if research or result["type"] in {"failed", "abandoned"}:
            status = ("cancelled" if result["type"] == "abandoned" else
                      "failed" if result["type"] == "failed" else
                      research["outcome"] if research["outcome"] in {
                          "clarification", "insufficient_evidence"} else
                      "partial" if research["completeness"] == "partial" else "completed")
            self.conn.execute(queries.get("finish_conversation_research"), {
                **self._params(conversation_id), "request_id": request_id,
                "status": status, "reason": "conversation_" + result["type"]})
        result = {**result, "conversationId": str(conversation_id), "sequence": sequence}
        self.conn.execute(queries.get("finish_conversation_request"), {
            "conversation_id": conversation_id, "request_id": request_id,
            "state": state, "result": Jsonb(result)})
        self.conn.execute(queries.get("advance_conversation"), {
            "conversation_id": conversation_id, "sequence": sequence, "pending_request_id": None})
        return result

    def abandon(self, conversation_id, request_id):
        self.get(conversation_id)
        prior = self.conn.execute(queries.get("get_conversation_request"), {
            "conversation_id": conversation_id, "request_id": request_id}).fetchone()
        self.conn.commit()
        if prior and prior["state"] == "abandoned":
            return prior["result"]
        return self.finish(conversation_id, request_id, {
            "type": "abandoned", "reason": "Request abandoned. Any already-started provider call may still incur usage; its result cannot replace this turn.",
        }, state="abandoned")


def fingerprint(snapshot):
    return json_digest(snapshot.get("request", snapshot))


def public_conversation(row):
    return {
        "conversationId": str(row["conversation_id"]),
        "title": row["title"],
        "lastSequence": row["last_sequence"],
        "pendingRequestId": str(row["pending_request_id"]) if row["pending_request_id"] else None,
        "contextEpoch": row.get("context_epoch", 0),
        "contextBoundarySequence": row.get("context_boundary_sequence", 0),
        "contextResetAt": row["context_reset_at"].isoformat() if row.get("context_reset_at") else None,
        "archivedAt": row["archived_at"].isoformat() if row.get("archived_at") else None,
        "createdAt": row["created_at"].isoformat(),
        "updatedAt": row["updated_at"].isoformat(),
    }


_HISTORY_CITATION_KEYS = ("chunkId", "sourceName", "locatorLabel")


def history_turn(row):
    """One turn as the model sees it in history, and as the context budget counts it.

    History re-sends the answer text and compact citation labels (source name and
    locator), never the bodies of cited passages or a turn's source snapshot.
    Re-sending each answer's full evidence grew history by ~9-10 KB per turn and
    hit the 12 KB context wall after two or three turns (EMO-017); the passage
    bodies stay saved on the turn's own evidence for display and audit.
    """
    if row["role"] == "assistant":
        evidence = row["evidence"] or {}
        citations = [
            {key: item[key] for key in _HISTORY_CITATION_KEYS if item.get(key) is not None}
            for item in evidence.get("citations") or [] if isinstance(item, dict)
        ]
        turn_evidence = {"citations": citations}
        if evidence.get("type") is not None:
            turn_evidence["type"] = evidence["type"]
    else:
        turn_evidence = {}
    return {"sequence": row["sequence"], "role": row["role"], "text": row["text"],
            "evidence": turn_evidence}


def research_history_prompt(rows, *, max_bytes: int) -> str:
    """Keep exact student reports ahead of verbose replies; archive omissions."""
    total = rows[-1].get("total_turns", len(rows)) if rows else 0
    selected = []
    def render():
        return json.dumps({"kind": "selected_conversation_history", "total_turns": total,
            "omitted_turns": total - len(selected),
            "turns": sorted(selected, key=lambda turn: turn["sequence"]),
            "read_older_with": "advisor_read_conversation"}, ensure_ascii=False, separators=(",", ":"))
    # Course corrections and preferences come from the student, not the
    # assistant's retelling. One long reply must not evict earlier reports.
    # Keep whole messages in chronological order; never extract/merge their
    # meaning or turn them into verified academic facts.
    for role in ("user", "assistant"):
        for row in reversed(rows):
            if row["role"] != role:
                continue
            selected.append(history_turn(row))
            if len(render().encode("utf-8")) > max_bytes:
                selected.pop()
    return render()


def public_turn(row):
    evidence = row["evidence"] or {}
    return {"sequence": row["sequence"], "requestId": str(row["request_id"]),
            "role": row["role"], "text": row["text"], "status": row["status"],
            "evidence": evidence,
            **({"artifactRun": evidence["artifactRun"]} if evidence.get("artifactRun") else {}),
            **({"artifactNotice": evidence["artifactNotice"]} if evidence.get("artifactNotice") else {}),
            "createdAt": row["created_at"].isoformat()}


