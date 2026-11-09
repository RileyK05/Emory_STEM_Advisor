"""The one answering path: research, shared by HTTP queries and saved turns."""

import logging
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg import Error as DatabaseError

from app.advising.research_repository import ScopeChanged, build_scope, verify_manifest
from app.advising.research_store import ResearchStore, RunClosed
from app.api import deps
from app.api.schemas import (
    AnswerResponse,
    ArtifactRequestInfo,
    ArtifactRevisesInfo,
    ClaimReviewUnit,
    QueryRequest,
    ResearchDecisionInfo,
)
from app.api.serialize import answer_response, provider_failure_detail
from app.config import Settings
from app.connections.models import (
    ConnectionUnavailable,
    CredentialUnreadable,
    MissingCredential,
    required_data_scopes,
)
from app.connections.selection import StaleProfileSelection, resolve
from app.db import queries
from app.db.connection import connector, release_borrow, resolved_database_url
from app.embeddings.base import Embedder
from app.gaps.store import GapError, GapStore
from app.generation.controller import GenerationCancelled, ToolConversationError
from app.generation.evidence import persist_response, record_attempt
from app.generation.operation import GenerationOperation
from app.generation.research_controller import ResearchController
from app.generation.research_final import (
    RESEARCH_PROMPT_VERSION,
    RESEARCH_RENDER_VERSION,
    render_final,
)
from app.generation.research_tools import ResearchSession
from app.jobs.store import JobCancelled, LeaseLost
from app.llm.base import Provider, ProviderError
from app.operations.budget import OperationBudgetError, OperationLimits
from app.retrieval.config import RetrievalConfig
from app.retrieval.trace import normalize_query, write_trace
from app.retrieval.types import RetrievalTrace, Retrieved
from app.tools.executions import ToolExecutionLedger, ToolLedgerError

logger = logging.getLogger(__name__)

FAILED_ANSWER = "The advisor couldn't answer that. Try again."


def _noop(*args):
    pass


def research_sampling(settings) -> dict[str, str]:
    """The sampling fields research sets, leaving a connection's own choice alone."""
    if (settings.research_reasoning_effort is None or settings.llm_reasoning_effort is not None
            or settings.llm_provider not in {"openai", "openai-compatible"}):
        return {}
    return {"llm_sampling_policy": "model_default",
            "llm_reasoning_effort": settings.research_reasoning_effort}


def _settle_usage(conn, workspace_key: str, trace_id: UUID, answer, settings) -> None:
    """Record measured usage after a call without retroactively denying the answer.

    The next request is denied by the bucket check; the current answer is already
    produced and is not thrown away because a post-call settlement crossed a cap.
    """
    from app.pricing.service import PricingService
    from app.quotas.service import QuotaService

    total = (answer.prompt_tokens or 0) + (answer.completion_tokens or 0)
    try:
        if total > 0:
            QuotaService.settle_generation(
                conn, workspace_key=workspace_key, request_id=trace_id,
                bucket_type="tokens", delta_count=total, enforce=False, settings=settings)
        _, cost, _ = PricingService.compute_call_cost(
            conn, provider=settings.llm_provider,
            model_id=answer.model_id or settings.llm_model_id or settings.hf_model_id,
            created_at=datetime.now(UTC), prompt_tokens=answer.prompt_tokens,
            completion_tokens=answer.completion_tokens,
            cached_prompt_tokens=answer.cached_prompt_tokens, usage_source=answer.usage_source)
        if cost is not None:
            QuotaService.settle_generation(
                conn, workspace_key=workspace_key, request_id=trace_id,
                bucket_type="cost_usd", delta_count=0, delta_amount=float(cost),
                enforce=False, settings=settings)
    except Exception:
        logger.exception("post-call quota settlement failed for %s", workspace_key)


