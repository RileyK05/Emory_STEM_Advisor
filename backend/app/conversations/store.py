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


_SUMMARY_EXCERPT_LIMIT = 8
_SUMMARY_EXCERPT_CHARS = 400
_STALE_SUMMARY_SECONDS = 300
MAX_CONVERSATIONS_PER_WORKSPACE = 500


def _excerpt(value) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text[:_SUMMARY_EXCERPT_CHARS] if text else None


def _bounded_effective(effective) -> dict | None:
    """Scope pointers only; never copy the source snapshot or full artifact specs."""
    if not isinstance(effective, dict):
        return None
    source_ids = effective.get("sourceIds")
    references = []
    for artifact in (effective.get("artifactReferences") or [])[:20]:
        if not isinstance(artifact, dict):
            continue
        spec = artifact.get("spec") if isinstance(artifact.get("spec"), dict) else {}
        pointer = {
            "artifactId": artifact.get("artifactId"),
            "revisionId": artifact.get("revisionId"),
            "spec": {key: spec[key] for key in ("title", "name", "kind")
                     if spec.get(key) is not None},
        }
        references.append(pointer)
    return {
        "collectionId": effective.get("collectionId"),
        "sourceIds": list(source_ids)[:100] if isinstance(source_ids, list) else None,
        "artifactReferences": references,
    }


