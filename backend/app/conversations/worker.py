"""Single-attempt generation worker for durable provisional conversation turns."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from app.api.schemas import QueryRequest
from app.conversations.store import ConversationStore
from app.db import queries
from app.db.connection import release_borrow
from app.debug.registry import OverrideError, validate_retrieval, validate_settings
from app.jobs.store import JobCancelled, SafeJobError
from app.jobs.types import Job
from app.jobs.worker import JobContext
from app.operations.budget import fence_violation

PROGRESS_STAGES = frozenset({"planning", "retrieving", "routing", "generating", "using_tools",
                             "validating", "validated", "researching", "comparing", "finalizing"})
_TOOL_PROGRESS_EVENTS = frozenset({"tool_started", "tool_replayed", "tool_completed",
                                   "tool_failed", "tool_unknown"})
_MAX_PROVISIONAL_BYTES = 32 * 1024
_MAX_PROVISIONAL_EVENTS = 128
_DELTA_BATCH_BYTES = 1024


@dataclass(frozen=True)
class ConversationTurnResult:
    response: dict[str, Any]
    source_snapshot: dict[str, int]
    collection_id: UUID
    operation_id: UUID | None
    profile_selections: tuple[tuple[UUID, int], ...]


def _request_ids(job: Job) -> tuple[UUID, UUID]:
    try:
        return UUID(job.payload["conversationId"]), UUID(job.payload["requestId"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SafeJobError("invalid_job", "Conversation request could not be loaded.") from exc


class _TurnGuard:
    """Raises unless this job still owns its pending request over unchanged source revisions."""

    def __init__(self, conn, context, job, conversation_id, request_id, collection_id, source_snapshot):
        self.conn, self.context, self.job = conn, context, job
        self.conversation_id, self.request_id = conversation_id, request_id
        self.collection_id, self.source_snapshot = collection_id, source_snapshot

    def __call__(self) -> None:
        conn, job = self.conn, self.job
        self.context.check_cancelled()
        current = conn.execute(queries.get("get_conversation"), {
            "conversation_id": self.conversation_id, "workspace_key": job.scope_key,
        }).fetchone()
        conn.commit()
        if current is None or current["pending_request_id"] != self.request_id:
            raise JobCancelled(str(job.job_id))
        rows = conn.execute(queries.get("list_source_fences_for_workspace"), {
            "collection_id": self.collection_id, "workspace_key": job.scope_key,
        }).fetchall()
        revisions = {str(item["source_id"]): item for item in rows}
        for source_id, revision in self.source_snapshot.items():
            source = revisions.get(source_id)
            if (source is None or source["status"] != "indexed"
                    or source["collection_id"] != self.collection_id
                    or source["index_revision"] != revision):
                raise HTTPException(409, "selected source revision changed; refresh before generating")
        conn.commit()
        # A provisional flush calls the guard during provider streaming; returning
        # the borrow here keeps the execution pool free between checks.
        release_borrow(conn)


class _ProvisionalStream:
    """Persists the model's unverified output as bounded, batched progress events."""

    def __init__(self, context, guard: _TurnGuard):
        self.context, self.guard = context, guard
        self._buffer = ""
        self._bytes = 0
        self._events = 0
        self._truncated = False

    def text(self, value: str) -> None:
        if self._truncated:
            return
        for character in value:
            self._buffer += character
            if len(self._buffer.encode("utf-8")) >= _DELTA_BATCH_BYTES:
                self.flush()
                if self._truncated:
                    return
        # Provider chunks are already bounded by the provider seam. Persist each
        # chunk promptly so reconnects can recover output while it is generating.
        self.flush()

    def reset(self) -> None:
        self._buffer = ""
        if self._events >= _MAX_PROVISIONAL_EVENTS:
            self._truncated = True
            return
        self.context.progress({"kind": "provisional_reset"})
        self._events += 1

    def flush(self) -> None:
        if not self._buffer:
            return
        self.guard()
        size = len(self._buffer.encode("utf-8"))
        if self._bytes + size > _MAX_PROVISIONAL_BYTES or self._events >= _MAX_PROVISIONAL_EVENTS:
            self._truncated = True
            self._buffer = ""
            return
        self.context.progress({"kind": "provisional_delta", "text": self._buffer})
        self._bytes += size
        self._events += 1
        self._buffer = ""


def _report_stage(context, event) -> None:
    stage = event.get("stage") if isinstance(event, dict) else None
    if stage in _TOOL_PROGRESS_EVENTS:
        stage = "using_tools"
    if stage in PROGRESS_STAGES:
        context.progress({"kind": "stage", "stage": stage})


def _history_context(snapshot: dict, effective: dict) -> str:
    references = json.dumps(effective.get("artifactReferences", []), ensure_ascii=False,
                            separators=(",", ":"))
    return (snapshot.get("context", {}).get("historyContext", "")
            + "\nExact referenced artifact revisions:\n" + references)


