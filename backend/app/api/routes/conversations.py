"""Private conversations with immutable request identity and full bounded history."""

import json
import re
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api import deps
from app.api.query_service import query_response
from app.api.schemas import (
    ConversationRecord,
    ConversationStreamEventsResponse,
    ConversationStreamStartResponse,
    ConversationSummary,
    ConversationTurnResponse,
    QueryRequest,
)
from app.artifacts.storage import ArtifactStore
from app.atlas.records import normalize_code
from app.auth.dependencies import get_workspace_key
from app.conversations.store import (
    ConversationStore,
    fingerprint,
    history_prompt,
    research_history_prompt,
    warm_citations,
)
from app.conversations.worker import PROGRESS_STAGES
from app.db import queries
from app.db.connection import PooledConnection
from app.jobs.store import JobNotFound, JobStore
from app.jobs.types import JobStatus

router = APIRouter(prefix="/api/conversations")
Conn = Annotated[object, Depends(deps.get_conn)]
Workspace = Annotated[str, Depends(get_workspace_key)]


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
    expectedSequence: int = Field(ge=0, strict=True)
    mode: Literal["advising", "history"] = "advising"
    artifactReferences: list[ArtifactReference] = Field(default_factory=list, max_length=20)


@router.post("", response_model=ConversationSummary)
def create(body: CreateConversation, conn: Conn, workspace: Workspace):
    if not body.title.strip():
        raise HTTPException(422, "title must contain text")
    return ConversationStore(conn, workspace).create(body.title.strip())


@router.get("", response_model=list[ConversationSummary])
def listing(conn: Conn, workspace: Workspace):
    return ConversationStore(conn, workspace).list()


@router.get("/{conversation_id}", response_model=ConversationRecord)
def get(conversation_id: UUID, conn: Conn, workspace: Workspace,
        after: int = Query(default=0, ge=0), limit: int = Query(default=200, ge=1, le=200)):
    return ConversationStore(conn, workspace).turns(conversation_id, after=after, limit=limit)


def _job_store(request: Request) -> JobStore:
    return deps.request_job_store(request, state_attr="conversation_job_store",
                                 unavailable_detail="Job storage is unavailable.")


@router.post("/{conversation_id}/turns/{request_id}/abandon",
             response_model=ConversationTurnResponse, response_model_exclude_unset=True)
def abandon(conversation_id: UUID, request_id: UUID, request: Request, conn: Conn, workspace: Workspace):
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
    return result


_ANSWER_CODE_RE = re.compile(r"\b([A-Z]{2,7}) ?(\d{3}[A-Z]{0,2})\b")
_MAX_FOLLOW_UP_CODES = 8


def _answer_course_codes(text: str) -> list[str]:
    """Course codes a prior answer named, in order (cross-campus `_OX` aliases skipped)."""
    codes: list[str] = []
    for subject, number in _ANSWER_CODE_RE.findall(text or ""):
        code = normalize_code(f"{subject} {number}")
        if code is not None and code not in codes:
            codes.append(code)
    return codes[:_MAX_FOLLOW_UP_CODES]


# A bare follow-up does not name its own topic, so its retrieval query folds in
# the topic of an earlier question (EMO-007). These markers are deliberately
# broad: a pronoun, a leading "and/what about", or a very short query with no
# course code and no topic noun.
_FOLLOW_UP_START_RE = re.compile(r"^\s*(and|also|then|or|what about|how about)\b", re.IGNORECASE)
_ANAPHORA_RE = re.compile(
    r"\b(it|its|they|them|their|that|those|this|these|one|ones|the same|such)\b", re.IGNORECASE)
_TOPIC_NOUN_RE = re.compile(
    r"\b(major|majors|minor|minors|requirements?|degree|degrees|course|courses|class|classes|"
    r"program|programs|bs|ba|bba|track|tracks|concentration|certificate|prerequisite|"
    r"prerequisites|prereq|prereqs|curriculum|sequence|gpa|credit|credits|semester|honors)\b",
    re.IGNORECASE)
# Case-insensitive on purpose: a typed follow-up may use lower-case codes.
_FOLLOW_UP_CODE_RE = re.compile(r"\b([A-Za-z]{2,7}) ?(\d{3}[A-Za-z]{0,2})\b")


