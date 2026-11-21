"""Chain small artifact calls, checking each one in code.

The model never decides what step comes next: grounding, outline, sections,
assembly, deterministic checks and targeted repair run in a fixed order. Every
model call is one tool conversation with a single terminal tool, so a refused
stage comes back to the model in the same conversation."""

from __future__ import annotations

import re
import time
from uuid import UUID

from app.advising.catalog import (
    load_campus_equivalents,
    load_catalog_code_aliases,
    load_program_quoted_codes,
)
from app.advising.research_repository import (
    ResearchScope,
    ScopeChanged,
    build_scope,
)
from app.advising.sequence import CourseRequirement, SequencePlan
from app.db import queries
from app.generation.artifact_templates import choose_template
from app.generation.controller import (
    ToolCallLimitExceeded,
    ToolConversationController,
    ToolConversationError,
)
from app.generation.evidence import record_attempt
from app.generation.native_tools import (
    StageEdits,
    StageOutline,
    StageSection,
    stage_scope,
    stage_spec,
)
from app.generation.pipeline.assembly import AssemblyStages
from app.generation.pipeline.outline import OutlineStages
from app.generation.pipeline.plans import PlanStages
from app.generation.pipeline.revisions import RevisionStages
from app.generation.pipeline.sections import SectionStages
from app.generation.pipeline.shared import (
    _OUTPUT_LIMIT_CONTINUATIONS,
    _TRANSIENT_RETRIES,
    ArtifactSlot,
    ArtifactTargetError,
    AssembledSection,
    Grounding,
    PipelineResult,
    _Lane,
    _slot_from_chunk,
    _StageProvider,
    add_database_slots,
    list_program_titles,
    load_research_grounding,
)
from app.generation.pipeline.templates import TemplateStages
from app.generation.student_request import (
    CHECKLIST_REQUEST,
    TERM_PLAN_REQUEST,
    WHATIF_REQUEST,
    builder_for,
)
from app.llm.base import GenerationRequest
from app.tools.executions import ToolExecutionLedger
from app.tools.registry import ToolRegistry
from app.util.digest import json_digest

__all__ = ["ArtifactPipeline", "ArtifactSlot", "ArtifactTargetError", "Grounding", "add_database_slots", "list_program_titles", "load_research_grounding"]


