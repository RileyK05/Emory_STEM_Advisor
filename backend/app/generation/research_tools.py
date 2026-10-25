"""Progressive, scoped advising reads. Discovery never issues citations."""

import json
import re
from dataclasses import asdict, replace
from itertools import zip_longest
from typing import Any, Literal
from uuid import uuid5

from pydantic import Field, ValidationError, field_validator

from app.advising.catalog import load_advising_context
from app.advising.comparison import compare_mentions
from app.advising.research_repository import (
    NotInScope,
    exact_chunks,
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
from app.advising.research_workspace import ConversationWorkspace
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

RESEARCH_TOOLS_VERSION = "8"


class SearchInput(ToolInputModel):
    query: str = Field(min_length=1, max_length=300)
    kind: Literal["program", "course", "document", "policy", "career_reference", "any"] = "any"
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
    handles: list[str] = Field(default_factory=list, max_length=4,
        description="Only course handles from this run. For earlier-memory handles, use advisor_recall with handles only; exact course codes can be passed in course_codes without handles.")


class CompareInput(ToolInputModel):
    handles: list[str] = Field(min_length=2, max_length=4)


class PreparationInput(ToolInputModel):
    selected_codes: list[CourseCode] = Field(min_length=1, max_length=30)
    completed_codes: list[CourseCode] = Field(default_factory=list, max_length=30)


class RecallInput(ToolInputModel):
    query: str = Field(default="", max_length=300,
        description="Search mode only: omit handles. New search when cursor is absent; a cursor preserves its original query.")
    handles: list[str] = Field(default_factory=list, max_length=4,
        description="Reopen memory handles only: supply handles alone and omit query and cursor. Do not pass memory handles to course or source readers.")
    cursor: str | None = Field(default=None, max_length=128,
        description="Continue a prior recall search using its original query, even if query is reworded.")


class ConversationInput(ToolInputModel):
    query: str = Field(default="", max_length=300,
        description="New search when cursor is absent; a cursor preserves its original query.")
    cursor: str | None = Field(default=None, max_length=128,
        description="Continue the original query's next page, including within a long turn. Omit cursor to start a different search.")


def _coerce_embedded_json(value, model, *, unwrap_key: str):
    """Validate a tool argument that may arrive as a JSON-encoded string (EMO-006).

    Some OpenAI-compatible providers (MiMo) send an object field as a JSON string
    and occasionally wrap it once more as ``{"<unwrap_key>": {...}}``. Accept all
    three shapes so a valid decision is not rejected for serialization style.
    """
    if isinstance(value, str):
        value = json.loads(value)
    if isinstance(value, dict) and set(value) == {unwrap_key}:
        value = value[unwrap_key]
        if isinstance(value, str):
            value = json.loads(value)
    # Route through JSON parsing: the strict models require tuple fields, and
    # strict JSON input accepts an array for a tuple where `model_validate`
    # would not.
    return model.model_validate_json(json.dumps(value))


class StateInput(ToolInputModel):
    # A provider may omit the optimistic revision; only the model writes its own
    # run state, so the current revision is what it last saw. Default to it rather
    # than rejecting the update and stranding its candidates.
    expected_revision: int | None = Field(default=None, ge=0)
    state: ResearchState

    @field_validator("state", mode="before")
    @classmethod
    def from_json(cls, value):
        return _coerce_embedded_json(value, ResearchState, unwrap_key="state")


class FinishInput(ToolInputModel):
    decision: FinalDecision

    @field_validator("decision", mode="before")
    @classmethod
    def from_json(cls, value):
        return _coerce_embedded_json(value, FinalDecision, unwrap_key="decision")


class ResearchResult(ToolOutputModel):
    status: Literal["ok", "not_found", "incomplete", "conflict"] = "ok"
    data: dict[str, Any] = Field(default_factory=dict)
    next_cursor: str | None = None
    limitations: list[str] = Field(default_factory=list)


def _discovery_words(text):
    return set(re.findall(r"[\w]+", text.casefold())) - {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "i", "in",
        "is", "it", "of", "on", "or", "that", "the", "this", "to", "what", "which", "with",
    }


def _query_snippet(text: str, query: str, limit: int = 400) -> str:
    """Show the query-dense part of a long passage, not always its opening."""
    words = {word for word in re.findall(r"\w+", query.casefold()) if len(word) > 2}
    if not words or len(text) <= limit:
        return text[:limit]
    lower = text.casefold()
    best_start, best_score = 0, -1
    for start in range(0, len(text) - limit + 1, 80):
        window = lower[start:start + limit]
        score = sum(1 for word in words if word in window)
        if score > best_score:
            best_start, best_score = start, score
    snippet = text[best_start:best_start + limit]
    return (("…" if best_start else "") + snippet
            + ("…" if best_start + limit < len(text) else ""))


def _document_matches(sources, query):
    """Rank scoped metadata as discovery leads; matches establish no facts."""
    words = _discovery_words(query)
    ranked = []
    for pin in sources:
        title = _discovery_words(pin.title or "")
        labels = _discovery_words(" ".join((pin.filename, pin.publisher or "",
            pin.institution or "", pin.issuing_unit or "", pin.edition or "",
            *pin.document_roles, *(label for values in pin.applicability.values()
                                  for label in values))))
        score = 3 * len(words & title) + len(words & labels)
        if score:
            ranked.append((-score, pin.filename, str(pin.source_id), pin))
    return [entry[-1] for entry in sorted(ranked)]


def _source_metadata(pin, *, compact=False):
    """Verified discovery metadata, including explicit unknown applicability."""
    metadata = {
        "source_kind": pin.source_kind, "filename": pin.filename, "source_title": pin.title,
        "publisher": pin.publisher, "institution": pin.institution, "issuing_unit": pin.issuing_unit,
        "source_url": pin.source_url, "edition": pin.edition, "published_at": pin.published_at,
        "acquired_at": pin.acquired_at, "geography": pin.geography, "occupation_id": pin.occupation_id,
        "document_roles": list(pin.document_roles),
        "applicability": {key: list(values) for key, values in pin.applicability.items()},
        "applicability_status": pin.applicability_status, "metadata_revision": pin.metadata_revision,
    }
    if not compact:
        return metadata
    omitted = set()
    for name, limit in {"filename": 180, "source_title": 180, "publisher": 180,
                        "institution": 120, "issuing_unit": 120, "source_url": 240,
                        "edition": 120, "geography": 120, "occupation_id": 100}.items():
        value = metadata[name]
        if value is not None and len(value.encode("utf-8")) > limit:
            metadata[name] = value.encode("utf-8")[:limit - 3].decode("utf-8", errors="ignore") + "…"
            omitted.add(name)
    # Keep whole short labels: cutting a restriction midway could change its meaning.
    roles = [role for role in metadata["document_roles"]
             if len(role.encode("utf-8")) <= 120][:3]
    if roles != metadata["document_roles"]:
        omitted.add("document_roles")
    metadata["document_roles"] = roles
    short_labels = {dimension: [label for label in labels if len(label.encode("utf-8")) <= 120][:2]
                    for dimension, labels in metadata["applicability"].items()}
    applicability = {dimension: labels for dimension, labels in short_labels.items() if labels}
    applicability = dict(list(applicability.items())[:3])
    if applicability != metadata["applicability"]:
        omitted.add("applicability")
    metadata["applicability"] = applicability
    # 30 browse leads must fit the tool's 64 KiB envelope, including their handles.
    secondary = iter(("source_url", "source_title", "issuing_unit", "institution",
                      "applicability", "document_roles", "published_at", "acquired_at",
                      "geography", "occupation_id"))
    while True:
        metadata["metadata_truncated"] = bool(omitted)
        metadata["metadata_omitted_fields"] = sorted(omitted)
        if omitted:
            metadata["metadata_note"] = "Read this handle for complete source metadata."
        if len(json.dumps(metadata, ensure_ascii=False).encode("utf-8")) <= 1536:
            return metadata
        name = next(secondary)
        metadata.pop(name)
        omitted.add(name)


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
        self.cursors: dict[str, tuple[str, Any]] = {}
        self.program_reads = {}
        self.program_positions = {}
        self.program_sizes = {}
        self.program_cursors = {}
        self.pending_policy_read: str | None = None
        self.actions = []
        self.checks: dict[str, dict] = {}
        self.unavailable_preparation_check_ids: set[str] = set()
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
        self.workspace = None
        self.prior_workspace = None
        self.recall_page = None
        self.conversation_page = None

    def open_workspace(self, conversation_id):
        if conversation_id is None:
            return
        self.workspace = ConversationWorkspace(self.conn, self.scope, self.store, conversation_id)
        self.prior_workspace = self.workspace.checkpoint(
            self.handle, max_bytes=self.config.research.max_state_bytes)
        self.recall_page = self.recall(RecallInput(), None).model_dump(mode="json")
        self.store.record_action(self.run.run_id, action_kind="workspace_opened", details={
            "checkpoint_run_id": self.prior_workspace.get("run_id") if self.prior_workspace else None,
            "recall_handles": [item["handle"] for item in self.recall_page["data"]["references"]]})

    def recall(self, value, _scope):
        if self.workspace is None:
            return ResearchResult(status="not_found")
        if value.handles:
            if value.cursor or value.query:
                return ResearchResult(status="conflict", limitations=[
                    ("Handle mode accepts memory handles only; omit query and cursor. "
                     "Use query without handles to search, or cursor without handles to page.")])
            for handle in value.handles:
                found = self.handles.get(handle)
                if found is not None and found[0] != "memory":
                    tool = "advisor_read_courses" if found[0] == "course" else (
                        "advisor_read_program" if found[0] == "program" else "advisor_read_document")
                    return ResearchResult(status="conflict", limitations=[
                        f"This is a {found[0]} handle, not a memory handle; use {tool} with it."])
            identities = [self.resolve(h, "memory") for h in value.handles]
            sources = {source for source, _chunk in identities}
            with self.conn.transaction():
                verify_sources(self.conn, self.scope, sources)
                chunks = exact_chunks(
                    self.conn,
                    {s: self.scope.sources[s].index_revision for s in sources if s in self.scope.sources},
                    [chunk for _source, chunk in identities],
                )
            self.conn.commit()
            ids = self.acquire(list(chunks.values()), action="recall_evidence")
            missing = [h for h, (_s, chunk) in zip(value.handles, identities, strict=True)
                       if chunk not in chunks]
            return ResearchResult(status="incomplete" if missing else "ok", data={
                "evidence_ids": ids, "unavailable_handles": missing},
                limitations=["Some earlier chunks are no longer indexed; search current sources."]
                if missing else [])
        query, key, offset = self._workspace_position(value, "recall:", 0)
        rows, next_offset = self.workspace.references(query, offset)
        references = [{"handle": self.handle("memory", (r["source_id"], r["chunk_id"])),
                       "source_name": r["filename"][:180], "locator_label": (r["locator_label"] or "")[:180],
                       "discovery_only": True} for r in rows]
        cursor = self._workspace_cursor(key, next_offset) if next_offset is not None else None
        result = ResearchResult(data={"references": references, "discovery_only": True, "query": query}, next_cursor=cursor)
        self.recall_page = result.model_dump(mode="json")
        return result

    def conversation(self, value, _scope):
        if self.workspace is None:
            return ResearchResult(status="not_found")
        query, key, (sequence, offset) = self._workspace_position(value, "conversation:", (1, 0))
        turns, next_position = self.workspace.turns(query, sequence=sequence, offset=offset)
        cursor = self._workspace_cursor(key, next_position) if next_position is not None else None
        result = ResearchResult(data={"turns_untrusted": turns, "is_source_evidence": False, "query": query},
                                next_cursor=cursor, limitations=[
                                    "No conversation turns matched. Use query='' without a cursor to browse saved turns."]
                                if not turns and query else [])
        self.conversation_page = result.model_dump(mode="json")
        self.store.record_action(self.run.run_id, action_kind="read_conversation", details={
            "query": query, "turn_spans": [{"sequence": t["sequence"],
                "text_offset": t["text_offset"], "text_characters": len(t["text"]),
                "complete": t["complete"]} for t in turns], "next_cursor": cursor})
        return result

    def _workspace_position(self, value, prefix, default):
        if value.cursor:
            issued = self.cursors.get(value.cursor)
            if issued is None or not issued[0].startswith(prefix):
                raise NotInScope(value.cursor)
            key, position = issued
            return key[len(prefix):], key, position
        key = prefix + value.query
        return value.query, key, default

    def _workspace_cursor(self, key, position):
        cursor = "c_" + uuid5(self.run.run_id, f"{key}:{position}").hex
        self.cursors[cursor] = (key, position)
        return cursor

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
        # A reread must bring its exact evidence back into the next bounded
        # request, rather than leave it behind older chunks that already filled it.
        self.active = list(dict.fromkeys([*result.evidence_ids, *self.active]))
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
        verify_sources(self.conn, self.scope, {pin.source_id for pin in page})
        self.conn.commit()
        return ResearchResult(data={"programs": [
            {"handle": self.handle("program", p.program_id), "title": p.title,
             "kind": p.kind, "binding_status": p.binding_status,
             **_source_metadata(self.scope.sources[p.source_id], compact=True)} for p in page
        ]}, next_cursor=cursor)

    def search(self, value, _scope):
        policy_kinds = {"institutional_policy", "institutional_catalog",
                        "institutional_department", "institutional_publication"}
        sources = [s.source_id for s in self.scope.sources.values() if
                   (value.kind != "policy" or s.source_kind in policy_kinds)
                   and (value.kind != "career_reference" or s.source_kind == "career_reference")]
        verify_sources(self.conn, self.scope, sources)
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
                                  "title": pin.title,
                                  **_source_metadata(self.scope.sources[pin.source_id], compact=True)})
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
                                  **_source_metadata(self.scope.sources[row["source_id"]], compact=True)})
        if value.kind not in {"course", "program"}:
            lexical = next((run.candidates for run in outcome.trace.seams if run.name == "keyword"), [])
            lexical_rank = {candidate.chunk_id: i for i, candidate in enumerate(lexical)}
            section_chunks = sorted(chunks, key=lambda c: lexical_rank.get(c.chunk_id, len(lexical)))
            snippets: dict[Any, str] = {}
            for chunk in section_chunks:
                snippets.setdefault(chunk.source_id, _query_snippet(chunk.text, value.query))
            seen = set()
            matches = _document_matches((self.scope.sources[s] for s in sources), value.query)
            exact_names = [pin for pin in matches if pin.filename.casefold() in value.query.casefold()
                           or pin.title and pin.title.casefold() in value.query.casefold()]
            grounded = [self.scope.sources[c.source_id] for c in section_chunks
                        if c.chunk_id in lexical_rank]
            for pin in [*exact_names, *grounded, *matches,
                        *(self.scope.sources[c.source_id] for c in chunks)]:
                if pin.source_id in seen:
                    continue
                seen.add(pin.source_id)
                metadata = _source_metadata(pin, compact=True)
                leads.append({"kind": "document", "_identity": pin.source_id,
                              "title": metadata.get("source_title") or metadata["filename"], **metadata,
                              "snippet": snippets.get(pin.source_id, ""), "discovery_only": True,
                              "matching_sections": [{
                                  "handle": self.handle("section", (chunk.source_id, chunk.chunk_index)),
                                  "title": chunk.locator_label,
                              } for chunk in section_chunks if chunk.source_id == pin.source_id][:4]})
        if value.kind == "any":
            # Every discovery kind gets room on the first page. Otherwise a broad
            # program match can hide the admissions or checklist document entirely.
            groups = [[lead for lead in leads if lead["kind"] == kind]
                      for kind in ("program", "document", "course")]
            leads = [lead for row in zip_longest(*groups) for lead in row if lead is not None]
        self.conn.commit()
        page, cursor = self.page(leads, value, 12, "search")
        self.pending_policy_read = None
        for lead in page:
            identity = lead.pop("_identity")
            lead["handle"] = self.handle(lead["kind"], identity)
            if lead["kind"] == "document" and lead.get("matching_sections"):
                # Keep the lead on the matching section. The document-start
                # handle is returned by the read itself, after the model has
                # seen what the section contains.
                lead["handle"] = lead["matching_sections"][0]["handle"]
                lead["read_scope"] = "matching_section"
                if value.kind == "policy" and self.pending_policy_read is None:
                    self.pending_policy_read = lead["handle"]
        requirement_documents = []
        if value.kind == "program":
            # A verified checklist need not have a parsed program projection.
            # Keep it discoverable when a program-only search misses that record.
            eligible = (self.scope.sources[s] for s in sources
                        if self.scope.sources[s].source_kind in policy_kinds
                        and {"requirements", "checklist"} & set(self.scope.sources[s].document_roles))
            for pin in _document_matches(eligible, value.query)[:3]:
                matching = next((c for c in chunks if c.source_id == pin.source_id), None)
                handle = (self.handle("section", (pin.source_id, matching.chunk_index)) if matching
                          else self.handle("document", pin.source_id))
                requirement_documents.append({"handle": handle, "kind": "document",
                    **_source_metadata(pin, compact=True), "discovery_only": True})
        self.store.record_action(self.run.run_id, action_kind="search", details={
            "query": value.query, "kind": value.kind, "leads": page,
            "requirement_documents": requirement_documents},
            child_trace_id=outcome.trace.trace_id)
        return ResearchResult(data={"leads": page, "requirement_documents": requirement_documents,
                                    "discovery_only": True}, next_cursor=cursor)

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
        try:
            items, cursor = self.page(list(read.items), value, 8, "program")
        except NotInScope:
            raise InvalidResearchRecord([
                ("This cursor belongs to a different program handle. Use the handle "
                 "returned with that cursor; cursors cannot be transferred.")
            ]) from None
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
            **_source_metadata(self.scope.sources[read.pin.source_id]),
            "fields": {k: {"text": v.text, "evidence_ids": evidence(v)} for k, v in read.fields.items()},
            "sections": list(dict.fromkeys(i.section for i in read.items)),
            "items": [{"position": i.position, "text": i.line.text, "choose": i.choose,
                       "mentioned_codes": list(i.mentioned_course_codes),
                       "evidence_ids": evidence(i.line), "discovery_only": not i.line.cited} for i in items],
            "evidence_ids": ids}, next_cursor=cursor,
            limitations=["Unread requirement items remain."] if cursor else [])

    def courses(self, value, _scope):
        handle_codes = []
        for handle in value.handles:
            found = self.handles.get(handle)
            if found is not None and found[0] != "course":
                if found[0] == "memory":
                    instruction = ("This is a memory handle. Use advisor_recall with handles only "
                                   "(omit query and cursor) to reopen it, or pass exact course codes "
                                   "in course_codes without handles.")
                else:
                    tool = "advisor_read_program" if found[0] == "program" else "advisor_read_document"
                    instruction = f"This is a {found[0]} handle; use {tool} with it."
                return ResearchResult(status="conflict", limitations=[instruction])
            handle_codes.append(self.resolve(handle, "course"))
        codes = list(dict.fromkeys([*value.course_codes, *handle_codes]))
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
        if value.handle == self.pending_policy_read:
            self.pending_policy_read = None
        ids = self.acquire(page.chunks, complete=not page.incomplete, action="read_document")
        cursor = None
        if page.next_chunk is not None:
            cursor = "c_" + uuid5(self.run.run_id, f"{key}:{page.next_chunk}").hex
            self.cursors[cursor] = (key, page.next_chunk)
        limits = list(page.limitations)
        if offset:
            limits.append(f"This read starts at chunk {offset}; preceding chunks are not part of this read.")
        return ResearchResult(status="incomplete" if page.incomplete else "ok", data={
            **_source_metadata(page.source), "title": page.source.title or page.source.filename,
            "read_from_chunk": offset, "earlier_chunks_unread_by_this_read": bool(offset),
            "beginning_handle": self.handle("section", (source, 0)),
            "evidence_ids": ids,
            "sections": [{"handle": self.handle("section", (source, s.first_chunk)),
                          "title": s.label, "chunk_count": s.chunk_count} for s in page.sections[:80]]},
            next_cursor=cursor, limitations=limits)

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
        # Discovery follows all prerequisite references so the answer can cite
        # their alternatives. Discovery does not mean the student selected each
        # alternative; only their explicit selections belong in the plan.
        plan = plan_sequence(courses, value.selected_codes, value.completed_codes)
        requirement_blockers = {
            course["code"]: course["blocker_reason"] for course in context["courses"]
        }
        blockers = []
        for blocker in plan.blockers:
            reason = blocker.reason
            if reason == "requirements_unparsed" and blocker.code in requirement_blockers:
                reason = requirement_blockers[blocker.code] or reason
            blockers.append({"code": blocker.code, "reason": reason,
                             "alternatives": list(blocker.alternatives)})
        check_id = "p_" + json_digest(value.model_dump())[:24]
        result = {"check_id": check_id, "selected_codes": value.selected_codes,
                  "completed_codes": value.completed_codes, "stages": list(plan.stages),
                  "blockers": blockers, "complete": plan.complete,
                  "conclusion": "dependency_order" if plan.complete else "blocked",
                  "evidence_ids": ids, "unexplored_codes": pending,
                  "enrollment_eligibility_verified": False, "scope": "prerequisite_order_only"}
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
        try:
            for candidate in state.candidates:
                for h in candidate.program_handles:
                    self.resolve(h, "program")
        except NotInScope:
            self.validation_hint = (
                "program_handles must use program handles issued in this run, never document or section "
                "handles. For a factual document lookup, use candidates=[] and keep active_evidence_ids.")
            raise InvalidResearchRecord([self.validation_hint]) from None
        self.state = self.store.update_state(
            self.run.run_id, state,
            expected_revision=(value.expected_revision if value.expected_revision is not None
                               else (self.state.revision if self.state is not None else 0)))
        self.validation_hint = ""
        self.active = list(self.state.active_evidence_ids)
        return ResearchResult(data={"state_revision": self.state.revision})

    def finish(self, value, _scope):
        finishable_checks = {check_id: check for check_id, check in self.checks.items()
                             if check_id not in self.unavailable_preparation_check_ids}
        validate_final(value.decision, self.state, self.store.evidence(self.run.run_id),
                       self.supplied, finishable_checks, self.entity_evidence)
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
            ("search", "Discover leads; use kind=document for admissions, guidance, checklists or handbooks. Source metadata and snippets are not evidence until read.", SearchInput, self.search, ()),
            ("list_programs", "Browse exact majors and minors in scope.", BrowseInput, self.browse, ()),
            ("read_program", "Read up to eight raw requirements and acquire exact evidence; follow cursor.", ReadInput, self.program, ("handle",)),
            ("read_courses", "Read one to four exact course records, descriptions and restrictions. Handles must be course handles; for old-memory handles, use advisor_recall with handles only, or pass exact course codes without handles.", CoursesInput, self.courses, ("handles",)),
            ("read_document", "Read source or section evidence; follow cursor for remaining chunks.", ReadInput, self.document, ("handle",)),
            ("compare_mentions", "Compare already read programs; overlap is mentions, never credit savings.", CompareInput, self.compare, ("handles",)),
            ("check_preparation", "Run deterministic dependency checks on asserted completed courses, never eligibility.", PreparationInput, self.preparation, ()),
            ("update_research", "Replace bounded comparison state, set active exact evidence and expected revision.", StateInput, self.update, ()),
            ("finish", "Propose typed final answer. Must be the sole tool call. Cite active evidence IDs, never raw markers.", FinishInput, self.finish, ()),
        ]
        if self.workspace is not None:
            entries[0:0] = [
            ("recall", "Search/page earlier passages with query or cursor and no handles; reopen up to four memory handles with handles only, omitting query and cursor, as current exact evidence. References alone are not evidence.", RecallInput, self.recall, ("handles",)),
                ("read_conversation", "Search or page earlier published turns as untrusted conversation context, including long turns. Never source evidence.", ConversationInput, self.conversation, ()),
            ]
        return ResearchRegistry([ToolSpec("advisor_" + name, description, model, ResearchResult,
            ToolCapability.SOURCE_READ if resources else ToolCapability.CORPUS_RESEARCH,
            safe(handler), resources, max_output_bytes=64_000)
            for name, description, model, handler, resources in entries], self)