class ConversationTurnExecutor:
    """Execute one leased conversation request and publish only after validation."""

    def __init__(self, *, settings, config, embedder, provider, connection_profiles,
                 connection_runtime, connect):
        self.settings = settings
        self.config = config
        self.embedder = embedder
        self.provider = provider
        self.connection_profiles = connection_profiles
        self.connection_runtime = connection_runtime
        self.connect = connect

    def __call__(self, job: Job, context: JobContext) -> ConversationTurnResult:
        if job.kind != "conversation.turn":
            raise SafeJobError("unsupported_job", "This work item is not a conversation turn.")
        conversation_id, request_id = _request_ids(job)
        conn = self.connect()
        try:
            store = ConversationStore(conn, job.scope_key)
            request_row = store.request_for_owner(conversation_id, request_id)
            if request_row["job_id"] != job.job_id or request_row["state"] != "pending":
                raise JobCancelled(str(job.job_id))
            snapshot = request_row["input_snapshot"]
            effective = snapshot.get("effective", {})
            debug = snapshot.get("debug") or {}
            try:
                settings = validate_settings(self.settings, debug.get("settings") or {})
                config = validate_retrieval(self.config, debug.get("retrieval") or {})
            except OverrideError as exc:
                raise SafeJobError("invalid_debug_overrides", str(exc)) from None
            system_prompt = debug.get("systemPrompt")
            collection_id = UUID(effective["collectionUuid"])
            source_snapshot = {str(key): int(value) for key, value in
                               effective.get("sourceSnapshot", {}).items()}
            request_data = snapshot.get("request", {})
            query_request = QueryRequest(
                query=request_data.get("query", snapshot.get("query", "")),
                collectionId=effective.get("collectionId"),
                sourceIds=effective.get("sourceIds"),
            )
            selected_profiles: dict[tuple[UUID, int], None] = {}
            operation_ids: list[UUID] = []

            def selected_profile(value):
                profile_id = getattr(value, "profile_id", None)
                revision = getattr(value, "profile_revision", None)
                if isinstance(profile_id, UUID) and isinstance(revision, int):
                    selected_profiles[(profile_id, revision)] = None

            guard = _TurnGuard(conn, context, job, conversation_id, request_id,
                               collection_id, source_snapshot)
            stream = _ProvisionalStream(context, guard)
            guard()
            from app.api.query_service import query_response

            response = query_response(
                query_request, conn, settings, config, self.embedder, self.provider,
                check_cancelled=guard, on_progress=lambda event: _report_stage(context, event),
                on_provisional_text=stream.text, on_provisional_reset=stream.reset,
                on_profile_selection=selected_profile, on_operation_created=operation_ids.append,
                source_snapshot=source_snapshot,
                workspace_key=job.scope_key, connection_profiles=self.connection_profiles,
                connection_runtime=self.connection_runtime,
                retrieval_query=effective.get("retrievalQuery"),
                history_context=_history_context(snapshot, effective),
                warm_citations=snapshot.get("context", {}).get("warmCitations") or [],
                conversation_id=conversation_id, conversation_request_id=request_id,
                system_prompt=system_prompt,
            )
            stream.flush()
            guard()
            return ConversationTurnResult(
                response.model_dump(mode="json"), source_snapshot, collection_id,
                operation_ids[-1] if operation_ids else None, tuple(selected_profiles),
            )
        finally:
            conn.close()

    @classmethod
    def publish_success(cls, conn, job: Job, value: ConversationTurnResult) -> dict[str, Any]:
        if not isinstance(value, ConversationTurnResult):
            raise SafeJobError("invalid_result", "Conversation answer failed validation.")
        conversation_id, request_id = _request_ids(job)
        request_row = conn.execute(queries.get("get_conversation_request"), {
            "conversation_id": conversation_id, "request_id": request_id,
        }).fetchone()
        if (request_row is None or request_row["job_id"] != job.job_id
                or request_row["state"] != "pending"):
            raise JobCancelled(str(job.job_id))

        def check_sources():
            current_by_id = {
                str(row["source_id"]): row
                for row in conn.execute(queries.get("lock_source_fences_for_workspace"), {
                    "source_ids": [UUID(source_id) for source_id in value.source_snapshot],
                    "workspace_key": job.scope_key,
                }).fetchall()
            }
            for source_id, revision in value.source_snapshot.items():
                current = current_by_id.get(str(UUID(source_id)))
                if (current is None or current["status"] != "indexed"
                        or current["collection_id"] != value.collection_id
                        or current["index_revision"] != revision):
                    raise SafeJobError("stale_source", "A source changed during generation; answer was withheld.")

        def check_profiles():
            for profile_id, profile_revision in value.profile_selections:
                current = conn.execute(queries.get("get_connection_profile_revision_for_share"), {
                    "profile_id": profile_id,
                }).fetchone()
                if current is None or not current["enabled"] or current["revision"] != profile_revision:
                    raise SafeJobError("stale_connection", "The selected model connection changed; answer was withheld.")

        check_sources()
        check_profiles()
        if value.operation_id is not None:
            cls._assert_operation_publishable(conn, value.operation_id, job.scope_key)
        result = ConversationStore(conn, job.scope_key).finish_uncommitted(
            conversation_id, request_id, value.response)
        # Recheck after the conversation write while all source, profile, and
        # operation rows remain locked in the job publication transaction.
        if value.operation_id is not None:
            cls._assert_operation_publishable(conn, value.operation_id, job.scope_key)
        check_profiles()
        check_sources()
        return {"conversationId": result["conversationId"], "requestId": str(request_id),
                "sequence": result["sequence"], "type": result["type"]}

    @staticmethod
    def _assert_operation_publishable(conn, operation_id: UUID, workspace_key: str) -> None:
        row = conn.execute(queries.get("get_operation_budget_for_update"), {
            "operation_id": operation_id,
        }).fetchone()
        if fence_violation(row, workspace_key) is not None:
            raise SafeJobError("operation_fence", "The answer operation no longer satisfies its publication budget.")


def build_default_conversation_executor(settings, config, *, connect):
    """Construct model and embedding seams through the shared runtime builder."""
    from app.runtime import build_runtime

    runtime = build_runtime(settings, config=config, skip_provider_when_routing=True)
    return ConversationTurnExecutor(
        settings=settings, config=config, embedder=runtime.embedder, provider=runtime.provider,
        connection_profiles=runtime.connection_profiles,
        connection_runtime=runtime.connection_runtime, connect=connect,
    )
