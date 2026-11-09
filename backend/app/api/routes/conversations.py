"""Private conversations with immutable request identity and full bounded history."""

import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api import deps
from app.api.admission import AdmissionLimits
from app.api.schemas import (
    ConversationRecord,
    ConversationStreamEventsResponse,
    ConversationStreamStartResponse,
    ConversationSummary,
    ConversationTurnResponse,
    QueryRequest,
)
from app.api.serialize import public_payload
from app.artifacts.storage import ArtifactStore
from app.auth.dependencies import get_principal, get_workspace_key
from app.auth.models import Principal
from app.conversations.store import (
    ConversationStore,
    fingerprint,
    research_history_prompt,
)
from app.conversations.worker import PROGRESS_STAGES
from app.db import queries
from app.db.connection import PooledConnection
from app.debug.dependencies import get_debug
from app.debug.store import AppliedDebug
from app.jobs.store import JobNotFound, JobStore
from app.jobs.types import JobStatus

router = APIRouter(prefix="/api/conversations")
Conn = Annotated[object, Depends(deps.get_conn)]
Workspace = Annotated[str, Depends(get_workspace_key)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]
Debug = Annotated[AppliedDebug | None, Depends(get_debug)]


class CreateConversation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)


class ArtifactReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifactId: UUID
    revisionId: UUID


class ConversationQuery(QueryRequest):
    model_config = ConfigDict(extra="forbid")
    requestId: UUID
    expectedSequence: int = Field(ge=0, le=2_147_483_647, strict=True)
    artifactReferences: list[ArtifactReference] = Field(default_factory=list, max_length=20)


@router.post("", response_model=ConversationSummary)
def create(body: CreateConversation, conn: Conn, workspace: Workspace):
    if not body.title.strip():
        raise HTTPException(422, "title must contain text")
    return ConversationStore(conn, workspace).create(body.title.strip())


@router.get("", response_model=list[ConversationSummary])
def listing(
    conn: Conn,
    workspace: Workspace,
    archived: bool = Query(default=False),
    limit: int = Query(default=500, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=10_000),
):
    return ConversationStore(conn, workspace).list(
        archived=archived, limit=limit, offset=offset)


@router.post("/{conversation_id}/archive", response_model=ConversationSummary)
def archive(conversation_id: UUID, conn: Conn, workspace: Workspace):
    return ConversationStore(conn, workspace).archive(conversation_id)


@router.post("/{conversation_id}/restore", response_model=ConversationSummary)
def restore(conversation_id: UUID, conn: Conn, workspace: Workspace):
    return ConversationStore(conn, workspace).restore(conversation_id)


@router.get("/{conversation_id}", response_model=ConversationRecord)
def get(conversation_id: UUID, conn: Conn, workspace: Workspace, principal: PrincipalDep,
        after: int = Query(default=0, ge=0), limit: int = Query(default=200, ge=1, le=200)):
    return public_payload(
        ConversationStore(conn, workspace).turns(conversation_id, after=after, limit=limit), principal)


def _job_store(request: Request) -> JobStore:
    return deps.request_job_store(request, state_attr="conversation_job_store",
                                 unavailable_detail="Job storage is unavailable.")


@router.post("/{conversation_id}/turns/{request_id}/abandon",
             response_model=ConversationTurnResponse, response_model_exclude_unset=True)
def abandon(conversation_id: UUID, request_id: UUID, request: Request, conn: Conn, workspace: Workspace,
            principal: PrincipalDep):
    store = ConversationStore(conn, workspace)
    prior = store.request_for_owner(conversation_id, request_id)
    result = store.abandon(conversation_id, request_id)
    # The conversation fence is committed. Return the pooled request borrow to
    # its pool before the store-owned cancellation borrows from the same pool,
    # so a small pool cannot self-wait (RT-01). A directly created connection
    # (pooling disabled, or a test override) is owned by its borrower: leave it.
    if isinstance(conn, PooledConnection):
        conn.close()
    # Commit the conversation fence before acquiring a job row lock. A worker
    # publishes job-then-conversation, so this ordering avoids lock inversion.
    if prior["job_id"] is not None:
        try:
            _job_store(request).cancel(prior["job_id"])
        except JobNotFound:
            pass
    return public_payload(result, principal)