def _summary_turn(item: dict) -> dict:
    """One turn's compact checkpoint input and exact reference metadata.

    Checkpoints keep exact identifiers and a bounded excerpt, not the full
    retrieved passage bodies or the source snapshot. That keeps a summary input
    proportional to the conversation text instead of to every passage or source
    ever retrieved (EMO-017).
    """
    is_user = item["role"] == "user"
    evidence = item["evidence"] or {}
    turn = {
        "sequence": item["sequence"], "role": item["role"],
        "status": item["status"], "text": item["text"],
    }
    if is_user:
        turn["effective"] = _bounded_effective(evidence.get("effective"))
        return turn
    turn["citations"] = [
        {key: citation[key] for key in
         ("chunkId", "sourceId", "sourceName", "locatorLabel")
         if citation.get(key) is not None}
        for citation in (evidence.get("citations") or [])
        if isinstance(citation, dict)
    ]
    turn["contextBlock"] = []
    for body in (evidence.get("contextBlock") or [])[:_SUMMARY_EXCERPT_LIMIT]:
        if not isinstance(body, dict) or not body.get("chunkId"):
            continue
        entry = {key: body[key] for key in
                 ("chunkId", "sourceId", "sourceName", "locatorLabel")
                 if body.get(key) is not None}
        excerpt = _excerpt(body.get("text"))
        if excerpt:
            entry["text"] = excerpt
        turn["contextBlock"].append(entry)
    return turn


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
            self._expire_stale_pending_summary(conversation_id)
            pending = self.conn.execute(queries.get("pending_conversation_summary"), {
                "conversation_id": conversation_id, "workspace_key": self.workspace_key,
            }).fetchone()
            if pending is not None:
                raise HTTPException(409, "Cannot archive a conversation while its summary is pending.")
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

    def summaries(self, conversation_id):
        self.get(conversation_id)
        rows = self.conn.execute(queries.get("list_conversation_summaries"), {
            "conversation_id": conversation_id, "workspace_key": self.workspace_key,
        }).fetchall()
        self.conn.commit()
        return [public_summary(row) for row in rows]

    def context(self, conversation_id, *, max_bytes: int, archive: bool = False,
                enforce: bool = True):
        conv = self.get(conversation_id)
        boundary = conv.get("context_boundary_sequence", 0)
        if archive:
            rows = self.conn.execute(queries.get("conversation_recent_context"), {
                "conversation_id": conversation_id}).fetchall()
            self.conn.commit()
            return list(reversed(rows))
        rows = self.conn.execute(queries.get("conversation_turns"), {
            "conversation_id": conversation_id, "after": boundary, "limit": 1000}).fetchall()
        size = len(history_prompt(rows).encode("utf-8"))
        if enforce and (size > max_bytes or len(rows) >= 1000):
            self.conn.commit()
            raise HTTPException(422, "conversation exceeds the context budget; start a new conversation. Saved turns remain available.")
        self.conn.commit()
        return rows

    def latest_summary(self, conversation_id):
        row = self.conn.execute(queries.get("latest_conversation_summary"), {
            "conversation_id": conversation_id, "workspace_key": self.workspace_key,
        }).fetchone()
        self.conn.commit()
        return row

    def _expire_stale_pending_summary(self, conversation_id) -> None:
        """A crashed compaction must not fence the conversation forever.

        Runs inside the caller's transaction and is followed by a fresh pending
        read, so a live (recent) checkpoint still blocks concurrent writers.
        """
        self.conn.execute(queries.get("expire_stale_pending_summary"), {
            "conversation_id": conversation_id, "workspace_key": self.workspace_key,
            "seconds": _STALE_SUMMARY_SECONDS,
        })

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

        A new request must start exactly at `expected_sequence` with no pending request or
        summary; otherwise the conversation changed since the caller loaded it. Must run
        inside the caller's transaction.
        """
        row = self.get(conversation_id, lock=True)
        if row.get("archived_at") is not None:
            raise HTTPException(409, "conversation is archived; restore it before sending")
        self._expire_stale_pending_summary(conversation_id)
        pending_summary = self.conn.execute(queries.get("pending_conversation_summary"), {
            "conversation_id": conversation_id, "workspace_key": self.workspace_key,
        }).fetchone()
        prior = self.conn.execute(queries.get("get_conversation_request"), {
            "conversation_id": conversation_id, "request_id": request_id}).fetchone()
        if prior:
            return prior
        if (row["last_sequence"] != expected_sequence or row["pending_request_id"] is not None
                or pending_summary is not None):
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
                     admission=None):
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

            policy = QuotaService.resolve_policy(self.conn, workspace_key=self.workspace_key)

            if admission is not None:
                admission.check(self.conn, self.workspace_key)
            job_id = uuid4()
            now = datetime.now(UTC)
            idempotency_key = f"conversation:{conversation_id}:{request_id}"
            job = self.conn.execute(queries.get("insert_job"), {
                "job_id": job_id, "kind": "conversation.turn", "scope_key": self.workspace_key,
                "idempotency_key": idempotency_key,
                "payload": Jsonb({"conversationId": str(conversation_id), "requestId": str(request_id)}),
                "max_attempts": 1, "available_at": now,
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
                run = ResearchStore(self.conn, self.workspace_key)._lock(UUID(research["runId"]))
                verify_manifest(self.conn, run["scope_manifest"])
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

    def begin_summary(self, conversation_id, summary_id, expected_sequence, *, max_bytes,
                      keep_turns: int = 4):
        """Freeze an exact contiguous checkpoint input before any provider I/O."""
        if not 1 <= keep_turns <= 50:
            raise ValueError("summary keep_turns must be between 1 and 50")
        request_fingerprint = fingerprint({"request": {"expectedSequence": expected_sequence}})
        self.conn.commit()
        with self.conn.transaction():
            conversation = self.get(conversation_id, lock=True)
            replay = self._summary_replay(conversation_id, summary_id, request_fingerprint)
            if replay is not None:
                return {"replay": replay}
            self._expire_stale_pending_summary(conversation_id)
            pending_summary = self.conn.execute(queries.get("pending_conversation_summary"), {
                "conversation_id": conversation_id, "workspace_key": self.workspace_key,
            }).fetchone()
            if (conversation["last_sequence"] != expected_sequence
                    or conversation["pending_request_id"] is not None or pending_summary is not None):
                raise HTTPException(409, "conversation changed or has a pending request; refresh before summarizing")

            previous = self.conn.execute(queries.get("latest_conversation_summary"), {
                "conversation_id": conversation_id, "workspace_key": self.workspace_key,
            }).fetchone()
            boundary = conversation.get("context_boundary_sequence", 0)
            base_sequence = max(boundary, previous["covered_through"] if previous else 0)
            reference_metadata = (
                list(previous["reference_metadata"] or [])
                if previous and previous["covered_through"] > boundary
                else []
            )
            if (
                previous
                and previous["covered_through"] > boundary
                and [item.get("sequence") for item in reference_metadata]
                != list(range(boundary + 1, base_sequence + 1))
            ):
                raise HTTPException(409, "prior summary reference coverage is incomplete")
            candidate = self._checkpoint_boundary(
                conversation_id, base_sequence, expected_sequence, keep_turns=keep_turns)
            raw_turns = self._covered_turns(conversation_id, base_sequence, candidate)
            input_snapshot = {
                "previousSummary": (
                    {
                        "summaryId": str(previous["summary_id"]),
                        "coveredThrough": previous["covered_through"],
                        "text": previous["summary_text"],
                    }
                    if previous and previous["covered_through"] > boundary
                    else None
                ),
                "turns": raw_turns, "expectedSequence": expected_sequence,
                "coveredThrough": candidate,
            }
            reference_metadata.extend(raw_turns)
            if _json_size(input_snapshot) > max_bytes or _json_size(reference_metadata) > max_bytes:
                raise HTTPException(422, "conversation summary input or exact reference metadata exceeds its budget")
            inserted = self.conn.execute(queries.get("insert_conversation_summary"), {
                "summary_id": summary_id, "conversation_id": conversation_id,
                "workspace_key": self.workspace_key, "expected_sequence": expected_sequence,
                "candidate_covered_through": candidate,
                "request_fingerprint": request_fingerprint,
                "input_fingerprint": fingerprint({"request": input_snapshot}),
                "input_snapshot": Jsonb(input_snapshot),
                "reference_metadata": Jsonb(reference_metadata),
                "operation_id": summary_id,
                "context_epoch": conversation.get("context_epoch", 0),
            }).fetchone()
        return {"replay": None, "row": inserted, "input": input_snapshot,
                "references": reference_metadata}

    def _summary_replay(self, conversation_id, summary_id, request_fingerprint):
        """The stored summary for a repeated request, None for a new one; conflicts raise."""
        prior = self.conn.execute(queries.get("get_conversation_summary"), {
            "summary_id": summary_id, "conversation_id": conversation_id,
            "workspace_key": self.workspace_key,
        }).fetchone()
        if prior is not None:
            if prior["request_fingerprint"] != request_fingerprint:
                raise HTTPException(409, "summary identity is already bound to different inputs")
            if prior["state"] == "pending":
                raise HTTPException(409, "summary outcome is pending or unknown; inspect it before retrying")
            return public_summary(prior)
        identity = self.conn.execute(queries.get("get_conversation_summary_identity"), {
            "summary_id": summary_id,
        }).fetchone()
        if identity is not None:
            raise HTTPException(404, "conversation summary not found")
        return None

    def _turn_at(self, conversation_id, sequence):
        return self.conn.execute(queries.get("conversation_turns"), {
            "conversation_id": conversation_id, "after": sequence - 1, "limit": 1,
        }).fetchone()

    def _checkpoint_boundary(self, conversation_id, base_sequence: int, expected_sequence: int,
                             *, keep_turns: int = 4) -> int:
        """The last sequence a summary may cover.

        Keeps the configured number of raw recent turns, then backs up to an
        assistant turn so a summary only covers complete saved request pairs.
        """
        candidate = expected_sequence - keep_turns
        while candidate > base_sequence:
            turn = self._turn_at(conversation_id, candidate)
            if turn is not None and turn["sequence"] == candidate and turn["role"] == "assistant":
                return candidate
            candidate -= 1
        raise HTTPException(422, "there are no complete older turns to summarize")

    def _covered_turns(self, conversation_id, base_sequence: int, candidate: int):
        rows = self.conn.execute(queries.get("conversation_turns"), {
            "conversation_id": conversation_id, "after": base_sequence, "limit": 1000,
        }).fetchall()
        if len(rows) >= 1000:
            raise HTTPException(422, "conversation history exceeds the bounded summary input; no turns were truncated")
        covered = [item for item in rows if item["sequence"] <= candidate]
        if ([item["sequence"] for item in covered] != list(range(base_sequence + 1, candidate + 1))
                or not covered or covered[-1]["role"] != "assistant"):
            raise HTTPException(409, "conversation turn history changed while preparing the checkpoint")
        return [_summary_turn(item) for item in covered]

    def finish_summary(self, conversation_id, summary_id, *, state, summary_text=None,
                       failure_code=None, profile_id=None, profile_revision=None):
        if state not in {"completed", "refused", "failed", "stale"}:
            raise ValueError("invalid conversation summary state")
        if state == "completed" and (
            not isinstance(summary_text, str) or not summary_text.strip()
            or len(summary_text) > 20000 or len(summary_text.encode("utf-8")) > 80000
        ):
            raise HTTPException(422, "summary text is empty or exceeds its output budget")
        with self.conn.transaction():
            conversation = self.get(conversation_id, lock=True)
            summary = self.conn.execute(queries.get("lock_conversation_summary"), {
                "summary_id": summary_id, "conversation_id": conversation_id,
                "workspace_key": self.workspace_key,
            }).fetchone()
            if summary is None:
                raise HTTPException(404, "conversation summary not found")
            if summary["state"] != "pending":
                return public_summary(summary)
            if state == "completed" and (
                conversation["last_sequence"] != summary["expected_sequence"]
                or conversation["pending_request_id"] is not None
                or conversation.get("context_epoch", 0) != summary.get("context_epoch", 0)
                or conversation.get("context_boundary_sequence", 0) >= summary["candidate_covered_through"]
            ):
                state, summary_text, failure_code = "stale", None, "stale_conversation"
            row = self.conn.execute(queries.get("finish_conversation_summary"), {
                "summary_id": summary_id, "conversation_id": conversation_id,
                "workspace_key": self.workspace_key, "state": state,
                "summary_text": summary_text if state == "completed" else None,
                "failure_code": failure_code,
                "profile_id": profile_id, "profile_revision": profile_revision,
            }).fetchone()
            if row is None:
                raise HTTPException(409, "conversation summary is no longer pending")
        return public_summary(row)


    def reset_memory(self, conversation_id: UUID) -> dict:
        self.conn.commit()
        with self.conn.transaction():
            conv = self.get(conversation_id, lock=True)
            if conv["pending_request_id"] is not None:
                raise HTTPException(409, "Cannot reset memory while a turn request is pending.")
            self._expire_stale_pending_summary(conversation_id)
            pending_summary = self.conn.execute(queries.get("pending_conversation_summary"), {
                "conversation_id": conversation_id, "workspace_key": self.workspace_key,
            }).fetchone()
            if pending_summary is not None:
                raise HTTPException(409, "Cannot reset memory while a conversation summary is pending.")
            row = self.conn.execute(queries.get("reset_conversation_memory"), {
                "conversation_id": conversation_id, "workspace_key": self.workspace_key,
            }).fetchone()
            if row is None:
                raise HTTPException(404, "Conversation not found.")
        return public_conversation(row)

    def latest_research_notebook(self, conversation_id: UUID) -> dict | None:
        self.get(conversation_id)
        row = self.conn.execute(queries.get("latest_conversation_research_notebook"), {
            "conversation_id": conversation_id, "workspace_key": self.workspace_key,
        }).fetchone()
        self.conn.commit()
        if row is None or not row["state"]:
            return None
        return {
            "runId": str(row["run_id"]),
            "status": row["status"],
            "state": row["state"],
            "createdAt": row["created_at"].isoformat() if row.get("created_at") else None,
        }


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


def history_prompt(rows) -> str:
    # Every retained turn is included exactly once, stripped to text and
    # citation labels. Overflow still fails before a provider call rather than
    # silently dropping a turn. Cited passage bodies stay out of this JSON;
    # warm_citations reattaches them to the next numbered context instead.
    return json.dumps([history_turn(row) for row in rows], ensure_ascii=False,
                      separators=(",", ":"))


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


_WARM_CITATION_LIMIT = 8
_WARM_CITATION_BYTES = 12_000


def warm_citations(rows, *, allowed_source_ids: set[str] | None = None) -> list[dict]:
    """Passage bodies for citations this conversation already used.

    Newest citation first. Uncited retrieved passages stay out, and so does a
    passage whose source is outside an explicit source selection. The byte cap
    is separate from the history transcript budget.
    """
    chosen: list[dict] = []
    seen: set[str] = set()
    used = 0
    for row in reversed(rows):
        if row["role"] != "assistant":
            continue
        evidence = row["evidence"] or {}
        bodies = {item.get("chunkId"): item for item in evidence.get("contextBlock") or []
                  if isinstance(item, dict) and item.get("chunkId")}
        for citation in evidence.get("citations") or []:
            if not isinstance(citation, dict):
                continue
            chunk_id = citation.get("chunkId")
            if not chunk_id or chunk_id in seen:
                continue
            body = bodies.get(chunk_id)
            text = body.get("text") if isinstance(body, dict) else None
            source_id = body.get("sourceId") if isinstance(body, dict) else None
            if not isinstance(text, str) or not text or not source_id:
                continue
            seen.add(chunk_id)
            if allowed_source_ids is not None and str(source_id) not in allowed_source_ids:
                continue
            size = len(text.encode("utf-8"))
            if size > _WARM_CITATION_BYTES or used + size > _WARM_CITATION_BYTES:
                continue
            chosen.append({
                "chunkId": str(chunk_id),
                "sourceId": str(source_id),
                "sourceName": str(body.get("sourceName") or citation.get("sourceName") or ""),
                "locatorLabel": str(body.get("locatorLabel") or citation.get("locatorLabel") or ""),
                "text": text,
            })
            used += size
            if len(chosen) >= _WARM_CITATION_LIMIT:
                return chosen
    return chosen


def public_turn(row):
    return {"sequence": row["sequence"], "requestId": str(row["request_id"]),
            "role": row["role"], "text": row["text"], "status": row["status"],
            "evidence": row["evidence"], "createdAt": row["created_at"].isoformat()}


def public_summary(row):
    return {"summaryId": str(row["summary_id"]), "conversationId": str(row["conversation_id"]),
            "state": row["state"], "expectedSequence": row["expected_sequence"],
            "candidateCoveredThrough": row["candidate_covered_through"],
            "coveredThrough": row["covered_through"],
            "fingerprint": row["input_fingerprint"], "text": row["summary_text"],
            "referenceMetadata": row["reference_metadata"], "failureCode": row["failure_code"],
            "profileId": str(row["profile_id"]) if row["profile_id"] else None,
            "profileRevision": row["profile_revision"],
            "contextEpoch": row.get("context_epoch", 0),
            "createdAt": row["created_at"].isoformat(), "updatedAt": row["updated_at"].isoformat()}