def _observe_gap(conn, workspace_key, collection_id, body, trace, event_kind):
    """Track the question for the unanswered-questions list without undoing a published answer."""
    try:
        GapStore(conn, workspace_key).observe(
            collection_id=collection_id, query=body.query, source_ids=body.sourceIds,
            trace_id=trace.trace_id, event_kind=event_kind,
        )
    except (GapError, DatabaseError):
        conn.rollback()
        trace.warnings.append("Gap tracking is temporarily unavailable.")


def _fence_sources(conn, workspace_key, collection_id, body, source_snapshot):
    """The source IDs this query may read, checked against the workspace's current index state."""
    if source_snapshot is None and body.sourceIds is None:
        return None
    rows = conn.execute(queries.get("list_source_fences_for_workspace"),
                        {"collection_id": collection_id, "workspace_key": workspace_key}).fetchall()
    if source_snapshot is None:
        if not set(body.sourceIds) <= {row["source_id"] for row in rows}:
            raise HTTPException(status_code=404, detail="selected source not found in collection")
        return body.sourceIds
    current = {str(row["source_id"]): row for row in rows}
    for source_id, revision in source_snapshot.items():
        source = current.get(str(source_id))
        if (source is None or source["status"] != "indexed"
                or source["collection_id"] != collection_id
                or source["index_revision"] != revision):
            raise HTTPException(status_code=409, detail="selected source revision changed; refresh before generating")
    return [UUID(source_id) for source_id in source_snapshot]


class _AttemptAudit:
    """Records each provider attempt on the trace as it finishes, timed from the previous one."""

    def __init__(self, conn, outcome, operation, settings, selection):
        self.conn, self.outcome, self.operation = conn, outcome, operation
        self.settings, self.selection = settings, selection
        self._started = time.perf_counter()

    def record(self, result, error, request) -> None:
        record_attempt(self.conn, self.outcome.trace.trace_id, self.settings, result, error,
                       int((time.perf_counter() - self._started) * 1000),
                       operation_id=self.operation.operation_id, request=request,
                       selection=self.selection)
        self.conn.commit()
        self._started = time.perf_counter()


def _select_connection(conn, settings, body, workspace_key, profiles, runtime):
    """The evaluated tools-capable connection that serves this query, or None for the default provider."""
    if not settings.routing_enabled:
        if workspace_key != "development":
            raise HTTPException(503, "Research advising has no authorized tools-capable connection.")
        return None
    try:
        return resolve(settings, conn, body.query, data_scope="institutional_corpus",
                       required_data_scopes=required_data_scopes(workspace_key),
                       required_capabilities=frozenset({"tools"}),
                       profiles=profiles, runtime=runtime)
    except (ConnectionUnavailable, CredentialUnreadable, MissingCredential) as exc:
        raise HTTPException(503, "Research advising has no authorized tools-capable connection.") from exc