def _scope(store, conversation_id, body):
    """The collection and sources a turn inherits from the conversation's last advising turn.

    The research model resolves a follow-up from the transcript itself, so the
    question is passed through unchanged.
    """
    prior = store.last_advising_context(conversation_id)
    source_ids = body.sourceIds if "sourceIds" in body.model_fields_set else prior.get("sourceIds")
    return prior, source_ids


def _load_references(conn, settings, workspace, body):
    """Load referenced artifact revisions under the conversation byte budget."""
    artifacts = ArtifactStore(conn, workspace_key=workspace, data_dir=settings.data_dir)
    references = []
    reference_bytes = 0
    for reference in body.artifactReferences:
        record = artifacts.get(reference.artifactId, revision_id=reference.revisionId)
        reference_bytes += len(json.dumps(record["spec"], ensure_ascii=False).encode("utf-8"))
        if reference_bytes > settings.conversation_context_max_bytes:
            conn.commit()
            raise HTTPException(422, "referenced artifact revisions exceed the conversation context budget")
        references.append({"artifactId": str(reference.artifactId),
                           "revisionId": str(reference.revisionId), "spec": record["spec"]})
    return references


def _history_context(store, conversation_id, settings, references) -> str:
    """The transcript the research model reads, within the conversation context budget."""
    rows = store.context(conversation_id)
    artifact_bytes = len(json.dumps(references, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8"))
    history = research_history_prompt(rows, max_bytes=min(
        6000, settings.conversation_context_max_bytes - artifact_bytes))
    if len(history.encode("utf-8")) + artifact_bytes > settings.conversation_context_max_bytes:
        raise HTTPException(422, "conversation and artifact revisions exceed the context budget")
    return history


def _prepare_stream_snapshot(conversation_id, body, conn, settings, workspace, store):
    prior, source_ids = _scope(store, conversation_id, body)
    collection_name = body.collectionId or prior.get("collectionId")
    if collection_name is None:
        collection_name = settings.default_collection
    collection_id, collection_name = deps.resolve_collection(conn, settings, collection_name)
    available = conn.execute(queries.get("list_source_fences_for_workspace"), {
        "collection_id": collection_id, "workspace_key": workspace,
    }).fetchall()
    indexed = [row for row in available if row["status"] == "indexed"]
    available_revisions = {str(row["source_id"]): row["index_revision"] for row in indexed}
    if source_ids is not None and not {str(item) for item in source_ids} <= set(available_revisions):
        raise HTTPException(404, "selected source not found in collection")
    source_snapshot = {source_id: revision for source_id, revision in available_revisions.items()
                       if source_ids is None or source_id in {str(item) for item in source_ids}}
    references = _load_references(conn, settings, workspace, body)
    history_context = _history_context(store, conversation_id, settings, references)
    conn.commit()
    return {
        "request": body.model_dump(mode="json", exclude_unset=True),
        "query": body.query,
        "effective": {
            "collectionId": collection_name,
            "collectionUuid": str(collection_id),
            "sourceIds": [str(item) for item in source_ids] if source_ids is not None else None,
            "artifactReferences": references,
            "sourceSnapshot": source_snapshot,
        },
        "context": {"historyContext": history_context},
    }


@router.post("/{conversation_id}/turns/stream", status_code=202,
             response_model=ConversationStreamStartResponse)
def start_stream(conversation_id: UUID, body: ConversationQuery, request: Request,
                 conn: Conn, workspace: Workspace, debug: Debug):
    store = ConversationStore(conn, workspace)
    # A replay is an inspection of the immutable request/job record. Resolve it
    # before loading current sources, artifacts, or transcript context: those
    # may have changed since the original request was accepted.
    store.get(conversation_id)  # Authorize before checking whether this identity exists.
    prior = conn.execute(queries.get("get_conversation_request"), {
        "conversation_id": conversation_id, "request_id": body.requestId,
    }).fetchone()
    conn.commit()
    if prior is not None:
        request_snapshot = {"request": body.model_dump(mode="json", exclude_unset=True)}
        if prior["fingerprint"] != fingerprint(request_snapshot):
            raise HTTPException(409, "request identity is already bound to different inputs")
        if prior["job_id"] is None:
            raise HTTPException(409, "request has no durable stream job; inspect or abandon it before continuing")
        job = conn.execute(queries.get("get_job"), {"job_id": prior["job_id"]}).fetchone()
        conn.commit()
        if job is None or job["scope_key"] != workspace:
            return {"conversationId": str(conversation_id), "requestId": str(body.requestId),
                    "jobId": str(prior["job_id"]), "state": "unknown"}
        return {"conversationId": str(conversation_id), "requestId": str(body.requestId),
                "jobId": str(prior["job_id"]), "state": job["status"]}
    settings = deps.execution_settings(
        request, conn, workspace, debug.settings if debug is not None else None)
    admission = AdmissionLimits(settings.admission_max_pending_workspace,
                                settings.admission_max_pending_global)
    # Reject a full queue before loading context. The final check remains
    # inside `begin_stream`'s transaction, so this is best-effort.
    # Quota settlement also stays in that transaction: a missing source, a
    # context refusal, or a stale sequence must not consume the daily bucket.
    if admission.enabled:
        with conn.transaction():
            admission.check(conn, workspace)
    snapshot = _prepare_stream_snapshot(conversation_id, body, conn, settings, workspace, store)
    if debug is not None:
        snapshot["debug"] = debug.snapshot()
    result = store.begin_stream(conversation_id, body.requestId, body.expectedSequence,
                                snapshot, admission=admission, settings=settings)
    job_id = result.get("job_id")
    if job_id is None:
        return {"conversationId": str(conversation_id), "requestId": str(body.requestId),
                "jobId": None, "state": "unknown"}
    job = conn.execute(queries.get("get_job"), {"job_id": job_id}).fetchone()
    conn.commit()
    if job is None or job["scope_key"] != workspace:
        raise HTTPException(404, "conversation request not found")
    return {"conversationId": str(conversation_id), "requestId": str(body.requestId),
            "jobId": str(job_id), "state": job["status"]}


@router.get("/{conversation_id}/turns/{request_id}/events",
            response_model=ConversationStreamEventsResponse)
def stream_events(conversation_id: UUID, request_id: UUID, conn: Conn, workspace: Workspace,
                  after_event_id: int = Query(default=0, ge=0, alias="afterEventId"),
                  limit: int = Query(default=100, ge=1, le=100)):
    store = ConversationStore(conn, workspace)
    store.get(conversation_id)  # Authorize before reading request/job state.
    # One statement keeps request and job state from the same snapshot. Reading
    # them separately can report a terminal job beside a still-pending request
    # when a worker commits between the two reads (READ COMMITTED).
    request_row = conn.execute(queries.get("get_conversation_request_with_job"), {
        "conversation_id": conversation_id, "request_id": request_id,
    }).fetchone()
    if request_row is None:
        raise HTTPException(404, "conversation request not found")
    job_id = request_row["job_id"]
    if job_id is None:
        return {"events": [], "nextEventId": after_event_id,
                "requestState": request_row["request_state"],
                "jobState": "unknown", "done": True}
    if request_row["job_scope_key"] != workspace:
        raise HTTPException(404, "conversation request not found")
    rows = conn.execute(queries.get("list_job_events"), {
        "job_id": job_id, "after_event_id": after_event_id, "limit": limit,
    }).fetchall()
    events = []
    next_event_id = after_event_id
    for row in rows:
        next_event_id = max(next_event_id, row["event_id"])
        event_type, payload = row["event_type"], row["payload"] or {}
        item = {"eventId": row["event_id"]}
        if event_type == "progress":
            kind = payload.get("kind")
            if kind == "provisional_delta" and isinstance(payload.get("text"), str) and len(payload["text"].encode("utf-8")) <= 4096:
                item.update({"type": kind, "text": payload["text"]})
            elif kind == "provisional_reset":
                item["type"] = kind
            elif kind == "stage" and payload.get("stage") in PROGRESS_STAGES:
                item.update({"type": kind, "stage": payload["stage"]})
            else:
                continue
        else:
            status = payload.get("status")
            if status in {"queued", "running", "succeeded", "failed", "cancelled"}:
                item.update({"type": "state", "state": status})
            else:
                continue
        events.append(item)
    conn.commit()
    state = request_row["job_status"]
    return {"events": events, "nextEventId": next_event_id,
            "requestState": request_row["request_state"], "jobState": state,
            "done": request_row["request_state"] != "pending"
            or state in {JobStatus.SUCCEEDED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value}}
