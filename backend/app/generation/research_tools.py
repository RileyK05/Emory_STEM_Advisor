"""Progressive, scoped advising reads. Discovery never issues citations."""

import json
import re
from dataclasses import asdict, replace
from typing import Any, Literal
from uuid import uuid5

from pydantic import Field, ValidationError, field_validator

from app.advising.catalog import load_advising_context
from app.advising.comparison import compare_mentions
from app.advising.research_repository import (
    NotInScope,
    read_courses,
    read_document,
    read_program,
    verify_sources,
)
from app.advising.research_store import (
    Acquired,
    EvidenceLimit,
    InvalidResearchRecord,
    StateConflict,
)
from app.advising.research_types import FinalDecision, ResearchState
from app.advising.sequence import CourseRequirement, plan_sequence
from app.atlas.requisites import AllOf, AnyOf, CourseRef
from app.db import queries
from app.generation.advising_tools import CourseCode
from app.generation.research_final import DecisionRejected, validate_final
from app.retrieval.retrieve import retrieve
from app.retrieval.types import CitedChunk
from app.tools.registry import (
    ToolCapability,
    ToolInputError,
    ToolInputModel,
    ToolOutputModel,
    ToolPermissionDenied,
    ToolRegistry,
    ToolScope,
    ToolSpec,
)
from app.util.digest import json_digest

RESEARCH_TOOLS_VERSION = "1"


class SearchInput(ToolInputModel):
    query: str = Field(min_length=1, max_length=300)
    kind: Literal["program", "course", "policy", "career_reference", "any"] = "any"
    department: str | None = Field(default=None, max_length=100)
    cursor: str | None = Field(default=None, max_length=128)


class BrowseInput(ToolInputModel):
    kind: Literal["major", "minor"] | None = None
    department: str | None = Field(default=None, max_length=100)
    cursor: str | None = Field(default=None, max_length=128)


class ReadInput(ToolInputModel):
    handle: str = Field(min_length=1, max_length=128)
    cursor: str | None = Field(default=None, max_length=128)


class CoursesInput(ToolInputModel):
    course_codes: list[CourseCode] = Field(default_factory=list, max_length=4)
    handles: list[str] = Field(default_factory=list, max_length=4)


class CompareInput(ToolInputModel):
    handles: list[str] = Field(min_length=2, max_length=4)


class PreparationInput(ToolInputModel):
    selected_codes: list[CourseCode] = Field(min_length=1, max_length=30)
    completed_codes: list[CourseCode] = Field(default_factory=list, max_length=30)


class StateInput(ToolInputModel):
    expected_revision: int = Field(ge=0)
    state: ResearchState

    @field_validator("state", mode="before")
    @classmethod
    def from_json(cls, value):
        return ResearchState.model_validate_json(json.dumps(value))


class FinishInput(ToolInputModel):
    decision: FinalDecision

    @field_validator("decision", mode="before")
    @classmethod
    def from_json(cls, value):
        return FinalDecision.model_validate_json(json.dumps(value))


class ResearchResult(ToolOutputModel):
    status: Literal["ok", "not_found", "incomplete", "conflict"] = "ok"
    data: dict[str, Any] = Field(default_factory=dict)
    next_cursor: str | None = None
    limitations: list[str] = Field(default_factory=list)


class ResearchRegistry(ToolRegistry):
    """Recoverable, bounded schema feedback without echoing model argument values."""

    def __init__(self, specs, session):
        super().__init__(specs)
        self.session = session

    def invoke(self, tool_id, arguments, scope):
        try:
            return super().invoke(tool_id, arguments, scope)
        except ToolPermissionDenied:
            return ResearchResult(status="not_found")
        except ToolInputError as exc:
            cause = exc.__cause__
            problems = ["Arguments failed schema validation."]
            if isinstance(cause, ValidationError):
                errors = sorted(cause.errors(include_input=False, include_context=False, include_url=False),
                                key=lambda e: len(e["loc"]))
                problems = [".".join(map(str, e["loc"])) + ": " + e["msg"] for e in errors[:8]]
            self.session.validation_hint = "; ".join(problems)[:2000]
            self.session.store.record_action(self.session.run.run_id, action_kind="invalid_tool_arguments",
                                             details={"tool": tool_id, "problems": problems})
            return ResearchResult(status="conflict", limitations=problems)