class ArtifactPipeline(OutlineStages, PlanStages, TemplateStages, RevisionStages, SectionStages, AssemblyStages):
    """Deterministic artifact pipeline for one leased artifact run."""

    def __init__(
        self,
        *,
        conn,
        job,
        context,
        kind: str,
        inputs,
        settings,
        provider,
        operation,
        selection,
        collection_id: UUID,
        grounding: Grounding,
        prior: dict | None,
        program_titles: list[str],
        connect=None,
    ):
        self.conn = conn
        # Parallel sections need fresh connections; without a factory they run in order.
        self._can_parallelize = connect is not None
        self.connect = connect or (lambda: conn)
        self.job = job
        self.context = context
        self.kind = kind
        self.inputs = inputs
        self.settings = settings
        self.provider = provider
        self.operation = operation
        self.selection = selection
        self.collection_id = collection_id
        self.grounding = grounding
        self.prior = prior
        self.program_titles = program_titles
        brief = inputs.prompt.partition("\n\nPublished answer:\n")[0]
        self.brief = brief
        # The research model rewrites the student's request as a brief, and can
        # turn "plan" into "checklist". The student's own words decide.
        request_text = (inputs.requestText or brief).strip() or brief
        self.request_text = request_text
        # Only the student's own words say a course is done. The brief and the
        # published answer can assume courses to fit a term count (2026-11-19).
        self.student_text = (inputs.studentText or request_text).strip() or request_text
        self.total_requested = (kind == "workbook"
                                and bool(re.search(r"\btotals?\b", request_text, re.IGNORECASE)))
        self.plan_requested = (kind in {"document", "workbook"} and inputs.mode == "grounded"
                               and not CHECKLIST_REQUEST.search(request_text)
                               and bool(TERM_PLAN_REQUEST.search(request_text)))
        # A major-switch question gets a chart and line tables built from the
        # programs' own lines; the model only names the programs and courses.
        self.whatif_requested = (kind == "document" and inputs.mode == "grounded"
                                 and not self.plan_requested
                                 and bool(WHATIF_REQUEST.search(request_text)))
        # A weak model should fill a known structure, not invent one. The
        # template also becomes the outline if the model never produces one.
        self.template = (choose_template(kind, request_text)
                         if inputs.mode == "grounded" and not self.plan_requested
                         and not self.whatif_requested else None)
        # Code fills a template from the program's records when it can, so the
        # model's part shrinks to naming the program and picking sentences.
        self.builder = (builder_for(kind, self.template.id if self.template else None)
                        if inputs.mode == "grounded" and not self.plan_requested
                        and not self.whatif_requested else None)
        self.built_programs: list[str] = []
        self.template_fallback = False
        self.trace_id = grounding.trace_id
        # The tool ledger binds the whole run to one scope fingerprint. It is taken from
        # the grounding as it starts: the planner later appends course records it read,
        # and a fingerprint that followed them would refuse every stage after the outline.
        self._stage_fingerprint = json_digest({
            "version": 2, "kind": kind,
            "slots": [str(slot.chunk_id) for slot in grounding.slots],
            "provider": settings.llm_provider,
            "model": settings.llm_model_id,
            "sampling_policy": settings.llm_sampling_policy,
            "reasoning_effort": settings.llm_reasoning_effort,
            "mode": inputs.mode,
            "profile_id": str(selection.profile_id) if selection else None,
            "profile_revision": selection.profile_revision if selection else None,
        })
        self.outline: StageOutline | None = None
        self.outline_fallback = False
        self.edits: StageEdits | None = None
        self.plan: SequencePlan | None = None
        self.plan_records: dict[str, int] = {}
        self.plan_credits: dict[str, float] = {}
        # Only credits a record states: what a student is shown, never the packer's default.
        self.plan_stated_credits: dict[str, float] = {}
        self.plan_seasons: dict[str, frozenset[str]] = {}
        self.plan_requirements: dict[str, CourseRequirement] = {}
        self.plan_layout: tuple[list[list[str]], list[str]] | None = None
        self.plan_notes: list[str] = []
        self.whatif_programs: list[str] = []
        self.program_title: str | None = None
        self.program_allowed: set[str] | None = None
        self.extra_courses: set[str] = set()
        # A revision keeps the base artifact's plan context: K5 and K6 must still
        # apply to the revised page, and a workbook keeps its charts.
        self._prior_plan = False
        self._prior_completed: list[str] = []
        self.known_courses: set[str] = set()
        self.campus_equivalents: dict[str, frozenset[str]] = {}
        self.catalog_aliases: dict[str, frozenset[str]] = {}
        self.quoted_codes: frozenset[str] = frozenset()
        self.section_payloads: dict[str, StageSection] = {}
        self.kept: dict[str, AssembledSection] = {}
        self.section_texts: dict[str, str] = {}
        self.fallback_sections: set[str] = set()
        self._main_lane = _Lane(conn, operation, time.perf_counter())
        self._provider_calls = 0

    def run(self) -> PipelineResult:
        self._load_catalog_facts()
        if self.prior is not None:
            self._run_edits()
            sections = self._current_sections()
            to_write = [section for section in sections
                        if self._section_id(section) not in self.kept]
        else:
            self.context.progress({"stage": "outlining"})
            built = bool(self.builder) and self._build_from_request()
            # A plan for one plainly named program, with no courses named, is
            # entirely the program's required lines and the term packer: no
            # outline call is needed to choose them.
            built = built or (self.plan_requested and self._plan_needs_no_outline()
                              and self._fallback_plan_outline())
            try:
                if not built:
                    self._run_outline()
            except (ToolCallLimitExceeded, ToolConversationError):
                # A weak model can fail to outline a plan it cannot invent. The
                # program's required lines and the term packer are deterministic,
                # so build the plan without it. Other common artifacts fall back
                # to their base template.
                if self.plan_requested:
                    if not self._fallback_plan_outline():
                        raise
                elif self.whatif_requested:
                    if not self._fallback_whatif_outline():
                        raise
                elif self.template is None or not self._apply_template_outline():
                    raise
            if self.builder and self.kept:
                self.context.progress({"stage": "writing", "unit": "catalog"})
                self._pick_catalog()
            sections = self._current_sections()
            # A section the server built (a what-if comparison) needs no writer.
            to_write = [section for section in sections
                        if self._section_id(section) not in self.kept]
        self._write_sections(to_write)
        return self._assemble_and_check()

    def _load_catalog_facts(self):
        rows = self.conn.execute(
            queries.get("list_collection_course_codes"),
            {"collection_id": self.collection_id},
        ).fetchall()
        self.known_courses = {row["code"] for row in rows}
        self.campus_equivalents = load_campus_equivalents(
            self.conn, collection_id=self.collection_id)
        self.catalog_aliases = load_catalog_code_aliases(
            self.conn, collection_id=self.collection_id)
        self.quoted_codes = load_program_quoted_codes(
            self.conn, collection_id=self.collection_id)
        self.conn.commit()

    def _research_scope(self) -> ResearchScope | None:
        if self.inputs.mode != "grounded":
            return None
        # One run reads one scope: building it lists every source, so it is
        # built once. Reads still verify each source revision they touch.
        if getattr(self, "_scope", None) is not None:
            return self._scope
        try:
            scope = build_scope(
                self.conn, collection_id=self.collection_id,
                workspace_key=self.job.scope_key,
                source_ids=self.inputs.sourceIds,
            )
            self.conn.commit()
            self._scope = scope
            return scope
        except ScopeChanged as exc:
            raise ArtifactTargetError("stale_source", "A source changed; the artifact was withheld.") from exc

    def _slot_for_chunk(self, chunk, scope) -> int:
        for index, slot in enumerate(self.grounding.slots, 1):
            if slot.chunk_id == chunk.chunk_id and slot.text == chunk.text:
                return index
        self.grounding.slots.append(_slot_from_chunk(chunk, scope))
        return len(self.grounding.slots)

    def _course_exists(self, code: str) -> bool:
        if code in self.known_courses:
            return True
        return bool(self.campus_equivalents.get(code, frozenset()) & self.known_courses)

    def _run_stage(self, tool_id: str, description: str, input_model, handler,
                   messages, *, stage_key: str = "", lane: _Lane | None = None,
                   effort: str | None = None, max_output_tokens: int | None = None):
        lane = lane or self._main_lane
        attempts = self.settings.artifact_repair_attempts
        spec = stage_spec(tool_id, description, input_model, handler)
        registry = ToolRegistry((spec,))
        scope = stage_scope(self.job.scope_key, self.context.check_cancelled)
        fingerprint = self._stage_fingerprint
        ledger = ToolExecutionLedger(self.connect,
                                     max_tool_calls=self.settings.artifact_operation_max_calls)

        def audit(prepared, call_result, call_error):
            record_attempt(
                lane.conn, self.trace_id, self.settings, call_result, call_error,
                int((time.perf_counter() - lane.started) * 1000),
                operation_id=lane.operation.operation_id, request=prepared,
                selection=self.selection,
            )
            lane.conn.commit()
            lane.started = time.perf_counter()

        configured = self.settings.llm_reasoning_effort
        request = GenerationRequest(
            messages,
            max_output_tokens=min(max_output_tokens or self.settings.artifact_max_output_tokens,
                                  self.settings.artifact_max_output_tokens),
            timeout_seconds=self.settings.llm_timeout_seconds,
            temperature=0 if self.settings.llm_sampling_policy == "greedy" else None,
            # A small choosing stage needs little deliberation; never raise the
            # configured effort, and leave a model without effort control alone.
            reasoning_effort=(effort if effort and configured not in (None, "none")
                              else configured),
            response_schema=None,
            # Every call of one stage kind shares its system prompt.
            cache_key="artifact-" + json_digest(messages[0])[:24] if messages else None,
        )
        provider = _StageProvider(self.provider, f"{tool_id}:{stage_key}")
        try:
            return ToolConversationController(
                max_tool_calls=attempts + 1,
                max_provider_calls=(attempts + 1 + _OUTPUT_LIMIT_CONTINUATIONS
                                    + _TRANSIENT_RETRIES),
                max_output_continuations=_OUTPUT_LIMIT_CONTINUATIONS,
                max_repair_calls=attempts + 1,
                max_transient_retries=_TRANSIENT_RETRIES,
            ).run(
                provider, request, registry=registry, scope=scope, operation=lane.operation,
                ledger=ledger, operation_id=lane.operation.operation_id,
                workspace_key=self.job.scope_key, scope_fingerprint=fingerprint,
                terminal_tool_names=frozenset({tool_id}),
                terminal_repair_model=input_model,
                cancellation=lambda: self._guard(lane),
                before_provider_call=lambda: self._before_provider_call(lane),
                on_attempt=audit,
            )
        except ToolConversationError as exc:
            # The worker reports only "tool_failed"; name the stage so a run that
            # ended without a stored refusal can still be traced.
            exc.add_note(f"stage {tool_id}:{stage_key}")
            raise

    def _guard(self, lane: _Lane):
        self.context.check_cancelled()
        lane.operation.assert_publishable()
        lane.conn.commit()

    def _before_provider_call(self, lane: _Lane):
        self.context.check_cancelled()
        lane.conn.commit()
        from app.db.connection import release_borrow

        release_borrow(lane.conn)
        lane.started = time.perf_counter()
