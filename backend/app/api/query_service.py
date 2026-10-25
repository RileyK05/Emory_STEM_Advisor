"""Shared grounded query workflow for HTTP and durable workers."""

import logging
import re
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg import Error as DatabaseError

from app.admin.prompt_registry import PromptRegistry
from app.advising.catalog import load_advising_context
from app.api import deps
from app.api.schemas import AnswerResponse, QueryRequest, RefusalResponse
from app.api.serialize import answer_response, refusal_response
from app.config import Settings
from app.connections.models import (
    ConnectionUnavailable,
    CredentialUnreadable,
    MissingCredential,
    required_data_scopes,
)
from app.connections.selection import StaleProfileSelection, resolve, resolve_quality_fallback
from app.db import queries
from app.db.connection import connector, release_borrow, resolved_database_url
from app.embeddings.base import Embedder
from app.gaps.store import GapError, GapStore
from app.generation.answer import (
    ANSWER_PROMPT_NAME,
    PROMPT_VERSION,
    GenerationRejected,
    answer_from_context,
    citation_repair_hint,
)
from app.generation.answer_tools import ToolAnswerProvider, answer_tool_context
from app.generation.controller import ToolConversationController, ToolConversationError
from app.generation.evidence import persist_response, record_attempt
from app.generation.operation import GenerationOperation
from app.generation.query_plan import PlanRejected, parse_plan, planning_request
from app.llm.base import Provider, ProviderError, RateLimitError, TransientProviderError
from app.operations.budget import OperationBudgetError
from app.retrieval.config import RetrievalConfig
from app.retrieval.directory import load_directory
from app.retrieval.retrieve import retrieve
from app.retrieval.types import CitedChunk, Refusal, Retrieved
from app.tools.executions import ToolExecutionLedger, ToolLedgerError


def _noop(*args):
    pass


_DEFAULT_RETRY_DELAY_SECONDS = 1.0
_MAX_RETRY_DELAY_SECONDS = 30.0
logger = logging.getLogger(__name__)


def _settle_usage(conn, workspace_key: str, trace_id: UUID, answer, settings) -> None:
    """Record measured usage after a call without retroactively denying the answer.

    The next request is denied by the bucket check; the current answer is already
    produced and is not thrown away because a post-call settlement crossed a cap.
    """
    from app.pricing.service import PricingService
    from app.quotas.service import QuotaService

    prompt = answer.prompt_tokens or 0
    completion = answer.completion_tokens or 0
    total = prompt + completion
    try:
        if total > 0:
            QuotaService.settle_generation(
                conn,
                workspace_key=workspace_key,
                request_id=trace_id,
                bucket_type="tokens",
                delta_count=total,
                enforce=False,
            )
        _, cost, _ = PricingService.compute_call_cost(
            conn,
            provider=settings.llm_provider,
            model_id=answer.model_id or settings.llm_model_id or settings.hf_model_id,
            created_at=datetime.now(UTC),
            prompt_tokens=answer.prompt_tokens,
            completion_tokens=answer.completion_tokens,
            cached_prompt_tokens=answer.cached_prompt_tokens,
            usage_source=answer.usage_source,
        )
        if cost is not None:
            QuotaService.settle_generation(
                conn,
                workspace_key=workspace_key,
                request_id=trace_id,
                bucket_type="cost_usd",
                delta_count=0,
                delta_amount=float(cost),
                enforce=False,
            )
    except Exception:
        logger.exception("post-call quota settlement failed for %s", workspace_key)


def _answer_with_provider_retry(generate_answer, *, remaining_calls: int,
                                remaining_seconds: float, check_cancelled,
                                max_retries: int = 1):
    """Re-run a rate-limited or transient provider call inside the operation fence.

    A provider rate limit used to surface immediately as a 503 that blamed the
    settings (EMO-029). Retry once within the remaining call/time budget, honoring
    ``retry_after``; anything else still propagates.
    """
    retries = 0
    while True:
        try:
            return generate_answer()
        except (RateLimitError, TransientProviderError) as exc:
            retries += 1
            delay = min(float(getattr(exc, "retry_after", None)
                              or _DEFAULT_RETRY_DELAY_SECONDS), _MAX_RETRY_DELAY_SECONDS)
            if retries > max_retries or remaining_calls - 1 < retries or remaining_seconds <= delay:
                raise
            check_cancelled()
            time.sleep(delay)
            remaining_seconds -= delay