class ResearchSession:
    def __init__(self, conn, scope, store, run, *, settings, config, embedder):
        self.conn, self.scope, self.store, self.run = conn, scope, store, run
        self.settings, self.config, self.embedder = settings, config, embedder
        self.handles: dict[str, tuple[str, Any]] = {}
        self.cursors: dict[str, tuple[str, int]] = {}
        self.program_reads = {}
        self.program_positions = {}
        self.program_sizes = {}
        self.program_cursors = {}
        self.actions = []
        self.checks: dict[str, dict] = {}
        self.entity_evidence: dict[str, set[str]] = {}
        self.active: list[str] = []
        self.supplied: set[str] = set()
        self.decision = None
        self.execution_id = None
        self.new_evidence = 0
        self.read_count = 0
        self.discovery_count = 0
        self.state = None
        self.validation_hint = ""
        self.remaining_limits = {}

    def tool_scope(self, check_cancelled):
        return ToolScope(self.scope.workspace_key,
                         frozenset({ToolCapability.CORPUS_RESEARCH, ToolCapability.SOURCE_READ}),
                         frozenset(self.handles), check_cancelled)

    def handle(self, kind, identity):
        handle = "h_" + uuid5(self.run.run_id, f"{kind}:{identity}").hex
        if handle not in self.handles:
            self.discovery_count += 1
        self.handles[handle] = (kind, identity)
        return handle

    def resolve(self, handle, kind):
        found = self.handles.get(handle)
        if not found or found[0] != kind:
            raise NotInScope(handle)
        return found[1]

    def offset(self, cursor, key):
        if cursor is None:
            return 0
        found = self.cursors.get(cursor)
        if found is None or found[0] != key:
            raise NotInScope(cursor)
        return found[1]

    def page(self, rows, value, size, tool):
        key = json_digest({"tool": tool, "args": value.model_dump(exclude={"cursor"}),
                           "scope": self.scope.digest})
        offset = self.offset(value.cursor, key)
        next_cursor = None
        if offset + size < len(rows):
            next_cursor = "c_" + uuid5(self.run.run_id, f"{key}:{offset + size}").hex
            self.cursors[next_cursor] = (key, offset + size)
        return rows[offset:offset + size], next_cursor

    def acquire(self, chunks, *, refs=(), complete=True, action="read"):
        before = len(self.store.evidence(self.run.run_id))
        read_bytes = sum(len(c.text.encode()) for c in chunks)
        existing = self.store.evidence(self.run.run_id)
        known = {(e.source_id, e.source_revision, e.chunk_id) for e in existing}
        added = [c for c in chunks if (str(c.source_id), c.source_revision, str(c.chunk_id)) not in known]
        if (len(existing) + len(added) > self.config.research.max_evidence_items
                or sum(len(e.exact_text.encode()) for e in existing)
                + sum(len(c.text.encode()) for c in added) > self.config.research.max_evidence_bytes):
            raise EvidenceLimit("configured evidence register limit reached")
        if read_bytes > 48_000:
            raise EvidenceLimit("read is too large; use a smaller source page")
        result = self.store.acquire(self.run.run_id, [
            Acquired(c, self.scope.sources[c.source_id],
                     tuple(refs.get(c.chunk_id, ())) if isinstance(refs, dict) else tuple(refs),
                     complete) for c in chunks
        ], action_kind=action, summary=action, tool_execution_id=self.execution_id)
        self.active = list(dict.fromkeys([*self.active, *result.evidence_ids]))
        self.new_evidence += len(self.store.evidence(self.run.run_id)) - before
        records = {e.chunk_id: e.evidence_id for e in self.store.evidence(self.run.run_id)}
        for chunk in chunks:
            entities = refs.get(chunk.chunk_id, ()) if isinstance(refs, dict) else refs
            for entity in entities:
                self.entity_evidence.setdefault(entity, set()).add(records[str(chunk.chunk_id)])
        self.read_count += 1
        return list(result.evidence_ids)

    def browse(self, value, _scope):
        from app.advising.research_repository import list_programs

        pins = list_programs(self.scope, kind=value.kind, department=value.department)
        page, cursor = self.page(pins, value, 30, "browse")
        return ResearchResult(data={"programs": [
            {"handle": self.handle("program", p.program_id), "title": p.title,
             "kind": p.kind, "binding_status": p.binding_status} for p in page
        ]}, next_cursor=cursor)

    def search(self, value, _scope):
        sources = [s.source_id for s in self.scope.sources.values() if
                   value.kind not in {"policy", "career_reference"} or s.source_kind == (
                       "institutional_policy" if value.kind == "policy" else "career_reference")]
        self.conn.commit()
        outcome = retrieve(self.conn, collection_id=self.scope.collection_id, query=value.query,
                           config=replace(self.config, fusion=replace(
                               self.config.fusion, cited_set_size=24)), embedder=self.embedder,
                           embed_model=self.settings.embedding_model_id, source_ids=sources,
                           workspace_key=self.scope.workspace_key)
        self.conn.commit()
        chunks = getattr(outcome, "chunks", [])
        leads = []
        if value.kind in {"any", "program"}:
            terms = [word for word in re.findall(r"\w+", value.query) if len(word) > 2]
            patterns = [f"%{word}%" for word in terms]
            found = self.conn.execute(queries.get("research_search_programs"), {
                "collection_id": self.scope.collection_id, "source_ids": sources,
                "patterns": patterns, "query": " OR ".join(terms)}).fetchall()
            ranks = {r["program_id"]: float(r["score"]) for r in found}
            supported = self.conn.execute(queries.get("research_programs_for_chunks"), {
                "chunk_ids": [c.chunk_id for c in chunks]}).fetchall()
            scores = {c.chunk_id: c.normalized_score for c in chunks}
            for row in supported:
                ranks.setdefault(row["program_id"], scores[row["chunk_id"]] * 0.05)
            for pin in sorted(self.scope.programs.values(), key=lambda p: (-ranks.get(p.program_id, -1), p.title)):
                if pin.program_id in ranks and (not value.department or
                        value.department.casefold() in (pin.department or "").casefold()):
                    leads.append({"kind": "program", "_identity": pin.program_id,
                                  "title": pin.title, "source_kind": self.scope.sources[
                                      pin.source_id].source_kind})
        if value.kind in {"any", "course"}:
            locators = [c.locator_id for c in self._lead_chunks(chunks)]
            courses = self.conn.execute(queries.get("research_lead_courses"), {
                "collection_id": self.scope.collection_id, "source_ids": sources,
                "locator_ids": locators}).fetchall()
            seen = set()
            for row in courses:
                if row["canonical_code"] not in seen:
                    seen.add(row["canonical_code"])
                    leads.append({"kind": "course", "_identity": row["code"],
                                  "code": row["code"], "title": row["title"],
                                  "source_kind": self.scope.sources[row["source_id"]].source_kind})
        if value.kind not in {"course", "program"}:
            seen = set()
            for chunk in chunks:
                if chunk.source_id in seen:
                    continue
                seen.add(chunk.source_id)
                pin = self.scope.sources[chunk.source_id]
                leads.append({"kind": "document", "_identity": pin.source_id,
                              "title": pin.filename, "source_kind": pin.source_kind,
                              "snippet": chunk.text[:400], "discovery_only": True})
        self.conn.commit()
        page, cursor = self.page(leads, value, 12, "search")
        for lead in page:
            lead["handle"] = self.handle(lead["kind"], lead.pop("_identity"))
        self.store.record_action(self.run.run_id, action_kind="search", details={
            "query": value.query, "kind": value.kind, "leads": page},
            child_trace_id=outcome.trace.trace_id)
        return ResearchResult(data={"leads": page, "discovery_only": True}, next_cursor=cursor)

    def _lead_chunks(self, chunks):
        from app.advising.research_repository import exact_chunks

        with self.conn.transaction():
            verify_sources(self.conn, self.scope, {c.source_id for c in chunks})
            result = exact_chunks(self.conn, {s: p.index_revision for s, p in self.scope.sources.items()},
                                  [c.chunk_id for c in chunks])
        self.conn.commit()
        return list(result.values())

    def program(self, value, _scope):
        pid = self.resolve(value.handle, "program")
        read = read_program(self.conn, self.scope, pid)
        self.conn.commit()
        items, cursor = self.page(list(read.items), value, 8, "program")
        chunks = {c.chunk_id: c for supported in [*read.fields.values(), *(i.line for i in items)]
                  for c, _start, _end in supported.support}
        ids = self.acquire(list(chunks.values()), refs=(value.handle,), action="read_program")
        records = {e.chunk_id: e.evidence_id for e in self.store.evidence(self.run.run_id)}
        def evidence(supported):
            return list(dict.fromkeys(records[str(c.chunk_id)] for c, _s, _e in supported.support))
        positions = self.program_positions.setdefault(value.handle, set())
        self.program_sizes[value.handle] = len(read.items)
        self.program_cursors[value.handle] = cursor
        positions.update(i.position for i in items)
        self.program_reads[value.handle] = replace(read, items=tuple(
            i for i in read.items if i.position in positions))
        return ResearchResult(data={"title": read.pin.title, "handle": value.handle,
            "fields": {k: {"text": v.text, "evidence_ids": evidence(v)} for k, v in read.fields.items()},
            "sections": list(dict.fromkeys(i.section for i in read.items)),
            "items": [{"position": i.position, "text": i.line.text, "choose": i.choose,
                       "mentioned_codes": list(i.mentioned_course_codes),
                       "evidence_ids": evidence(i.line), "discovery_only": not i.line.cited} for i in items],
            "evidence_ids": ids}, next_cursor=cursor,
            limitations=["Unread requirement items remain."] if cursor else [])

    def courses(self, value, _scope):
        codes = list(dict.fromkeys([*value.course_codes,
                                  *(self.resolve(h, "course") for h in value.handles)]))
        if not 1 <= len(codes) <= 4:
            return ResearchResult(status="conflict", limitations=["Read one to four course codes."])
        reads = read_courses(self.conn, self.scope, codes)
        self.conn.commit()
        chunks = list({c.chunk_id: c for r in reads for c in r.chunks}.values())
        ids = self.acquire(chunks, refs={c.chunk_id: (r.code, r.canonical_code)
                                        for r in reads for c in r.chunks}, action="read_courses")
        return ResearchResult(data={"courses": [
            {"code": r.code, "title": r.title, "requisites_raw": r.requisites_raw,
             "parse_status": r.parse_status, "canonical_code": r.canonical_code} for r in reads],
            "evidence_ids": ids, "missing_codes": sorted(set(codes) - {r.code for r in reads})},
            status="ok" if reads else "not_found")

    def document(self, value, _scope):
        kind, identity = self.handles.get(value.handle, (None, None))
        if kind not in {"document", "section"}:
            raise NotInScope(value.handle)
        source, start = (identity, 0) if kind == "document" else identity
        key = "document:" + value.handle
        offset = self.offset(value.cursor, key) if value.cursor else start
        page = read_document(self.conn, self.scope, source, from_chunk=offset)
        self.conn.commit()
        ids = self.acquire(page.chunks, complete=not page.incomplete, action="read_document")
        cursor = None
        if page.next_chunk is not None:
            cursor = "c_" + uuid5(self.run.run_id, f"{key}:{page.next_chunk}").hex
            self.cursors[cursor] = (key, page.next_chunk)
        return ResearchResult(status="incomplete" if page.incomplete else "ok", data={
            "source_kind": page.source.source_kind, "evidence_ids": ids,
            "sections": [{"handle": self.handle("section", (source, s.first_chunk)),
                          "title": s.label, "chunk_count": s.chunk_count} for s in page.sections[:80]]},
            next_cursor=cursor, limitations=list(page.limitations))

    def compare(self, value, _scope):
        if any(h not in self.program_reads for h in value.handles):
            return ResearchResult(status="conflict", limitations=["Read each program first."])
        result = compare_mentions([self.program_reads[h] for h in value.handles])
        data = asdict(result)
        data["shared"] = {code: refs[:4] for code, refs in list(data["shared"].items())[:12]}
        data["unique"] = {str(pid): {code: refs[:2] for code, refs in list(codes.items())[:12]}
                          for pid, codes in data["unique"].items()}
        data["unresolved_positions"] = {str(pid): positions for pid, positions
                                        in data["unresolved_positions"].items()}
        incomplete = any(len(self.program_positions[h]) < len(read_program(
            self.conn, self.scope, self.resolve(h, "program")).items) for h in value.handles)
        self.conn.commit()
        incomplete = incomplete or len(result.shared) > 12 or any(
            len(codes) > 12 for codes in result.unique.values())
        # Remove whole comparison entries, never shorten a restriction into a
        # stronger claim. The read evidence remains available for a narrower comparison.
        while len(json.dumps(data, default=str).encode()) > 24_000:
            if data["shared"]:
                data["shared"].pop(next(reversed(data["shared"])))
            else:
                remaining = next((v for v in data["unique"].values() if v), None)
                if remaining is None:
                    break
                remaining.pop(next(reversed(remaining)))
            incomplete = True
        return ResearchResult(status="incomplete" if incomplete else "ok", data={
            "comparison": json.loads(json.dumps(data, default=str)),
            "shared_mention_count": len(result.shared)},
            limitations=["Compares read requirement items only; shared mentions establish no credit savings."]
            + (["Additional comparison entries were omitted to fit the page."] if incomplete else []))

    def preparation(self, value, _scope):
        pending = list(value.selected_codes)
        visited, all_chunks, refs = set(), {}, {}
        context = {"courses": []}
        while pending and len(visited) < 30:
            count = min(4, 30 - len(visited))
            codes, pending = pending[:count], pending[count:]
            visited.update(codes)
            reads = read_courses(self.conn, self.scope, codes)
            for read in reads:
                all_chunks.update({c.chunk_id: c for c in read.chunks})
                refs.update({c.chunk_id: (read.code, read.canonical_code) for c in read.chunks})
            cited = [CitedChunk(c.chunk_id, c.source_id, c.chunk_index,
                               self.scope.sources[c.source_id].filename, c.locator_label,
                               c.text, (), 0, 0, i + 1) for i, c in enumerate(all_chunks.values())]
            context = load_advising_context(self.conn, collection_id=self.scope.collection_id,
                                           chunks=cited)
            self.conn.commit()
            def deps(node):
                if isinstance(node, CourseRef):
                    return [node.code]
                if isinstance(node, (AllOf, AnyOf)):
                    return [c for child in node.items for c in deps(child)]
                return []
            for course in context["courses"]:
                for code in [*deps(course["prerequisites"]), *deps(course["corequisites"])]:
                    if code not in visited and code not in pending and code not in value.completed_codes:
                        pending.append(code)
        ids = self.acquire(list(all_chunks.values()), refs=refs, action="check_preparation")
        courses = {c["code"]: CourseRequirement(c["code"], c["title"],
                   c["parse_status"] if c["requirements_evidence_complete"] else "unparsed",
                   c["prerequisites"], c["corequisites"], c["requisites_raw"])
                   for c in context["courses"]}
        plan = plan_sequence(courses, list(dict.fromkeys([*value.selected_codes, *sorted(visited)])),
                             value.completed_codes)
        check_id = "p_" + json_digest(value.model_dump())[:24]
        result = {"check_id": check_id, "selected_codes": value.selected_codes,
                  "completed_codes": value.completed_codes, "stages": list(plan.stages),
                  "blockers": [asdict(b) for b in plan.blockers], "complete": plan.complete and not pending,
                  "conclusion": "unresolved" if pending or not plan.complete else "dependency_order",
                  "evidence_ids": ids, "unexplored_codes": pending,
                  "enrollment_eligibility_verified": False, "scope": "prerequisite_order_only"}
        if plan.blockers and not pending:
            result["conclusion"] = "blocked"
        self.checks[check_id] = result
        self.store.record_action(self.run.run_id, action_kind="preparation_check", details=result)
        return ResearchResult(status="ok" if result["complete"] else "incomplete", data=result)

    def update(self, value, _scope):
        state = value.state
        if len(state.candidates) > self.config.research.max_candidates:
            raise InvalidResearchRecord(["configured candidate limit exceeded"])
        if len(state.model_dump_json().encode()) > self.config.research.max_state_bytes:
            raise InvalidResearchRecord(["configured state limit exceeded"])
        if len({c.id for c in state.candidates}) != len(state.candidates):
            raise InvalidResearchRecord(["candidate IDs must be unique"])
        records = {e.evidence_id: e for e in self.store.evidence(self.run.run_id)}
        if any(criterion.basis == "career_evidence" and not any(
                records[eid].source_kind == "career_reference" for eid in criterion.evidence_ids
                if eid in records) for criterion in state.criteria):
            raise InvalidResearchRecord(["career_evidence criteria need a read career reference; otherwise label an assumption"])
        for candidate in state.candidates:
            for h in candidate.program_handles:
                self.resolve(h, "program")
        self.state = self.store.update_state(self.run.run_id, state,
                                             expected_revision=value.expected_revision)
        self.active = list(self.state.active_evidence_ids)
        return ResearchResult(data={"state_revision": self.state.revision})

    def finish(self, value, _scope):
        validate_final(value.decision, self.state, self.store.evidence(self.run.run_id),
                       self.supplied, self.checks, self.entity_evidence)
        self.decision = value.decision
        return ResearchResult(data={"accepted": True})

    def registry(self):
        def safe(handler):
            def invoke(value, scope):
                try:
                    result = handler(value, scope)
                    self.actions.append({"tool": "advisor_" + handler.__name__,
                        "arguments": value.model_dump(mode="json", exclude={"state", "decision"}),
                        "status": result.status, "next_cursor": result.next_cursor,
                        "evidence_ids": result.data.get("evidence_ids", []),
                        "limitations": result.limitations})
                    return result
                except NotInScope:
                    return ResearchResult(status="not_found")
                except (InvalidResearchRecord, StateConflict, EvidenceLimit, DecisionRejected) as exc:
                    return ResearchResult(status="conflict", limitations=[str(exc)])
            return invoke
        entries = [
            ("search", "Discover leads; results are not evidence until read.", SearchInput, self.search, ()),
            ("list_programs", "Browse exact majors and minors in scope.", BrowseInput, self.browse, ()),
            ("read_program", "Read up to eight raw requirements and acquire exact evidence; follow cursor.", ReadInput, self.program, ("handle",)),
            ("read_courses", "Read one to four exact course records, descriptions and restrictions.", CoursesInput, self.courses, ("handles",)),
            ("read_document", "Read source or section evidence; follow cursor for remaining chunks.", ReadInput, self.document, ("handle",)),
            ("compare_mentions", "Compare already read programs; overlap is mentions, never credit savings.", CompareInput, self.compare, ("handles",)),
            ("check_preparation", "Run deterministic dependency checks on asserted completed courses, never eligibility.", PreparationInput, self.preparation, ()),
            ("update_research", "Replace bounded comparison state, set active exact evidence and expected revision.", StateInput, self.update, ()),
            ("finish", "Propose typed final answer. Must be the sole tool call. Cite active evidence IDs, never raw markers.", FinishInput, self.finish, ()),
        ]
        return ResearchRegistry([ToolSpec("advisor_" + name, description, model, ResearchResult,
            ToolCapability.SOURCE_READ if resources else ToolCapability.CORPUS_RESEARCH,
            safe(handler), resources, max_output_bytes=64_000)
            for name, description, model, handler, resources in entries], self)
