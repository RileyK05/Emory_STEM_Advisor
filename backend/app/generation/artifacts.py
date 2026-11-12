"""Bounded artifact generation by a checked chain of small provider calls."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.artifacts.models import HtmlArtifactSpec
from app.artifacts.storage import SPEC_TYPES, ArtifactStore
from app.artifacts.visual_review import (
    ScreenshotUnavailable,
    apply_visual_review,
    build_visual_review_request,
    parse_visual_review,
    render_screenshot,
)
from app.auth.capabilities import execution_settings
from app.connections.models import required_data_scopes
from app.connections.selection import resolve
from app.db import queries
from app.db.collections import UnknownCollection, resolve_collection
from app.generation.artifact_pipeline import (
    ArtifactPipeline,
    ArtifactSlot,
    ArtifactTargetError,
    Grounding,
    add_database_slots,
    list_program_titles,
    load_research_grounding,
)
from app.generation.controller import ToolCallLimitExceeded, ToolConversationError
from app.generation.evidence import record_attempt
from app.generation.operation import GenerationOperation
from app.jobs.runtime import StagedArtifact, StagedRunResult
from app.jobs.store import SafeJobError
from app.llm.base import ProviderError, ProviderTimeoutError, provider_failure_detail
from app.operations.budget import OperationBudgetError
from app.retrieval.retrieve import retrieve
from app.retrieval.types import Refusal
from app.tools.executions import ToolLedgerError

logger = logging.getLogger(__name__)

_TOO_SLOW = "This took too long to make. Try again, or ask for a shorter version."


class ArtifactGenerationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1, max_length=20_000)
    # The student's own turn text, before the research model rewrites it as a
    # brief. A plan request must be recognized from these words.
    requestText: str | None = Field(default=None, max_length=20_000)
    sourceIds: list[UUID] | None = Field(default=None, max_length=500)
    collectionId: str | None = Field(default=None, max_length=200)
    mode: Literal["grounded", "authoring"] = "grounded"
    artifactId: UUID | None = None
    expectedRevisionId: UUID | None = None
    conversationId: UUID | None = None
    researchRunId: UUID | None = None
    throughSequence: int | None = Field(
        default=None, ge=0, le=2_147_483_647, strict=True)

    @model_validator(mode="after")
    def revision_target(self):
        if (self.artifactId is None) != (self.expectedRevisionId is None):
            raise ValueError("artifact and expected revision must be provided together")
        if self.throughSequence is not None and self.conversationId is None:
            raise ValueError("a summary boundary requires a conversation")
        return self


def load_artifact_target(conn, settings, workspace_key: str, kind: str, inputs: ArtifactGenerationInput):
    """The collection to read and, when revising, the stored spec of the target revision."""
    collection_id, _ = resolve_collection(conn, settings, inputs.collectionId)
    if inputs.sourceIds is not None:
        sources = conn.execute(queries.get("list_source_fences_for_workspace"),
                               {"collection_id": collection_id, "workspace_key": workspace_key}).fetchall()
        if not set(inputs.sourceIds) <= {s["source_id"] for s in sources}:
            raise ArtifactTargetError("source_not_found", "Selected source was not found.")
    prior = None
    if inputs.artifactId:
        prior_row = ArtifactStore(conn, workspace_key=workspace_key, data_dir=Path(settings.data_dir)).get(
            inputs.artifactId, inputs.expectedRevisionId)
        if prior_row["kind"] != kind:
            raise ArtifactTargetError("invalid_target", "The target artifact has a different type.")
        prior = prior_row["spec"]
    return collection_id, prior


def retrieve_artifact_context(conn, settings, config, embedder, workspace_key: str, collection_id,
                              inputs: ArtifactGenerationInput):
    """Retrieve the passages that ground one direct artifact request: (outcome, chunks)."""
    outcome = retrieve(
        conn, collection_id=collection_id, query=inputs.prompt, embedder=embedder,
        embed_model=settings.embedding_model_id, config=config,
        source_ids=inputs.sourceIds if inputs.mode == "grounded" else [],
        workspace_key=workspace_key,
    )
    conn.commit()
    if inputs.mode == "grounded" and isinstance(outcome, Refusal):
        raise ArtifactTargetError("no_grounding", "No relevant selected sources support this artifact.")
    return outcome, getattr(outcome, "chunks", [])


class ArtifactHandler:
    """Worker handler for one native artifact generation job (document, workbook, or HTML)."""

    def __init__(self, settings, config, embedder, provider, connect: Callable, *, profiles, runtime):
        self.settings = settings
        self.config = config
        self.embedder = embedder
        self.provider = provider
        self.connect = connect
        self.profiles = profiles
        self.runtime = runtime

    def __call__(self, job, context):
        try:
            inputs = ArtifactGenerationInput.model_validate(job.payload["inputs"])
        except ValidationError as exc:
            raise SafeJobError("invalid_run", "Artifact input failed validation.") from exc
        kind = job.payload["kind"]
        context.check_cancelled()
        conn = self.connect()
        try:
            execution_settings(conn, self.settings, job.scope_key)
            conn.commit()
        except HTTPException as exc:
            raise SafeJobError("access_changed", "Workspace access changed; work was stopped.",
                               retryable=False) from exc
        finally:
            conn.close()
        if inputs.conversationId is not None:
            if kind != "document" or inputs.throughSequence is None:
                raise SafeJobError("invalid_summary", "A summary requires a document and saved turn boundary.")
            conn = self.connect()
            try:
                from app.advising.catalog import load_campus_equivalents
                from app.artifacts.advising_summary import build_advising_summary_spec

                # The saved turns can quote a code the Course Atlas lacks (a student
                # asking about a course that does not exist); the export never names it.
                try:
                    collection_id, _ = resolve_collection(conn, self.settings, inputs.collectionId)
                except UnknownCollection:
                    identity = None  # no course data to check a code against
                else:
                    identity = ({row["code"] for row in conn.execute(
                        queries.get("list_collection_course_codes"),
                        {"collection_id": collection_id})},
                        load_campus_equivalents(conn, collection_id=collection_id))
                spec = build_advising_summary_spec(
                    conn,
                    inputs.conversationId,
                    job.scope_key,
                    through_sequence=inputs.throughSequence,
                    title=inputs.prompt if inputs.prompt else None,
                    course_identity=identity,
                )
                conn.commit()
                context.check_cancelled()
                return StagedRunResult((StagedArtifact(
                    kind="document", spec=spec.model_dump(mode="json"),
                    operation="revise" if inputs.artifactId else "create",
                    artifact_id=inputs.artifactId, expected_revision_id=inputs.expectedRevisionId,
                    draft_id=str(job.job_id) if inputs.artifactId else None,
                ),))
            finally:
                conn.close()
        conn = self.connect()
        try:
            return self._generate(conn, job, context, kind, inputs)
        except ArtifactTargetError as exc:
            raise SafeJobError(exc.code, str(exc)) from exc
        except ToolCallLimitExceeded as exc:
            notes = " ".join(getattr(exc, "__notes__", ()))
            logger.warning("artifact job %s exhausted a stage: %s %s",
                           job.job_id, exc, notes)
            if "advisor_stage_outline" in notes:
                raise SafeJobError(
                    "outline_exhausted",
                    "This plan was too complex to outline; try one program or one goal at a time."
                ) from exc
            raise SafeJobError(
                "section_exhausted",
                "This artifact was too complex to finish; try a shorter version."
            ) from exc
        except (ToolConversationError, ToolLedgerError) as exc:
            # Controller and ledger text only: never model output or slot text.
            logger.warning("artifact job %s stopped its tool conversation: %s %s",
                           job.job_id, exc, " ".join(getattr(exc, "__notes__", ())))
            if "did not call the required tool" in str(exc):
                raise SafeJobError(
                    "invalid_output",
                    "The model did not return a complete artifact. Try again or ask for a "
                    "smaller version.") from exc
            raise SafeJobError("tool_failed", "Native tool execution could not be completed safely.") from exc
        except OperationBudgetError as exc:
            if exc.code == "deadline_exceeded":
                raise SafeJobError(exc.code, _TOO_SLOW) from exc
            raise SafeJobError(exc.code, str(exc)) from exc
        except ProviderTimeoutError as exc:
            # A call cut short by the run's deadline is a slow run, not a broken connection.
            raise SafeJobError("deadline_exceeded", _TOO_SLOW) from exc
        except ProviderError as exc:
            raise SafeJobError("provider_failed", provider_failure_detail(exc, "The model")) from exc
        finally:
            conn.close()

    def _generate(self, conn, job, context, kind, inputs):
        settings = self.settings
        collection_id, prior = load_artifact_target(conn, settings, job.scope_key, kind, inputs)
        conn.commit()
        if (prior or {}).get("provenance", {}).get("generation_mode") == "authoring":
            # A revision of a student-authored file stays authoring even when
            # the follow-up turn did not restate where the text came from.
            inputs = inputs.model_copy(update={"mode": "authoring"})
        context.progress({"stage": "retrieving"})
        grounding = self._ground(conn, job, inputs, collection_id)
        context.check_cancelled()
        provider, active, selection = self._select_connection(conn, job, inputs)
        active = replace(active, llm_max_output_tokens=active.artifact_max_output_tokens)
        operation = GenerationOperation(conn, active, job.job_id, job.scope_key)
        pipeline = ArtifactPipeline(
            conn=conn, job=job, context=context, kind=kind, inputs=inputs,
            settings=active, provider=provider, operation=operation, selection=selection,
            collection_id=collection_id, grounding=grounding, prior=prior,
            program_titles=list_program_titles(conn, collection_id),
            connect=self.connect,
        )
        context.progress({"stage": "planning"})
        result = pipeline.run()
        context.check_cancelled()  # Usage is accounted even when cancellation wins.
        self._policy_guard(conn, job, context, active)
        if selection:
            selection.assert_current(conn)
            conn.commit()
        spec = result.spec
        provenance = dict(spec.provenance)
        provenance.update({
            "trace_id": str(grounding.trace_id),
            "model_id": active.llm_model_id or active.hf_model_id,
        })
        if pipeline.fallback_sections:
            provenance["degraded_sections"] = len(pipeline.fallback_sections)
        spec = spec.model_copy(update={"provenance": provenance})
        if (kind == "html" and isinstance(spec, HtmlArtifactSpec)
                and self.settings.visual_artifact_review_enabled):
            spec = self._visual_review(
                conn, job, context, inputs, spec, grounding, prior,
                provider, active, selection, operation,
            )
        context.progress({"stage": "validated"})
        context.check_cancelled()
        staged = StagedArtifact(
            kind=kind, spec=spec.model_dump(mode="json"),
            operation="revise" if inputs.artifactId else "create",
            artifact_id=inputs.artifactId, expected_revision_id=inputs.expectedRevisionId,
            draft_id=str(job.job_id) if inputs.artifactId else None,
            profile_id=selection.profile_id if selection else None,
            profile_revision=selection.profile_revision if selection else None,
            operation_id=operation.operation_id,
        )
        return StagedRunResult((staged,))

    def _ground(self, conn, job, inputs, collection_id) -> Grounding:
        """Numbered source slots: research evidence, else retrieval, plus DB facts."""
        if inputs.researchRunId is not None:
            grounding = load_research_grounding(
                conn, job.scope_key, collection_id, inputs.researchRunId, inputs.sourceIds)
            slots = list(grounding.slots)
            trace_id = grounding.trace_id
        else:
            outcome, chunks = retrieve_artifact_context(
                conn, self.settings, self.config, self.embedder, job.scope_key,
                collection_id, inputs)
            slots = [
                ArtifactSlot(
                    chunk_id=chunk.chunk_id, source_id=chunk.source_id,
                    source_name=chunk.source_name, locator_label=chunk.locator_label,
                    text=chunk.text,
                )
                for chunk in chunks
            ]
            trace_id = outcome.trace.trace_id
            if inputs.mode == "grounded" and not slots:
                raise ArtifactTargetError("no_grounding",
                                          "No relevant selected sources support this artifact.")
        program_titles = list_program_titles(conn, collection_id)
        try:
            slots, _program_title = add_database_slots(
                conn, collection_id=collection_id, workspace_key=job.scope_key,
                source_ids=inputs.sourceIds, slots=slots, prompt=inputs.prompt,
                program_titles=program_titles,
            )
        except Exception:  # noqa: BLE001 - extra facts are additive, never required grounding
            conn.rollback()
        # Checked after the database slots: a program's lines or a course's
        # record ground an artifact even when research stored no passage.
        if not slots and inputs.mode == "grounded" and inputs.researchRunId is not None:
            raise ArtifactTargetError("no_grounding",
                                      "The research run has no evidence to ground this artifact.")
        return Grounding(slots=slots, trace_id=trace_id, collection_id=collection_id)

    def _select_connection(self, conn, job, inputs):
        """(provider, settings, selection): the routed connection, or the configured default."""
        settings = execution_settings(conn, self.settings, job.scope_key)
        if job.scope_key != "development" and not settings.routing_enabled:
            raise SafeJobError("private_routing_required",
                               "Private generation requires an evaluated, authorized connection.")
        provider, active, selection = self.provider, settings, None
        if settings.routing_enabled:
            authoring = inputs.mode == "authoring"
            selection = resolve(
                settings, conn, query=inputs.prompt,
                job_kind="artifact_authoring" if authoring else None,
                data_scope="user_supplied_data" if authoring else "institutional_corpus",
                required_data_scopes=required_data_scopes(job.scope_key, authoring=authoring),
                required_capabilities=frozenset({"tools"}),
                profiles=self.profiles, runtime=self.runtime,
            )
            provider, active = selection.provider, selection.settings
        if provider is None:
            raise SafeJobError("provider_unavailable", "The selected model connection is unavailable.")
        if not active.tools_enabled:
            raise SafeJobError("feature_disabled",
                               "Artifact generation requires a tools-capable connection.",
                               retryable=False)
        return provider, active, selection

    def _policy_guard(self, conn, job, context, active):
        context.check_cancelled()
        current = execution_settings(conn, active, job.scope_key, ceilings=self.settings)
        if active.tools_enabled and not current.tools_enabled:
            raise SafeJobError("feature_disabled", "Tool access changed; output was withheld.",
                               retryable=False)
        conn.commit()
        from app.db.connection import release_borrow

        release_borrow(conn)

    def _visual_review(self, conn, job, context, inputs, spec, grounding, prior,
                       provider, active, selection, operation):
        """One screenshot-backed critique, with an optional validated revision."""
        if selection is None or "vision" not in selection.profile.capabilities:
            return spec
        context.progress({"stage": "visual_review"})
        try:
            image = render_screenshot(spec, active)
        except ScreenshotUnavailable:
            return spec.model_copy(update={"provenance": {
                **spec.provenance, "visual_review": "unavailable",
            }})
        request = build_visual_review_request(spec, image, active)
        try:
            result = self._call_once(conn, job, context, grounding, provider, active,
                                     selection, operation, request)
            review = parse_visual_review(result, image)
        except Exception:  # noqa: BLE001 - visual review is advisory, never publication authority
            return spec.model_copy(update={"provenance": {
                **spec.provenance, "visual_review": "unavailable",
            }})
        if review.revised_html:
            try:
                return apply_visual_review(spec, review)
            except (ValueError, ValidationError):
                return spec.model_copy(update={"provenance": {
                    **spec.provenance, "visual_review": "invalid_revision",
                }})
        status = "issues" if review.issues else "passed"
        return spec.model_copy(update={"provenance": {
            **spec.provenance,
            "visual_review": status,
            "visual_issue_count": len(review.issues),
            "screenshot_sha256": review.screenshot_sha256,
        }})

    def _call_once(self, conn, job, context, grounding, provider, active, selection,
                   operation, request):
        """One audited provider call used only by the advisory visual review."""
        request = operation.prepare(request)
        started = time.perf_counter()
        result = None
        error = None
        try:
            context.check_cancelled()
            conn.commit()
            result = provider.generate(request)
        except Exception as exc:
            error = exc
            raise
        finally:
            record_attempt(conn, grounding.trace_id, active, result, error,
                           int((time.perf_counter() - started) * 1000),
                           operation_id=operation.operation_id, request=request,
                           selection=selection)
            conn.commit()
            operation.account(result, error)
        return result


def build_artifact_handlers(settings, config, embedder, provider, connect: Callable,
                            *, profiles=None, runtime=None):
    if profiles is None or runtime is None:
        from app.runtime import build_connections

        profiles, runtime = build_connections(settings)
    handler = ArtifactHandler(settings, config, embedder, provider, connect,
                              profiles=profiles, runtime=runtime)
    return {kind: handler for kind in SPEC_TYPES}


@dataclass(frozen=True)
class WorkerHandlers:
    """Artifact handlers by kind, plus the executor for saved conversation turns."""

    artifacts: dict
    conversation: object


def build_worker_handlers(settings) -> WorkerHandlers:
    """The job worker's handlers; providers stay behind their factories."""
    from app.conversations.worker import ConversationTurnExecutor
    from app.db.connection import connector
    from app.jobs.store import JobStore
    from app.runtime import build_runtime

    if not settings.database_url:
        raise ValueError("worker requires a resolved database URL")
    # A provider that fails to build settles generation jobs through the safe handler
    # error path instead of disabling the whole worker queue.
    runtime = build_runtime(settings, skip_provider_when_routing=True)
    artifacts = build_artifact_handlers(
        settings, runtime.config, runtime.embedder, runtime.provider, connector(settings),
        profiles=runtime.connection_profiles, runtime=runtime.connection_runtime,
    )
    # Saved conversation turns share this runtime's model and embedder (one copy of
    # large local weights) but borrow from their own pool, so long turns cannot
    # starve job leasing on the control pool.
    conversation = ConversationTurnExecutor(
        settings=settings, config=runtime.config, embedder=runtime.embedder,
        provider=runtime.provider, connection_profiles=runtime.connection_profiles,
        connection_runtime=runtime.connection_runtime,
        connect=connector(settings, pool_name="conversation_execution"),
        job_store=JobStore(connector(settings)),
    )
    return WorkerHandlers(artifacts=artifacts, conversation=conversation)