def _requests_course_sequence(query: str) -> bool:
    # "DNA sequence analysis" is a topic query, not a request for course ordering.
    return bool(re.search(
        r"\b(?:courses?\s+sequence|sequence\s+tool|dependency\s+stages?|"
        r"prerequisite\s+order|sequence\s+(?:should|do|to)\s+(?:i\s+)?take)\b",
        query, re.IGNORECASE))


def _observe_gap(conn, workspace_key, collection_id, body, outcome, event_kind):
    """Track the final retrieval observation without undoing a published answer."""
    try:
        GapStore(conn, workspace_key).observe(
            collection_id=collection_id, query=body.query, source_ids=body.sourceIds,
            trace_id=outcome.trace.trace_id, event_kind=event_kind,
        )
    except (GapError, DatabaseError):
        conn.rollback()
        outcome.trace.warnings.append("Gap tracking is temporarily unavailable.")


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

    def switch_to(self, settings, selection) -> None:
        self.settings, self.selection = settings, selection

    def record(self, result, error, request) -> None:
        record_attempt(self.conn, self.outcome.trace.trace_id, self.settings, result, error,
                       int((time.perf_counter() - self._started) * 1000),
                       operation_id=self.operation.operation_id, request=request,
                       selection=self.selection)
        self.conn.commit()
        self._started = time.perf_counter()


class _SelectionFailed(Exception):
    """No connection may serve this query; persisted once a retrieval trace exists."""

    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason, self.detail = reason, detail


def _select_connection(conn, settings, body, workspace_key, required_scopes,
                       required_capabilities, profiles, runtime):
    """The evaluated connection that serves this query, or None for the default provider."""
    if not settings.routing_enabled:
        if workspace_key != "development":
            raise _SelectionFailed("private_routing_required",
                                   "private generation requires an evaluated, authorized connection")
        return None
    try:
        return resolve(settings, conn, body.query, data_scope="institutional_corpus",
                       required_data_scopes=required_scopes,
                       required_capabilities=required_capabilities,
                       profiles=profiles, runtime=runtime)
    except (ConnectionUnavailable, CredentialUnreadable, MissingCredential) as exc:
        raise _SelectionFailed(type(exc).__name__,
                               "no evaluated model connection can serve this request") from exc


def _fail_selection(conn, outcome, failure: _SelectionFailed):
    persist_response(conn, outcome, status="failed", reason=failure.reason)
    raise HTTPException(503, detail=failure.detail) from failure.__cause__


def _plan_query(conn, settings, provider, selection, operation, config, question, *,
                collection_id, source_ids, workspace_key, check_cancelled):
    """A validated search plan, or (None, reason) for the unplanned fallback.

    The planning call is reserved and accounted in the query's operation like any
    other call, including when it fails. A plan that cannot be used falls back to
    the scoped unplanned retrieval; budget exhaustion after the call propagates.
    """
    if settings.llm_provider == "hf-local":
        return None, "local model context is too small for the catalog directory"
    if not callable(getattr(provider, "generate", None)):
        return None, "provider has no typed generation seam"
    directory = load_directory(conn, collection_id=collection_id, workspace_key=workspace_key,
                               source_ids=source_ids)
    conn.commit()  # Release the read before provider I/O.
    release_borrow(conn)
    if directory.empty:
        return None, "no catalog entries in scope"
    check_cancelled()
    try:
        request = operation.prepare(planning_request(
            directory, question, config.planning, timeout_seconds=settings.llm_timeout_seconds,
            temperature=0 if settings.llm_sampling_policy == "greedy" else None))
    except OperationBudgetError as exc:
        return None, f"planning call exceeds the operation budget ({exc.code})"
    started = time.perf_counter()
    try:
        result = provider.generate(request)
    except Exception as exc:
        record_attempt(conn, None, settings, None, exc, int((time.perf_counter() - started) * 1000),
                       operation_id=operation.operation_id, request=request, selection=selection)
        conn.commit()
        operation.account(None, exc)
        if isinstance(exc, ProviderError):
            return None, f"planning call failed ({type(exc).__name__})"
        raise
    record_attempt(conn, None, settings, result, None, int((time.perf_counter() - started) * 1000),
                   operation_id=operation.operation_id, request=request, selection=selection)
    conn.commit()
    operation.account(result, None)
    try:
        plan = parse_plan(result.text, directory, config.planning)
    except PlanRejected as exc:
        return None, f"planning output rejected: {exc}"
    return plan, None