def _looks_like_bare_follow_up(query: str) -> bool:
    stripped = (query or "").strip()
    if not stripped:
        return False
    if _FOLLOW_UP_START_RE.match(stripped) or _ANAPHORA_RE.search(stripped):
        return True
    if _FOLLOW_UP_CODE_RE.search(stripped) or _TOPIC_NOUN_RE.search(stripped):
        return False
    return len(stripped.split()) <= 5


def _topic_anchor(prior_users):
    """The most recent earlier question that named a topic, not a bare follow-up."""
    for row in reversed(prior_users):
        if not _looks_like_bare_follow_up(row["text"]):
            return row
    return prior_users[-1]


def _effective_scope(rows, body):
    """Resolve the collection/source scope a saved follow-up inherits.

    The sourceIds fallback and the pronoun-aware retrieval rewrite are shared
    by the streaming and synchronous turn paths and must stay identical.
    """
    prior_users = [row for row in rows if row["role"] == "user"
                   and row["evidence"].get("request", {}).get("mode", "advising") == "advising"]
    prior = prior_users[-1]["evidence"].get("effective", {}) if prior_users else {}
    source_ids = body.sourceIds
    if "sourceIds" not in body.model_fields_set:
        source_ids = prior.get("sourceIds")
    retrieval_query = body.query
    if prior_users and _looks_like_bare_follow_up(body.query):
        # "Those courses" usually names what the last answer listed, and the topic
        # anchor is the last earlier question that actually named one, so a chain
        # of pronoun/marker follow-ups keeps talking about the same subject.
        anchor = _topic_anchor(prior_users)
        answers = [row for row in rows if row["role"] == "assistant"]
        codes = _answer_course_codes(answers[-1]["text"]) if answers else []
        discussed = f"\nCourses discussed: {', '.join(codes)}" if codes else ""
        retrieval_query = f"{anchor['text']}{discussed}\nFollow-up: {body.query}"
        if len(retrieval_query) > 20000:
            raise HTTPException(422, "follow-up retrieval query exceeds the input budget")
    return prior, source_ids, retrieval_query


def _warm_citations(rows, source_ids):
    allowed = None if source_ids is None else {str(item) for item in source_ids}
    return warm_citations(rows, allowed_source_ids=allowed)


def _research_scope(store, conversation_id, body):
    prior = store.last_advising_context(conversation_id)
    source_ids = body.sourceIds if "sourceIds" in body.model_fields_set else prior.get("sourceIds")
    # The research model resolves the question from its notebook/transcript;
    # legacy retrieval rewrites are not part of this workflow.
    return prior, source_ids, body.query


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


def _prepare_stream_snapshot(conversation_id, body, conn, settings, workspace, store):
    research = settings.advising_research_enabled and body.mode == "advising"
    rows = store.context(conversation_id, max_bytes=settings.conversation_context_max_bytes, archive=research)
    prior, source_ids, retrieval_query = (_research_scope(store, conversation_id, body)
                                         if research else _effective_scope(rows, body))
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
    artifact_context = json.dumps(references, ensure_ascii=False, separators=(",", ":"))
    history_context = (research_history_prompt(rows, max_bytes=min(
        6000, settings.conversation_context_max_bytes - len(artifact_context.encode("utf-8"))))
        if research else history_prompt(rows))
    if len((history_context + artifact_context).encode("utf-8")) > settings.conversation_context_max_bytes:
        raise HTTPException(422, "conversation and artifact revisions exceed the context budget")
    conn.commit()
    snapshot = {
        "request": body.model_dump(mode="json", exclude_unset=True),
        "query": body.query,
        "effective": {
            "collectionId": collection_name,
            "collectionUuid": str(collection_id),
            "sourceIds": [str(item) for item in source_ids] if source_ids is not None else None,
            "retrievalQuery": retrieval_query,
            "artifactReferences": references,
            "sourceSnapshot": source_snapshot,
        },
        "context": {"historyContext": history_context,
                    "warmCitations": _warm_citations(rows, source_ids)},
    }
    return snapshot


@router.post("/{conversation_id}/turns/stream", status_code=202,
             response_model=ConversationStreamStartResponse)