def query_response(
    body: QueryRequest, conn, settings: Settings, config: RetrievalConfig,
    embedder: Embedder, provider: Provider, *,
    check_cancelled: Callable[[], None] = _noop,
    on_progress: Callable[[dict], None] = _noop,
    on_profile_selection: Callable[[object], None] | None = None,
    on_operation_created: Callable[[UUID], None] | None = None,
    source_snapshot: dict[str, int] | None = None,
    workspace_key: str = "development",
    connection_profiles=None,
    connection_runtime=None,
    history_context: str | None = None,
    conversation_id: UUID | None = None,
    conversation_request_id: UUID | None = None,
) -> AnswerResponse:
    from app.auth.capabilities import EXECUTION_SETTINGS, execution_settings

    settings = execution_settings(conn, settings, workspace_key)
    admitted_settings = settings
    original_guard = check_cancelled

    def check_cancelled():
        original_guard()
        current = execution_settings(conn, admitted_settings, workspace_key)
        if any(getattr(admitted_settings, attr) and not getattr(current, attr)
               for attr in EXECUTION_SETTINGS):
            raise HTTPException(409, "feature access changed; refresh before starting new work")
        conn.commit()
        release_borrow(conn)

    check_cancelled()
    on_progress({"stage": "retrieving"})
    collection_id, _ = deps.resolve_collection(conn, settings, body.collectionId)
    source_ids = _fence_sources(conn, workspace_key, collection_id, body, source_snapshot)
    conn.commit()  # No read transaction across query embedding or generation.

    started = time.perf_counter()
    selection = _select_connection(conn, settings, body, workspace_key,
                                   connection_profiles, connection_runtime)
    if selection is not None:
        settings, provider = selection.settings, selection.provider
        if on_profile_selection:
            on_profile_selection(selection)
    if provider is None or not callable(getattr(provider, "generate", None)):
        raise HTTPException(503, "Research advising requires an available tools-capable connection.")
    settings = replace(settings,
        llm_max_output_tokens=settings.research_max_output_tokens,
        **research_sampling(settings))
    scope = build_scope(conn, collection_id=collection_id, workspace_key=workspace_key,
                        source_ids=source_ids, frozen_snapshot=source_snapshot)
    conn.commit()
    trace_id = write_trace(conn, collection_id=collection_id, query=body.query,
                           config_version=config.version, embed_model=settings.embedding_model_id,
                           refused=False, seams=[], contributions=[],
                           workspace_key=workspace_key)
    conn.commit()
    conn.execute(queries.get("mark_research_trace"), {"trace_id": trace_id})
    conn.commit()
    # Rounds request the ordinary allowance; the per-call cap admits the larger
    # allowance a truncated round retries with (research_controller).
    operation = GenerationOperation(conn, settings, trace_id, workspace_key, limits=OperationLimits(
        active_budget_seconds=settings.research_operation_timeout_seconds,
        max_calls=settings.research_max_calls,
        total_token_budget=settings.research_operation_token_budget,
        per_call_output_cap=max(settings.llm_max_output_tokens,
                                settings.research_truncation_max_output_tokens),
        per_call_timeout_seconds=settings.llm_timeout_seconds,
    ))
    if on_operation_created:
        on_operation_created(operation.operation_id)
    store = ResearchStore(conn, workspace_key)
    run, created = store.start(scope, run_id=uuid4(), operation_id=operation.operation_id,
        root_trace_id=trace_id, prompt_version=RESEARCH_PROMPT_VERSION,
        config_version=str(config.version), conversation_id=conversation_id,
        request_id=conversation_request_id)
    if not created:
        raise HTTPException(409, "This conversation request already owns a research run; reconnect to it.")
    session = ResearchSession(
        conn, scope, store, run, settings=settings, config=config, embedder=embedder,
        artifact_references=getattr(body, "artifactReferences", ()),
    )
    outcome = Retrieved([], RetrievalTrace(trace_id, collection_id, body.query,
                        normalize_query(body.query), config.version, settings.embedding_model_id,
                        False, ()))
    audit = _AttemptAudit(conn, outcome, operation, settings, selection)
    def guard():
        check_cancelled()
        if selection:
            selection.assert_current(conn)
    ledger = ToolExecutionLedger(connector(settings, resolved_database_url(settings, conn), pool_name="tool_execution"),
                                 max_tool_calls=settings.research_max_tool_calls)
    try:
        guard()
        session.open_workspace(conversation_id)
        decision, usage, system, user, model = ResearchController().run(
            session, provider, operation, ledger=ledger, check_cancelled=guard,
            on_progress=on_progress, question=body.query, history_context=history_context,
            selection_identity={"profile": str(selection.profile_id), "revision": selection.profile_revision}
            if selection else {"provider": settings.llm_provider, "model": settings.llm_model_id},
            on_attempt=lambda prepared, result, error: audit.record(result, error, prepared),
            before_provider_call=lambda: release_borrow(conn))
        rendered = render_final(decision, store.evidence(run.run_id), system_prompt=system,
                                user_prompt=user, usage=usage, model_id=model)
        outcome = replace(outcome, chunks=rendered.chunks)
        claims = [{"text": b["text"], "citation_numbers": b["citationNumbers"]}
                  for b in rendered.blocks if b["kind"] == "fact"]
        guard()
        conn.commit()
        on_progress({"stage": "validating"})
        with conn.transaction():
            verify_manifest(conn, scope.manifest())
            if selection:
                selection.assert_current(conn, lock=True, commit=False)
            operation.assert_publishable()
            response_id = persist_response(conn, outcome, status="answer", answer=rendered.answer,
                                            prompt_version=RESEARCH_PROMPT_VERSION, claim_units=claims)
            store.record_decision(run.run_id, decision, render_version=RESEARCH_RENDER_VERSION,
                                  response_id=response_id, supplied=session.supplied, commit=False)
            # Saved-turn audit settles atomically with its assistant turn in the worker.
            if conversation_id is None:
                status = (decision.outcome if decision.outcome in {"clarification", "insufficient_evidence"}
                          else "partial" if decision.completeness == "partial" else "completed")
                store.finish(run.run_id, status, "advisor_finish", commit=False)
            operation.assert_publishable()
        _settle_usage(conn, workspace_key, trace_id, rendered.answer, settings)
        _observe_gap(conn, workspace_key, collection_id, body, outcome.trace,
                     "generation_withheld" if decision.outcome == "insufficient_evidence"
                     else "retrieval_success")
        response = answer_response(outcome, rendered.answer, settings=settings, trace_id=trace_id,
                                   latency_ms=int((time.perf_counter() - started) * 1000))
        response.claims = [ClaimReviewUnit(text=c["text"], citationChunkIds=[
            str(rendered.chunks[n - 1].chunk_id) for n in c["citation_numbers"]]) for c in claims]
        artifact = None
        if decision.artifact is not None:
            request = decision.artifact
            artifact = ArtifactRequestInfo(
                kind=request.kind, title=request.title, brief=request.brief,
                purpose=request.purpose,
                revises=ArtifactRevisesInfo(
                    artifactId=str(request.revises.artifact_id),
                    revisionId=str(request.revises.revision_id),
                ) if request.revises else None,
            )
        response.research = ResearchDecisionInfo(runId=str(run.run_id), outcome=decision.outcome,
            completeness=decision.completeness, blocks=rendered.blocks, artifact=artifact,
            unresolvedQuestions=list(decision.unresolved_questions))
        cited_source_ids = sorted({
            UUID(str(item.source_id)) for item in rendered.answer.citations if item.source_id
        })
        if cited_source_ids:
            from app.advising.applicability import evaluate_sources_applicability

            response.applicability = evaluate_sources_applicability(
                conn,
                cited_source_ids,
                declared_cohort=getattr(body, "studentCohort", None),
                query=body.query,
            )
        on_progress({"stage": "validated"})
        return response
    except Exception as exc:
        conn.rollback()
        interrupted = not isinstance(exc, (ScopeChanged, StaleProfileSelection, OperationBudgetError,
                                           ProviderError, ToolConversationError, ToolLedgerError))
        reason = str(exc) if isinstance(exc, (ToolConversationError, OperationBudgetError)) else type(exc).__name__
        status = "cancelled" if isinstance(exc, (GenerationCancelled, JobCancelled)) else (
            "interrupted" if interrupted or isinstance(exc, LeaseLost) else "failed")
        try:
            store.finish(run.run_id, status, reason)
        except RunClosed:
            pass  # Concurrent abandonment already settled the run; preserve the original error.
        if isinstance(exc, (GenerationCancelled, JobCancelled, LeaseLost)):
            raise
        if isinstance(exc, (ScopeChanged, StaleProfileSelection)):
            raise HTTPException(409, "Research sources or model connection changed; refresh and retry.") from exc
        if isinstance(exc, ProviderError):
            raise HTTPException(503, provider_failure_detail(exc, "Research model")) from exc
        if isinstance(exc, (OperationBudgetError, ToolConversationError, ToolLedgerError)):
            detail = str(exc) if str(exc) == FAILED_ANSWER else "Research could not finish safely: " + str(exc)
            raise HTTPException(422, detail) from exc
        raise