def _tool_answer_provider(conn, body, outcome, operation, settings, provider, selection, audit,
                          check_cancelled, on_progress, on_provisional_text, on_provisional_reset,
                          history_context, workspace_key):
    advising_context = load_advising_context(
        conn, collection_id=outcome.trace.collection_id, chunks=outcome.chunks,
    )
    conn.commit()  # Snapshot is read-only; release the transaction before model I/O.
    registry, scope, fingerprint = answer_tool_context(
        query=body.query, chunks=outcome.chunks, workspace_key=workspace_key,
        settings=settings, selection=selection, check_cancelled=check_cancelled,
        history_context=history_context,
        advising_context=advising_context,
    )
    return ToolAnswerProvider(
        provider,
        controller=ToolConversationController(
            max_tool_calls=settings.tool_max_calls,
            max_provider_calls=settings.operation_max_calls,
        ),
        registry=registry, scope=scope, operation=operation,
        # The ledger's short transitions use a dedicated pool role, never the
        # pool held by this HTTP request or worker execution (RT-02): a nested
        # same-pool borrow can self-wait under a small pool.
        ledger=ToolExecutionLedger(connector(settings, resolved_database_url(settings, conn), pool_name="tool_execution"),
                                   max_tool_calls=settings.tool_max_calls),
        operation_id=operation.operation_id, workspace_key=workspace_key,
        scope_fingerprint=fingerprint,
        on_attempt=lambda prepared, result, error: audit.record(result, error, prepared),
        cancellation=check_cancelled,
        on_progress=lambda event, payload: on_progress({"stage": event, **payload}),
        on_provisional_text=on_provisional_text,
        on_provisional_reset=on_provisional_reset,
        final_repair=(lambda result: citation_repair_hint(
            result, outcome.chunks, query=body.query, history_context=history_context))
        if settings.answer_citation_repair_enabled else None,
        required_tool_names=frozenset({"advisor_plan_course_sequence"})
        if _requests_course_sequence(body.query) else frozenset(),
        before_provider_call=lambda: release_borrow(conn),
    )


def _publish_answer(conn, outcome, operation, attempt_selections, answer) -> None:
    """Persist the answer only if every connection used is still current and the operation is in budget."""
    try:
        with conn.transaction():
            for used in sorted(
                {(used.profile_id, used.profile_revision): used for used in attempt_selections}.values(),
                key=lambda used: (str(used.profile_id), used.profile_revision),
            ):
                used.assert_current(conn, lock=True, commit=False)
            operation.assert_publishable()
            persist_response(conn, outcome, status="answer", answer=answer)
            operation.assert_publishable()
    except StaleProfileSelection as exc:
        persist_response(conn, outcome, status="failed", reason="stale_connection")
        raise HTTPException(409, detail="model connection changed; retry this request") from exc
    except OperationBudgetError as exc:
        persist_response(conn, outcome, status="failed", reason=exc.code)
        raise HTTPException(422, detail=str(exc)) from exc


def _append_warm_citations(chunks: list[CitedChunk], warm) -> list[CitedChunk]:
    """Add previously cited passages that this retrieval did not already return."""
    if not warm:
        return list(chunks)
    present = {str(chunk.chunk_id) for chunk in chunks}
    merged = list(chunks)
    for item in warm:
        if not isinstance(item, dict):
            continue
        chunk_id = str(item.get("chunkId") or "")
        if not chunk_id or chunk_id in present:
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text:
            continue
        try:
            parsed_chunk = UUID(chunk_id)
            parsed_source = UUID(str(item.get("sourceId") or ""))
        except ValueError:
            continue
        merged.append(CitedChunk(
            chunk_id=parsed_chunk, source_id=parsed_source, chunk_index=0,
            source_name=str(item.get("sourceName") or ""),
            locator_label=str(item.get("locatorLabel") or ""), text=text, seams=(),
            normalized_score=0.0, source_slot=0, rank=len(merged) + 1,
        ))
        present.add(chunk_id)
    return merged