def start_stream(conversation_id: UUID, body: ConversationQuery, request: Request,
                 conn: Conn, workspace: Workspace):
    if body.mode != "advising":
        raise HTTPException(422, "history mode is a saved transcript read and does not start a model job")
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
    snapshot = _prepare_stream_snapshot(conversation_id, body, conn, request.app.state.settings, workspace, store)
    result = store.begin_stream(conversation_id, body.requestId, body.expectedSequence, snapshot)
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
    request_row = store.request_for_owner(conversation_id, request_id)
    job_id = request_row["job_id"]
    if job_id is None:
        return {"events": [], "nextEventId": after_event_id, "requestState": request_row["state"],
                "jobState": "unknown", "done": True}
    job = conn.execute(queries.get("get_job"), {"job_id": job_id}).fetchone()
    if job is None or job["scope_key"] != workspace:
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
    state = job["status"]
    return {"events": events, "nextEventId": next_event_id,
            "requestState": request_row["state"], "jobState": state,
            "done": request_row["state"] != "pending"
            or state in {JobStatus.SUCCEEDED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value}}


@router.post("/{conversation_id}/turns", response_model=ConversationTurnResponse,
             response_model_exclude_unset=True)
def turn(conversation_id: UUID, body: ConversationQuery, request: Request,
         conn: Conn, workspace: Workspace):
    store = ConversationStore(conn, workspace)
    snapshot = {"request": body.model_dump(mode="json", exclude_unset=True), "query": body.query}
    replay = store.replay(conversation_id, body.requestId, snapshot)
    if replay is not None:
        return replay
    settings = request.app.state.settings
    research = settings.advising_research_enabled and body.mode == "advising"
    rows = store.context(conversation_id, max_bytes=settings.conversation_context_max_bytes, archive=research)
    # The sequence check under the publication lock also fences context loaded
    # before another request's completion.
    prior, source_ids, retrieval_query = (_research_scope(store, conversation_id, body)
                                         if research else _effective_scope(rows, body))
    collection = body.collectionId or prior.get("collectionId")
    references = _load_references(conn, settings, workspace, body)
    conn.commit()
    snapshot["effective"] = {"collectionId": collection,
                             "sourceIds": [str(item) for item in source_ids] if source_ids is not None else None,
                             "retrievalQuery": retrieval_query, "artifactReferences": references}
    artifact_context = json.dumps(references, ensure_ascii=False, separators=(",", ":"))
    history_context = (research_history_prompt(rows, max_bytes=min(
        6000, settings.conversation_context_max_bytes - len(artifact_context.encode("utf-8"))))
        if research else history_prompt(rows))
    if len((history_context + artifact_context).encode("utf-8")) > settings.conversation_context_max_bytes:
        raise HTTPException(422, "conversation and artifact revisions exceed the context budget")
    replay = store.begin(conversation_id, body.requestId, body.expectedSequence, snapshot)
    if replay is not None:
        return replay
    def check_current():
        current = store.get(conversation_id)
        conn.commit()
        if current["pending_request_id"] != body.requestId:
            raise HTTPException(409, "conversation request was abandoned; completion was withheld")

    try:
        if body.mode == "history":
            # An explicit history action is a deterministic transcript read, not
            # an institutional answer or an ungrounded model interpretation.
            result = {"type": "history", "answer": "\n\n".join(
                f"Turn {row['sequence']} ({row['role']}):\n{row['text']}" for row in rows
            ) or "No prior turns are saved in this conversation.",
                      "turnReferences": [row["sequence"] for row in rows]}
        else:
            result = query_response(
                QueryRequest(query=body.query, collectionId=collection, sourceIds=source_ids),
                conn, settings, request.app.state.config, request.app.state.embedder,
                request.app.state.provider, workspace_key=workspace,
                connection_profiles=request.app.state.connection_profiles,
                connection_runtime=request.app.state.connection_runtime,
                retrieval_query=retrieval_query,
                history_context=history_context + "\nExact referenced artifact revisions:\n" + artifact_context,
                warm_citations=_warm_citations(rows, source_ids),
                check_cancelled=check_current,
                conversation_id=conversation_id, conversation_request_id=body.requestId,
            ).model_dump(mode="json")
    except HTTPException as exc:
        store.finish(conversation_id, body.requestId, {
            "type": "failed", "reason": str(exc.detail), "statusCode": exc.status_code,
        }, state="failed")
        raise
    except Exception:  # noqa: BLE001 - persist safe failure without leaking provider or private internals
        store.finish(conversation_id, body.requestId, {
            "type": "failed", "reason": "Conversation request failed. Saved turns were retained.",
            "statusCode": 503,
        }, state="failed")
        raise HTTPException(503, "conversation request failed; saved turns were retained") from None
    return store.finish(conversation_id, body.requestId, result)
