"""Research answering entry point shared by HTTP queries and saved turns."""

import time
from dataclasses import replace
from uuid import uuid4

from fastapi import HTTPException

from app.advising.research_repository import ScopeChanged, build_scope, verify_manifest
from app.advising.research_store import ResearchStore, RunClosed
from app.api.schemas import ClaimReviewUnit, ResearchDecisionInfo
from app.api.serialize import answer_response
from app.connections.models import required_data_scopes
from app.connections.selection import StaleProfileSelection
from app.db import queries
from app.db.connection import connector, resolved_database_url
from app.generation.controller import GenerationCancelled, ToolConversationError
from app.generation.evidence import persist_response
from app.generation.operation import GenerationOperation
from app.generation.research_controller import ResearchController
from app.generation.research_final import (
    RESEARCH_PROMPT_VERSION,
    RESEARCH_RENDER_VERSION,
    render_final,
)
from app.generation.research_tools import ResearchSession
from app.jobs.store import JobCancelled, LeaseLost
from app.llm.base import ProviderError
from app.operations.budget import OperationBudgetError
from app.retrieval.trace import normalize_query, write_trace
from app.retrieval.types import RetrievalTrace, Retrieved
from app.tools.executions import ToolExecutionLedger, ToolLedgerError


def research_response(body, conn, settings, config, embedder, provider, *, collection_id,
                      source_ids, source_snapshot, workspace_key, connection_profiles,
                      connection_runtime, check_cancelled, on_progress, on_profile_selection,
                      on_operation_created, history_context, conversation_id,
                      conversation_request_id):
    from app.api.query_service import _AttemptAudit, _select_connection, _SelectionFailed

    started = time.perf_counter()
    try:
        selection = _select_connection(conn, settings, body, workspace_key,
            required_data_scopes(workspace_key), frozenset({"tools"}),
            connection_profiles, connection_runtime)
    except _SelectionFailed as exc:
        raise HTTPException(503, "Research advising has no authorized tools-capable connection.") from exc
    if selection is not None:
        settings, provider = selection.settings, selection.provider
        if on_profile_selection:
            on_profile_selection(selection)
    if provider is None or not callable(getattr(provider, "generate", None)):
        raise HTTPException(503, "Research advising requires an available tools-capable connection.")
    settings = replace(settings, operation_max_calls=settings.research_max_calls,
        operation_timeout_seconds=settings.research_operation_timeout_seconds,
        operation_token_budget=settings.research_operation_token_budget,
        llm_max_output_tokens=min(settings.research_max_output_tokens, settings.llm_max_output_tokens))
    scope = build_scope(conn, collection_id=collection_id, workspace_key=workspace_key,
                        source_ids=source_ids, frozen_snapshot=source_snapshot)
    conn.commit()
    trace_id = write_trace(conn, collection_id=collection_id, query=body.query,
                           config_version=config.version, embed_model=settings.embedding_model_id,
                           refused=False, seams=[], contributions=[])
    conn.commit()
    conn.execute(queries.get("mark_research_trace"), {"trace_id": trace_id})
    conn.commit()
    operation = GenerationOperation(conn, settings, trace_id, workspace_key)
    if on_operation_created:
        on_operation_created(operation.operation_id)
    store = ResearchStore(conn, workspace_key)
    run, created = store.start(scope, run_id=uuid4(), operation_id=operation.operation_id,
        root_trace_id=trace_id, prompt_version=RESEARCH_PROMPT_VERSION,
        config_version=str(config.version), conversation_id=conversation_id,
        request_id=conversation_request_id)
    if not created:
        raise HTTPException(409, "This conversation request already owns a research run; reconnect to it.")
    session = ResearchSession(conn, scope, store, run, settings=settings, config=config, embedder=embedder)
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
            on_attempt=lambda prepared, result, error: audit.record(result, error, prepared))
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
        response = answer_response(outcome, rendered.answer, settings=settings, trace_id=trace_id,
                                   latency_ms=int((time.perf_counter() - started) * 1000))
        response.claims = [ClaimReviewUnit(text=c["text"], citationChunkIds=[
            str(rendered.chunks[n - 1].chunk_id) for n in c["citation_numbers"]]) for c in claims]
        response.research = ResearchDecisionInfo(runId=str(run.run_id), outcome=decision.outcome,
            completeness=decision.completeness, blocks=rendered.blocks,
            unresolvedQuestions=list(decision.unresolved_questions))
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
            raise HTTPException(503, "Research model connection failed; check provider settings.") from exc
        if isinstance(exc, (OperationBudgetError, ToolConversationError, ToolLedgerError)):
            raise HTTPException(422, "Research could not finish safely: " + str(exc)) from exc
        raise