def query_response(
    body: QueryRequest, conn, settings: Settings, config: RetrievalConfig,
    embedder: Embedder, provider: Provider, *,
    check_cancelled: Callable[[], None] = _noop,
    on_progress: Callable[[dict], None] = _noop,
    on_provisional_text: Callable[[str], None] | None = None,
    on_provisional_reset: Callable[[], None] | None = None,
    on_profile_selection: Callable[[object], None] | None = None,
    on_operation_created: Callable[[UUID], None] | None = None,
    source_snapshot: dict[str, int] | None = None,
    workspace_key: str = "development",
    connection_profiles=None,
    connection_runtime=None,
    retrieval_query: str | None = None,
    history_context: str | None = None,
    warm_citations: list | None = None,
    conversation_id: UUID | None = None,
    conversation_request_id: UUID | None = None,
    system_prompt: str | None = None,
) -> AnswerResponse | RefusalResponse:
    from app.flags.service import EXECUTION_FLAGS, FeatureFlagService

    settings = FeatureFlagService.execution_settings(conn, settings, workspace_key)
    admitted_settings = settings
    original_guard = check_cancelled

    def check_cancelled():
        original_guard()
        current = FeatureFlagService.execution_settings(conn, admitted_settings, workspace_key)
        if any(getattr(admitted_settings, attr) and not getattr(current, attr)
               for attr in EXECUTION_FLAGS.values()):
            raise HTTPException(409, "feature access changed; refresh before starting new work")
        conn.commit()
        release_borrow(conn)

    check_cancelled()
    on_progress({"stage": "retrieving"})
    collection_id, _ = deps.resolve_collection(conn, settings, body.collectionId)
    effective_source_ids = _fence_sources(conn, workspace_key, collection_id, body, source_snapshot)
    conn.commit()  # No read transaction across query embedding or generation.
    if settings.advising_research_enabled and getattr(body, "mode", "advising") == "advising":
        from app.api.research_service import research_response

        return research_response(
            body, conn, settings, config, embedder, provider,
            collection_id=collection_id, source_ids=effective_source_ids,
            source_snapshot=source_snapshot, workspace_key=workspace_key,
            connection_profiles=connection_profiles, connection_runtime=connection_runtime,
            check_cancelled=check_cancelled, on_progress=on_progress,
            on_profile_selection=on_profile_selection, on_operation_created=on_operation_created,
            history_context=history_context, conversation_id=conversation_id,
            conversation_request_id=conversation_request_id)
    started = time.perf_counter()

    def latency_ms() -> int:
        return int((time.perf_counter() - started) * 1000)

    required_scopes = required_data_scopes(workspace_key)
    required_capabilities = frozenset({"tools"}) if settings.tools_enabled else frozenset()
    selection = selection_failure = operation = plan = None
    plan_warning = None
    if settings.query_planning_enabled:
        # Planning is the operation's first model call, so the connection and the
        # persisted budget exist before retrieval. The operation id becomes the
        # trace id, as it is for unplanned queries.
        try:
            selection = _select_connection(conn, settings, body, workspace_key, required_scopes,
                                           required_capabilities, connection_profiles,
                                           connection_runtime)
        except _SelectionFailed as failure:
            selection_failure = failure
        else:
            plan_settings, plan_provider = ((selection.settings, selection.provider)
                                            if selection is not None else (settings, provider))
            if plan_provider is not None:
                on_progress({"stage": "planning"})
                operation = GenerationOperation(conn, replace(
                    plan_settings, operation_max_calls=plan_settings.operation_max_calls + 1,
                    operation_token_budget=(plan_settings.operation_token_budget
                                            + plan_settings.query_planning_token_budget),
                ), uuid4(), workspace_key)
                if on_operation_created is not None:
                    on_operation_created(operation.operation_id)
                plan, plan_warning = _plan_query(
                    conn, plan_settings, plan_provider, selection, operation, config,
                    retrieval_query or body.query, collection_id=collection_id,
                    source_ids=effective_source_ids, workspace_key=workspace_key,
                    check_cancelled=check_cancelled)
                check_cancelled()
                on_progress({"stage": "retrieving"})

    outcome = retrieve(
        conn, collection_id=collection_id, query=retrieval_query or body.query, embedder=embedder,
        embed_model=settings.embedding_model_id, config=config, source_ids=effective_source_ids,
        workspace_key=workspace_key, plan=plan,
        trace_id=operation.operation_id if operation is not None else None,
    )
    conn.commit()
    if operation is not None:
        conn.execute(queries.get("attach_operation_model_calls_to_trace"), {
            "trace_id": outcome.trace.trace_id, "operation_id": operation.operation_id})
        conn.commit()
    if plan_warning:
        outcome.trace.warnings.append("query planning skipped: " + plan_warning)
    check_cancelled()
    kept = _append_warm_citations(
        [] if isinstance(outcome, Refusal) else outcome.chunks, warm_citations)
    if isinstance(outcome, Refusal):
        if not kept:
            persist_response(conn, outcome, status="refusal", reason=outcome.reason)
            _observe_gap(conn, workspace_key, collection_id, body, outcome, "retrieval_refusal")
            return refusal_response(outcome, settings=settings, latency_ms=latency_ms())
        outcome = Retrieved(chunks=kept, trace=outcome.trace)
        outcome.trace.warnings.append(
            "Previously cited passages were kept in the numbered context.")
    elif len(kept) != len(outcome.chunks):
        outcome = replace(outcome, chunks=kept)
        outcome.trace.warnings.append(
            "Previously cited passages were kept in the numbered context.")

    if selection_failure is not None:
        _fail_selection(conn, outcome, selection_failure)
    if not settings.query_planning_enabled:
        try:
            selection = _select_connection(conn, settings, body, workspace_key, required_scopes,
                                           required_capabilities, connection_profiles,
                                           connection_runtime)
        except _SelectionFailed as failure:
            _fail_selection(conn, outcome, failure)
    attempt_selections = []
    if selection is not None:
        settings, provider = selection.settings, selection.provider
        attempt_selections.append(selection)
        if on_profile_selection is not None:
            on_profile_selection(selection)
    if provider is None:
        persist_response(conn, outcome, status="failed", reason="provider_unavailable")
        raise HTTPException(503, detail="selected model connection is unavailable")

    on_progress({"stage": "generating"})
    if operation is None:
        operation = GenerationOperation(conn, settings, outcome.trace.trace_id, workspace_key)
        if on_operation_created is not None:
            on_operation_created(operation.operation_id)

    audit = _AttemptAudit(conn, outcome, operation, settings, selection)
    if settings.tools_enabled:
        answer_provider = _tool_answer_provider(
            conn, body, outcome, operation, settings, provider, selection, audit, check_cancelled,
            on_progress, on_provisional_text, on_provisional_reset, history_context, workspace_key)
    else:
        answer_provider = provider

    def account(result, error):
        audit.record(result, error, operation.last_request)
        operation.account(result, error)

    def prepare(request):
        check_cancelled()
        return operation.prepare(request)

    if system_prompt is not None:
        prompt_version = "debug-override"
    else:
        promoted = PromptRegistry.promoted(conn, ANSWER_PROMPT_NAME)
        conn.commit()
        system_prompt, prompt_version = promoted or (None, PROMPT_VERSION)

    course_subjects = {row["subject"] for row in conn.execute(
        queries.get("list_advising_course_subjects"), {"collection_id": collection_id},
    ).fetchall()}
    conn.commit()

    def generate_answer():
        return answer_from_context(
            outcome.chunks, retrieval_query or body.query, provider=answer_provider,
            max_new_tokens=(settings.hf_max_new_tokens if settings.llm_provider == "hf-local"
                            else settings.llm_max_output_tokens),
            timeout_seconds=settings.llm_timeout_seconds,
            sampling_policy=settings.llm_sampling_policy,
            reasoning_effort=settings.llm_reasoning_effort,
            history_context=history_context,
            include_resource_ids=settings.tools_enabled,
            prepare_request=None if settings.tools_enabled else prepare,
            on_attempt=None if settings.tools_enabled else account,
            on_provisional_text=(on_provisional_text if not settings.tools_enabled else None),
            repair_citations=settings.answer_citation_repair_enabled and not settings.tools_enabled,
            on_provisional_reset=on_provisional_reset,
            before_provider_call=lambda: release_borrow(conn),
            system_prompt=system_prompt,
            prompt_version=prompt_version,
            course_subjects=course_subjects,
        )

    try:
        operation_status = operation.controller.get(operation.conn, operation.operation_id)
        try:
            answer = _answer_with_provider_retry(
                generate_answer,
                remaining_calls=operation_status.remaining_calls,
                remaining_seconds=operation_status.remaining_seconds,
                check_cancelled=check_cancelled,
            )
        except GenerationRejected as first_rejection:
            if first_rejection.recoverable_integrity and on_provisional_reset is not None:
                on_provisional_reset()
            if (not first_rejection.recoverable_integrity or settings.tools_enabled
                    or selection is None or selection.role != "standard"):
                raise
            try:
                fallback = resolve_quality_fallback(
                    settings, conn, body.query, chosen=selection,
                    data_scope="institutional_corpus",
                    required_data_scopes=required_scopes,
                    required_capabilities=required_capabilities,
                    profiles=connection_profiles, runtime=connection_runtime,
                )
            except (ConnectionUnavailable, CredentialUnreadable, MissingCredential):
                raise first_rejection
            if fallback is None:
                raise
            selection = fallback
            attempt_selections.append(fallback)
            settings, provider = fallback.settings, fallback.provider
            answer_provider = provider
            audit.switch_to(settings, selection)
            if on_profile_selection is not None:
                on_profile_selection(fallback)
            answer = generate_answer()
        check_cancelled()
    except GenerationRejected as exc:
        persist_response(conn, outcome, status="refusal", reason=str(exc))
        _observe_gap(conn, workspace_key, collection_id, body, outcome, "generation_withheld")
        _settle_usage(conn, workspace_key, outcome.trace.trace_id, exc.answer, settings)
        trace = answer_response(
            outcome, exc.answer, settings=settings, trace_id=outcome.trace.trace_id,
            latency_ms=latency_ms(),
        ).trace
        trace.warnings.append("generated answer withheld: " + str(exc))
        return RefusalResponse(reason="Unable to produce a validated grounded answer.", trace=trace)
    except OperationBudgetError as exc:
        persist_response(conn, outcome, status="failed", reason=exc.code)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProviderError as exc:
        persist_response(conn, outcome, status="failed", reason=type(exc).__name__)
        if isinstance(exc, RateLimitError):
            retry_after = getattr(exc, "retry_after", None)
            detail = ("the model is rate-limited"
                      + (f"; retry in {round(retry_after)}s" if retry_after else "; retry shortly"))
        else:
            detail = "model connection failed; check provider settings"
        raise HTTPException(status_code=503, detail=detail) from exc
    except (ToolConversationError, ToolLedgerError) as exc:
        persist_response(conn, outcome, status="failed", reason=type(exc).__name__)
        raise HTTPException(status_code=422, detail="native tool execution could not be completed safely") from exc
    on_progress({"stage": "validated"})
    check_cancelled()
    _publish_answer(conn, outcome, operation, attempt_selections, answer)
    _settle_usage(conn, workspace_key, outcome.trace.trace_id, answer, settings)
    _observe_gap(conn, workspace_key, collection_id, body, outcome, "retrieval_success")
    cited_source_ids = sorted({
        UUID(str(item.source_id)) for item in answer.citations if item.source_id
    })
    applicability = None
    if cited_source_ids:
        from app.advising.applicability import evaluate_sources_applicability

        applicability = evaluate_sources_applicability(
            conn,
            cited_source_ids,
            declared_cohort=getattr(body, "studentCohort", None),
            query=body.query,
        )
    return answer_response(
        outcome, answer, settings=settings, trace_id=outcome.trace.trace_id,
        latency_ms=latency_ms(), applicability=applicability,
    )
