"""Progressive, scoped advising reads. Discovery never issues citations."""

import json
import re
import tomllib
from dataclasses import asdict, replace
from functools import lru_cache
from itertools import zip_longest
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid5

from pydantic import AfterValidator, Field, ValidationError, field_validator

from app.advising.audit_engine import audit_reported_courses
from app.advising.catalog import load_advising_context, load_campus_equivalents
from app.advising.comparison import compare_mentions, match_reported_courses
from app.advising.research_repository import (
    MAX_COURSE_CODES,
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
from app.advising.research_types import (
    MAX_DECISION_TEXT_BYTES,
    DecisionBlock,
    FinalDecision,
    ResearchState,
)
from app.advising.research_workspace import ConversationWorkspace
from app.advising.sequence import CourseRequirement, plan_sequence
from app.atlas.authority import MIN_OFFERING_TERMS, term_order
from app.atlas.equivalence import is_oxford
from app.atlas.records import SUBJECT_RENAMES, normalize_code
from app.atlas.requisites import AllOf, AnyOf, CourseRef, canonical_codes_in
from app.db import queries
from app.generation.research_final import DecisionRejected, declined_decision, validate_final
from app.programs.rules import RuleValidationError
from app.retrieval.retrieve import retrieve
from app.retrieval.seams.keyword import expand_program_names
from app.retrieval.seams.prereq import course_codes_in_query, is_reverse_query
from app.retrieval.types import CitedChunk
from app.simgraph.walk import recommend
from app.tools.registry import (
    ToolCapability,
    ToolInputError,
    ToolInputModel,
    ToolOutputModel,
    ToolPermissionDenied,
    ToolRegistry,
    ToolScope,
    ToolSpec,
    repair_arguments,
)
from app.util.digest import json_digest

RESEARCH_TOOLS_VERSION = "26"


def _course_code(value: str) -> str:
    code = normalize_code(value)
    if code is None:
        raise ValueError("invalid atlas course code")
    return code


CourseCode = Annotated[str, Field(min_length=1, max_length=32), AfterValidator(_course_code)]


_CONFLICTS_PATH = Path(__file__).resolve().parents[2] / "configs" / "source_conflicts.toml"


@lru_cache(maxsize=1)
def _load_conflicts() -> tuple[dict, ...]:
    with _CONFLICTS_PATH.open("rb") as handle:
        return tuple({**entry, "pattern": re.compile(entry["question"])}
                     for entry in tomllib.load(handle)["conflict"])


def source_conflicts(question: str | None) -> list[dict]:
    """The known disagreements between Emory sources that a question runs into."""
    return [entry for entry in _load_conflicts() if entry["pattern"].search(question or "")]


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
    completed_codes: list[CourseCode] = Field(max_length=30)


class AuditInput(ToolInputModel):
    handle: str = Field(min_length=1, max_length=128)
    completed_codes: list[CourseCode] = Field(max_length=60)


class SuggestInput(ToolInputModel):
    goal: str = Field(min_length=1, max_length=300)


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
    value = repair_arguments(value, model)
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


#: What the server read about a course that no evidence passage holds.
COURSE_FACT_FIELDS = ("title", "required_by", "named_by_programs", "offering_note")
COURSE_FACT_TERMS = 4
COURSE_FACT_COURSES = 20
#: The most one evidence step may read.
MAX_READ_BYTES = 48_000
#: Requirement items in one program read.
PROGRAM_PAGE_ITEMS = 30
#: Programs listed on one course record.
MAX_NAMING_PROGRAMS = 40
#: Program lines a course read carries, and how much of each line.
MAX_PROGRAM_LINES = 30
MAX_PROGRAM_LINE_CHARS = 160
_PROGRAM_QUESTION_RE = re.compile(r"\b(?:majors?|minors?|programs?|degrees?)\b", re.IGNORECASE)
# A line match names a course; it never shows a degree requirement is done.
_REQUIREMENT_MET_RE = re.compile(
    r"\b(?:satisf(?:y|ies|ied|ying)|fulfil+(?:s|ed|ling)?|meets?|met|completed?|finish(?:es|ed)?)\b"
    r"[^.;\n]{0,60}\b(?:requirements?|lines?)\b", re.IGNORECASE)
_NEGATED_RE = re.compile(r"\b(?:not|never|cannot|can't|isn't|aren't|whether)\b[^.;\n]{0,30}$",
                         re.IGNORECASE)
# "Which majors need MATH 221": program_lines is already the whole answer.
_WHICH_PROGRAMS_RE = re.compile(
    r"\b(?:which|what|any)\b.*\b(?:majors?|minors?|programs?|degrees?)\b.*"
    r"\b(?:need|needs|require|requires|use|uses|include|includes)\b", re.IGNORECASE)
#: Courses opened for the model in one step; the evidence register has its own limit.
MAX_AUTO_READ_COURSES = 12
#: Topic groups and courses per group a suggestion returns, closest to the goal first.
MAX_SUGGESTED_GROUPS = 3
MAX_SUGGESTED_COURSES = 12


def course_stem(code: str) -> str:
    """Subject and number without the campus mark or suffix: ENG_OX 341W is ENG 341."""
    return re.sub(r"[A-Z]+$", "", code.replace("_OX", ""))


_FORMER_SUBJECTS = {new: old for old, new in SUBJECT_RENAMES.items()}


def _former_code(code: str) -> str | None:
    """The code a renamed subject's course had before: DATASCI 190 was QTM 190."""
    subject, _, rest = code.partition(" ")
    return f"{_FORMER_SUBJECTS[subject]} {rest}" if subject in _FORMER_SUBJECTS and rest else None


def _current_code(code: str) -> str:
    """A term listing's printed code under its subject's current name."""
    subject, _, rest = code.partition(" ")
    return f"{SUBJECT_RENAMES[subject]} {rest}" if subject in SUBJECT_RENAMES and rest else code


def offering_note(code: str, checked: list[str], variants: list[str]) -> str:
    """Say what the Atlas terms show for a course that none of them list."""
    span = f"{checked[0]} to {checked[-1]}"
    if variants:
        return (f"{code} is in no Course Atlas term from {span}, but {', '.join(variants)} "
                "is. Advise from that code.")
    return (f"{code} is in no Course Atlas term from {span}. It is no longer offered, even "
            "though a catalog record or a requisite may still name it.")


# A grading-basis question, and a program line that states a grading rule.
_GRADING_ASKED = re.compile(
    r"(?<![a-z])s[/-]u(?![a-z])|pass[/-]fail|letter[- ]grade|satisfactory|grading basis",
    re.IGNORECASE)
_GRADING_LINE = re.compile(r"letter grade|(?<![A-Za-z])S/U(?![A-Za-z])|pass/fail|satisfactory",
                           re.IGNORECASE)
MAX_GRADING_ITEMS = 6

_DEGREE_NAMED = re.compile(r"\b(?:b\.?a\.?|b\.?s\.?|bachelor)\b", re.IGNORECASE)


def other_degrees(pin, programs) -> list:
    """The same subject and kind under another degree: Computer Science (BA) for its BS."""
    subject = _program_subject(pin.title)
    return [other for other in programs if other.program_id != pin.program_id
            and other.kind == pin.kind and other.degree != pin.degree
            and _program_subject(other.title) == subject]


def _program_subject(title: str) -> str:
    return re.sub(r"\s*\(.*$", "", title).casefold()


def _term_offerings(pin) -> bool:
    return "course_offerings" in pin.document_roles


def _opened_chunks(reads):
    """A course opened in bulk is its record: cards would crowd out the other courses."""
    return [c for r in reads for c in r.chunks[:1]]


def _within_read_limit(chunks):
    """Chunks in order, without repeats, up to what one evidence step may read."""
    kept, size = {}, 0
    for chunk in chunks:
        length = len(chunk.text.encode())
        if chunk.chunk_id not in kept and size + length <= MAX_READ_BYTES:
            kept[chunk.chunk_id] = chunk
            size += length
    return list(kept.values())


class ResearchSession:
    def __init__(self, conn, scope, store, run, *, settings, config, embedder,
                 artifact_references=()):
        self.conn, self.scope, self.store, self.run = conn, scope, store, run
        self.settings, self.config, self.embedder = settings, config, embedder
        # Tuples, not model objects: the pure validator only needs exact identities.
        self.artifact_references = {
            (str(item[0]), str(item[1])) if isinstance(item, (tuple, list))
            else (str(item.artifactId), str(item.revisionId))
            for item in artifact_references
        }
        self.handles: dict[str, tuple[str, Any]] = {}
        self.cursors: dict[str, tuple[str, Any]] = {}
        self.program_reads = {}
        self.program_positions = {}
        self.program_sizes = {}
        self.program_cursors = {}
        self.pending_policy_reads: tuple[str, ...] = ()
        self._course_codes: frozenset[str] | None = None
        self._master_codes: frozenset[str] = frozenset()
        self._retired_codes: frozenset[str] | None = None
        self.actions = []
        self.checks: dict[str, dict] = {}
        self.unavailable_preparation_check_ids: set[str] = set()
        self.entity_evidence: dict[str, set[str]] = {}
        self.active: list[str] = []
        self.supplied: set[str] = set()
        self.decision = None
        self.rejected_decision = None
        self.question = None
        # The advisor's fixed instructions, so a reply that repeats them is refused.
        self.instructions = None
        # The student asked for something to keep; a reply must not call it ready.
        self.artifact_requested = False
        self.history_context = None
        self.execution_id = None
        self.new_evidence = 0
        self.read_count = 0
        self.course_facts: dict[str, dict] = {}
        self.follow_on_codes: set[str] = set()
        # Set when the server already holds the whole answer, so the next step is writing it.
        self.answer_in_hand = ""
        self.must_name: set[str] = set()
        self.must_name_degrees: set[str] = set()
        self.line_facts: dict[str, list] = {}
        self.audit_matched_lines_only = False
        self.read_keys: set[tuple] = set()
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
            # Reopening memory must restore entity refs so recommendations /
            # identity checks still see the course or program that was read.
            prior_refs = {
                e.chunk_id: e.entity_refs
                for e in self.store.evidence(self.run.run_id) if e.entity_refs
            }
            refs = {}
            for chunk in chunks.values():
                key = str(chunk.chunk_id)
                if key in prior_refs:
                    refs[chunk.chunk_id] = prior_refs[key]
                    continue
                codes = tuple(canonical_codes_in(chunk.locator_label or ""))
                if codes:
                    refs[chunk.chunk_id] = codes
            ids = self.acquire(list(chunks.values()), refs=refs, action="recall_evidence")
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

    def acquire(self, chunks, *, refs=(), complete=True, action="read", progress_key=None):
        before = len(self.store.evidence(self.run.run_id))
        read_bytes = sum(len(c.text.encode()) for c in chunks)
        existing = self.store.evidence(self.run.run_id)
        known = {(e.source_id, e.source_revision, e.chunk_id) for e in existing}
        added = [c for c in chunks if (str(c.source_id), c.source_revision, str(c.chunk_id)) not in known]
        if (len(existing) + len(added) > self.config.research.max_evidence_items
                or sum(len(e.exact_text.encode()) for e in existing)
                + sum(len(c.text.encode()) for c in added) > self.config.research.max_evidence_bytes):
            raise EvidenceLimit("configured evidence register limit reached")
        if read_bytes > MAX_READ_BYTES:
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
        key = progress_key if progress_key is not None else (action,)
        if key not in self.read_keys:
            self.read_keys.add(key)
            self.read_count += bool(added)
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
                   and (value.kind != "career_reference" or s.source_kind == "career_reference")
                   and not (value.kind in {"policy", "document"} and _term_offerings(s))]
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
            terms = [word for word in re.findall(r"\w+", expand_program_names(value.query))
                     if len(word) > 2]
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
            # A program named in the student's own words outranks every partial match, and
            # the longest name wins: "religion and anthropology" is not "anthropology".
            asked = f"{value.query} {self.question or ''}".casefold()
            for pin in self.scope.programs.values():
                subject = _program_subject(pin.title)
                if subject in asked:
                    ranks[pin.program_id] = 1_000.0 + len(subject)
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
            # A title in the student's own words is the course, whatever the passages found.
            titled = self.conn.execute(queries.get("research_courses_titled_in"), {
                "collection_id": self.scope.collection_id, "source_ids": sources,
                "text": f"{value.query} {self.question or ''}".casefold()}).fetchall()
            seen = set()
            for row in [*titled, *courses]:
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
                if pin.source_id in seen or _term_offerings(pin):
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
        self.pending_policy_reads = ()
        for lead in page:
            identity = lead.pop("_identity")
            lead["handle"] = self.handle(lead["kind"], identity)
            if lead["kind"] == "document" and lead.get("matching_sections"):
                # Keep the lead on the matching section. The document-start
                # handle is returned by the read itself, after the model has
                # seen what the section contains.
                lead["handle"] = lead["matching_sections"][0]["handle"]
                lead["read_scope"] = "matching_section"
                if value.kind == "policy" and not self.pending_policy_reads:
                    # The model must read the policy before searching again, but
                    # it must be able to reach every section the search
                    # matched. Pinning it to the first matched section - which
                    # the keyword seam ranks, not the retrieval score - makes
                    # the rule unreachable when a different section of the same
                    # document holds it.
                    self.pending_policy_reads = tuple(
                        section["handle"] for section in lead["matching_sections"])
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

    def _exact_chunks(self, chunk_ids, sources):
        with self.conn.transaction():
            verify_sources(self.conn, self.scope, sources)
            found = exact_chunks(
                self.conn, {s: p.index_revision for s, p in self.scope.sources.items()}, chunk_ids)
        self.conn.commit()
        return found

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
            items, cursor = self.page(list(read.items), value, PROGRAM_PAGE_ITEMS, "program")
        except NotInScope:
            raise InvalidResearchRecord([
                ("This cursor belongs to a different program handle. Use the handle "
                 "returned with that cursor; cursors cannot be transferred.")
            ]) from None
        if _GRADING_ASKED.search(self.question or ""):
            # The program's own grading rule outranks the College default, so it
            # is read with the first page wherever the program lists it.
            shown = {i.position for i in items}
            items = [*items, *[i for i in read.items if i.position not in shown
                               and _GRADING_LINE.search(i.line.text)][:MAX_GRADING_ITEMS]]
        chunks = {c.chunk_id: c for supported in [*read.fields.values(), *(i.line for i in items)]
                  for c, _start, _end in supported.support}
        ids = self.acquire(list(chunks.values()), refs=(value.handle,), action="read_program",
                           progress_key=("read_program", value.handle, value.cursor))
        records = {e.chunk_id: e.evidence_id for e in self.store.evidence(self.run.run_id)}
        def evidence(supported):
            return list(dict.fromkeys(records[str(c.chunk_id)] for c, _s, _e in supported.support))
        positions = self.program_positions.setdefault(value.handle, set())
        self.program_sizes[value.handle] = len(read.items)
        self.program_cursors[value.handle] = cursor
        positions.update(i.position for i in items)
        self.program_reads[value.handle] = replace(read, items=tuple(
            i for i in read.items if i.position in positions))
        unsupported_fields = [name for name, field in read.fields.items() if not field.cited]
        limitations = (["Unread requirement items remain."] if cursor else [])
        if unsupported_fields:
            limitations.append("Program fields without evidence are discovery only: "
                               + ", ".join(sorted(unsupported_fields)) + ".")
        renamed = {old: new for old, new in SUBJECT_RENAMES.items()
                   if any(re.search(rf"\b{old}\b", i.line.text) for i in items)}
        others = other_degrees(read.pin, self.scope.programs.values())
        degrees = [{"title": other.title, "handle": self.handle("program", other.program_id)}
                   for other in others]
        if not _DEGREE_NAMED.search(self.question or ""):
            self.must_name_degrees |= {other.degree for other in others if other.degree}
        return ResearchResult(data={"title": read.pin.title, "handle": value.handle,
            "binding_status": read.pin.binding_status,
            **({"renamed_subjects": renamed} if renamed else {}),
            **({"other_degrees": degrees} if degrees else {}),
            **_source_metadata(self.scope.sources[read.pin.source_id]),
            "fields": {k: {"text": v.text, "evidence_ids": evidence(v),
                           "discovery_only": not v.cited} for k, v in read.fields.items()},
            "sections": list(dict.fromkeys(i.section for i in read.items)),
            "items": [{"position": i.position, "text": i.line.text, "choose": i.choose,
                       "mentioned_codes": list(i.mentioned_course_codes),
                       "evidence_ids": evidence(i.line), "discovery_only": not i.line.cited} for i in items],
            "evidence_ids": ids}, next_cursor=cursor,
            limitations=limitations)

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
        required_by = {row["code"]: row["required_by"][:24] for row in self.conn.execute(
            queries.get("research_courses_requiring"), {
                "collection_id": self.scope.collection_id, "codes": [r.code for r in reads]})}
        # A program names one campus's code; the course a student read may be the other's.
        equivalents = load_campus_equivalents(self.conn, collection_id=self.scope.collection_id)
        sharing = {code: r.code for r in reads
                   for code in (r.code, *equivalents.get(r.code, ()))}
        named_by: dict[str, list] = {}
        for row in self.conn.execute(queries.get("research_programs_naming_courses"), {
                "collection_id": self.scope.collection_id, "codes": list(sharing)}):
            titles = named_by.setdefault(sharing[row["code"]], [])
            titles.extend(title for title in row["programs"] if title not in titles)
        named_by = {code: titles[:MAX_NAMING_PROGRAMS] for code, titles in named_by.items()}
        program_lines: dict[str, list] = {}
        if self.question and _PROGRAM_QUESTION_RE.search(self.question):
            for row in self.conn.execute(queries.get("research_program_lines_naming_courses"), {
                    "collection_id": self.scope.collection_id, "codes": list(sharing)}):
                if row["source_id"] in self.scope.sources:
                    program_lines.setdefault(sharing[row["code"]], []).append(row)
        self.conn.commit()
        program_lines = {code: rows[:MAX_PROGRAM_LINES] for code, rows in program_lines.items()}
        if program_lines and self.question and _WHICH_PROGRAMS_RE.search(self.question):
            self.answer_in_hand = (
                "program_lines is every program line naming this course. List every program "
                "from it, quote how its line uses the course, say so when a line offers it as "
                "one option, and cite each line's evidence_ids.")
        # "What can I take after X?" is answered by the graph. Open the courses it
        # names here, so the answer rests on them instead of on what a search finds.
        follow_ons = []
        if self.question and is_reverse_query(self.question):
            wanted = sorted({code for r in reads if r.code not in self.follow_on_codes
                             for code in required_by.get(r.code, ())} - set(codes))
            for start in range(0, min(len(wanted), MAX_AUTO_READ_COURSES), MAX_COURSE_CODES):
                follow_ons.extend(read_courses(self.conn, self.scope,
                                               wanted[start:start + MAX_COURSE_CODES]))
                self.conn.commit()
        self.follow_on_codes.update(r.code for r in follow_ons)
        if follow_ons:
            self.answer_in_hand = (
                "required_by is every course naming this one, and follow_on_courses holds their "
                "records. List every one, say which other prerequisites each still needs, and "
                "cite each record.")
        everything = [*reads, *follow_ons]
        # A which-programs answer needs each course's record, not its term listings.
        course_chunks = [*(_opened_chunks(reads) if self.answer_in_hand
                           else (c for r in reads for c in r.chunks)),
                         *_opened_chunks(follow_ons)]
        refs = {c.chunk_id: (r.code, r.canonical_code) for r in everything for c in r.chunks}
        # The program lines arrive with their passages, so an answer can cite a
        # line without opening every program that names the course.
        rows = [row for lines in program_lines.values() for row in lines]
        line_chunks = list(self._exact_chunks({cid for row in rows for cid in row["chunk_ids"]},
                                              {row["source_id"] for row in rows}).values())
        progress = ("read_courses", tuple(sorted(codes)))
        try:
            ids = self.acquire(_within_read_limit([*course_chunks, *line_chunks]), refs=refs,
                               action="read_courses", progress_key=progress)
        except EvidenceLimit:
            ids = self.acquire(_within_read_limit(course_chunks), refs=refs,
                               action="read_courses", progress_key=progress)
        records = {e.chunk_id: e.evidence_id for e in self.store.evidence(self.run.run_id)}
        terms, checked, variants = self._offerings([r.code for r in everything])
        self.must_name |= {r.code for r in follow_ons if r.code in terms}

        def record(r):
            data = {"code": r.code, "title": r.title, "requisites_raw": r.requisites_raw,
                    "parse_status": r.parse_status, "canonical_code": r.canonical_code,
                    "offered_terms": terms.get(r.code, []),
                    "required_by": required_by.get(r.code, []),
                    "named_by_programs": named_by.get(r.code, [])}
            if r.code in program_lines:
                data["program_lines"] = [{
                    "program": row["program"], "line": row["line"][:MAX_PROGRAM_LINE_CHARS],
                    "choose": row["choose"],
                    "evidence_ids": [records[str(cid)] for cid in row["chunk_ids"]
                                     if str(cid) in records],
                } for row in program_lines[r.code]]
                if self.answer_in_hand:
                    self.line_facts[r.code] = data["program_lines"]
            running = variants.get(course_stem(r.code), [])
            # A master row the Atlas import keeps current is offered, even when
            # no listing matches its code; it is never called retired.
            if r.code not in terms and len(checked) >= MIN_OFFERING_TERMS and (
                    running or r.lifecycle != "current"):
                data["offering_note"] = offering_note(r.code, checked, running)
            self.course_facts.pop(r.code, None)  # The newest reads stay in a full prompt.
            self.course_facts[r.code] = {key: data[key] for key in COURSE_FACT_FIELDS if data.get(key)}
            self.course_facts[r.code]["offered_terms"] = sorted(
                data["offered_terms"], key=term_order)[-COURSE_FACT_TERMS:]
            self.course_facts[r.code]["evidence_ids"] = sorted(self.entity_evidence.get(r.code, ()))
            return data

        return ResearchResult(data={"courses": [record(r) for r in reads],
            "follow_on_courses": [record(r) for r in follow_ons], "terms_checked": checked,
            "evidence_ids": ids, "missing_codes": sorted(set(codes) - {r.code for r in reads})},
            status="ok" if reads else "not_found")

    def _offerings(self, codes):
        """Each course's Atlas terms, every term checked, and running variants by stem.

        A listing keeps the code the Atlas printed that term, so a course under a
        renamed subject is asked for under both: Fall 2025 lists QTM 190, which
        is DATASCI 190.
        """
        former = {old: code for code in codes if (old := _former_code(code))}
        found: dict[str, dict[str, None]] = {}
        for row in self.conn.execute(queries.get("research_course_offering_terms"), {
                "collection_id": self.scope.collection_id, "codes": [*codes, *former]}):
            found.setdefault(former.get(row["code"], row["code"]), {})[row["term"]] = None
        terms = {code: list(listed) for code, listed in found.items()}
        checked = sorted((row["term"] for row in self.conn.execute(
            queries.get("research_offering_terms_checked"),
            {"collection_id": self.scope.collection_id})), key=term_order)
        variants: dict[str, list[str]] = {}
        unlisted = [code for code in codes if code not in terms]
        stems = {course_stem(code) for code in unlisted}
        stems |= {course_stem(old) for code in unlisted if (old := _former_code(code))}
        for row in self.conn.execute(queries.get("research_course_offering_variants"), {
                "collection_id": self.scope.collection_id, "stems": sorted(stems)}):
            code = _current_code(row["code"])
            listed = variants.setdefault(course_stem(code), [])
            if code not in listed:
                listed.append(code)
        self.conn.commit()
        return terms, checked, variants

    def _retired_courses(self, decision) -> frozenset[str]:
        """Courses an answer names that no checked Atlas term shows, under any code.

        Taken from the answer, not from what the run read, so skipping the read
        cannot hide one.
        """
        candidates = {candidate.id: candidate for candidate in (
            self.state.candidates if self.state else ())}
        named = {code for block in decision.blocks for code in canonical_codes_in(block.text)}
        named |= {normalize_code(code) for code in decision.recommended_course_codes}
        named |= {code for cid in decision.candidate_ids if cid in candidates
                  for code in candidates[cid].course_codes}
        if named:
            named &= self._known_course_codes() | self._retired_course_codes()
        if not named:
            return frozenset()
        terms, checked, variants = self._offerings(sorted(named))
        return frozenset(code for code in named
                         if self._not_offered(code, terms, checked, variants))

    def _not_offered(self, code, terms, checked, variants) -> bool:
        """No checked term shows the course or a variant, and its master row is not current.

        The Atlas import keeps a master row current only when a term showed it.
        A code with no master row (an Oxford alternative a requisite names) is
        judged by the listings alone.
        """
        self._known_course_codes()
        return (len(checked) >= MIN_OFFERING_TERMS and code not in terms
                and course_stem(code) not in variants and code not in self._master_codes)

    def _retired_course_codes(self) -> frozenset[str]:
        """Master rows the Atlas import retired: real courses, no longer offered."""
        if self._retired_codes is None:
            self._retired_codes = frozenset(row["code"] for row in self.conn.execute(
                queries.get("list_collection_retired_course_codes"),
                {"collection_id": self.scope.collection_id}))
            self.conn.commit()
        return self._retired_codes

    def document(self, value, _scope):
        kind, identity = self.handles.get(value.handle, (None, None))
        if kind not in {"document", "section"}:
            raise NotInScope(value.handle)
        source, start = (identity, 0) if kind == "document" else identity
        key = "document:" + value.handle
        offset = self.offset(value.cursor, key) if value.cursor else start
        page = read_document(self.conn, self.scope, source, from_chunk=offset)
        self.conn.commit()
        if value.handle in self.pending_policy_reads:
            self.pending_policy_reads = ()
        ids = self.acquire(page.chunks, complete=not page.incomplete, action="read_document",
                           progress_key=("read_document", value.handle, value.cursor, offset))
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
        handles = list(dict.fromkeys(value.handles))
        if len(handles) != len(value.handles) or not 2 <= len(handles) <= 4:
            return ResearchResult(
                status="conflict",
                limitations=["Compare two to four distinct programs."],
            )
        if any(h not in self.program_reads for h in handles):
            return ResearchResult(status="conflict", limitations=["Read each program first."])
        result = compare_mentions([self.program_reads[h] for h in handles])
        data = asdict(result)
        # Bound by dropping whole codes, never by shortening a code's refs into
        # a stronger claim than the omitted lines supported.
        data["shared"] = {
            code: list(refs) for code, refs in list(data["shared"].items())[:12]
        }
        data["unique"] = {
            str(pid): {code: list(refs) for code, refs in list(codes.items())[:12]}
            for pid, codes in data["unique"].items()
        }
        data["unresolved_positions"] = {str(pid): positions for pid, positions
                                        in data["unresolved_positions"].items()}
        incomplete = any(len(self.program_positions[h]) < len(read_program(
            self.conn, self.scope, self.resolve(h, "program")).items) for h in handles)
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

    def _unstated_completed(self, codes) -> list[str]:
        """Completed courses the student never named, in this question or earlier turns.

        An eligibility answer may count only what the student reported. A course
        that a stated course itself required is still unstated: the student may
        hold the stated one through transfer credit or a waiver.
        """
        if not codes:
            return []
        said = set(course_codes_in_query(f"{self.question or ''}\n{self.history_context or ''}"))
        equivalents = load_campus_equivalents(self.conn, collection_id=self.scope.collection_id)
        said |= {other for code in said for other in equivalents.get(code, ())}
        return [code for code in codes
                if (normal := normalize_code(code)) is not None and normal not in said]

    def preparation(self, value, _scope):
        unstated = self._unstated_completed(value.completed_codes)
        if unstated:
            raise InvalidResearchRecord([(
                "completed_codes may hold only courses the student said they completed; they "
                f"did not name {', '.join(unstated)}. Check with what they said and tell them "
                "what else the course needs, rather than assuming it is done")])
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
        ids = self.acquire(list(all_chunks.values()), refs=refs, action="check_preparation",
                           progress_key=("check_preparation", tuple(value.selected_codes),
                                         tuple(value.completed_codes)))
        courses = {c["code"]: CourseRequirement(c["code"], c["title"],
                   c["parse_status"] if c["requirements_evidence_complete"] else "unparsed",
                   c["prerequisites"], c["corequisites"], c["requisites_raw"])
                   for c in context["courses"]}
        # Discovery follows all prerequisite references so the answer can cite
        # their alternatives. Discovery does not mean the student selected each
        # alternative; only their explicit selections belong in the plan.
        plan = plan_sequence(courses, value.selected_codes, value.completed_codes,
                             load_campus_equivalents(self.conn,
                                                     collection_id=self.scope.collection_id))
        requirement_blockers = {
            course["code"]: course["blocker_reason"] for course in context["courses"]
        }
        blockers = []
        for blocker in plan.blockers:
            reason = blocker.reason
            if reason == "requirements_unparsed" and blocker.code in requirement_blockers:
                reason = requirement_blockers[blocker.code] or reason
            blockers.append({"code": blocker.code, "reason": reason,
                             "alternatives": list(blocker.alternatives),
                             "alternative_groups": [list(group) for group in blocker.alternative_groups],
                             "alternative_groups_truncated": blocker.alternative_groups_truncated})
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

    def audit(self, value, _scope):
        self.resolve(value.handle, "program")
        read = self.program_reads.get(value.handle)
        if read is None or self.program_cursors[value.handle]:
            return ResearchResult(status="conflict", limitations=[
                "Read the whole program first, following every cursor."])
        equivalents = load_campus_equivalents(self.conn, collection_id=self.scope.collection_id)
        release = self.conn.execute(queries.get("get_current_rule_release"),
                                    {"program_id": read.pin.program_id}).fetchone()
        self.conn.commit()
        records = {e.chunk_id: e.evidence_id for e in self.store.evidence(self.run.run_id)}
        evidence = {item.position: list(dict.fromkeys(
            records[str(c.chunk_id)] for c, _s, _e in item.line.support)) for item in read.items}
        cited = list(dict.fromkeys(i for ids in evidence.values() for i in ids))
        self.active = list(dict.fromkeys([*cited, *self.active]))
        base = {"program": read.pin.title, "handle": value.handle,
                "completed_codes": value.completed_codes, "evidence_ids": cited}
        if release is None:
            self.audit_matched_lines_only = True
            matches, unmatched = match_reported_courses(
                read.items, value.completed_codes, equivalents)
            return ResearchResult(status="incomplete", data={
                **base, "reviewed_rules": False, "unmatched_codes": list(unmatched),
                "lines": [{"position": m.item.position, "text": m.item.line.text,
                           "choose": m.item.choose, "state": m.state,
                           "reported_codes": list(m.reported),
                           "evidence_ids": evidence[m.item.position]} for m in matches]},
                limitations=[("No staff-reviewed rules exist for this program. Lines show where "
                              "reported courses are named, not that a requirement is met.")])
        try:
            result = audit_reported_courses(
                release["rules_tree"], value.completed_codes, equivalents=equivalents,
                rules_digest=release["rules_digest"],
                max_search_states=self.settings.audit_max_search_states)
        except (RuleValidationError, ValueError) as exc:
            return ResearchResult(status="conflict", limitations=[f"Unsupported audit: {exc}"])
        return ResearchResult(data={
            **base, "reviewed_rules": True, "overall_status": result.overall_status,
            "blockers": result.blockers,
            "rules": [{"title": row.title, "status": row.status,
                       "required_count": row.required_count,
                       "satisfied_count": row.satisfied_count,
                       "unresolved_reason": row.unresolved_reason,
                       "evidence_ids": evidence.get(row.raw_position, [])}
                      for row in result.rule_evaluations if row.status != "not_selected"]},
            limitations=[("Uses the student's report, not a transcript: grades, credit hours and "
                          "transfer rules are unverified.")])

    def suggest(self, value, _scope):
        groups = recommend(self.conn, collection_id=self.scope.collection_id,
                           query_vec=self.embedder.embed_query(value.goal),
                           simgraph=self.config.simgraph)[:MAX_SUGGESTED_GROUPS]
        self.conn.commit()
        terms, checked, variants = self._offerings(
            [code for group in groups for _id, code, _title in group.courses])

        def current(code):
            return not self._not_offered(code, terms, checked, variants)

        suggestions = [{"topic": " > ".join(group.path), "courses": [
            {"code": code, "title": title} for _id, code, title in group.courses
            if current(code)][:MAX_SUGGESTED_COURSES]} for group in groups]
        suggestions = [group for group in suggestions if group["courses"]]
        return ResearchResult(
            status="ok" if suggestions else "not_found",
            data={"suggestions": suggestions, "discovery_only": True},
            limitations=[("Suggestions come from topic similarity. They are not requirements "
                          "and do not show that a course counts toward any program.")])

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
        # A course the model is weighing will be recommended or dropped; opening
        # it now saves the rejected finish that would otherwise ask for the read.
        self._open_courses(code for candidate in state.candidates
                           for code in candidate.course_codes)
        return ResearchResult(data={"state_revision": self.state.revision})

    def finish(self, value, _scope):
        if value.decision.outcome == "declined":
            self.decision = declined_decision(value.decision)
            return ResearchResult(data={"accepted": True})
        finishable_checks = {check_id: check for check_id, check in self.checks.items()
                             if check_id not in self.unavailable_preparation_check_ids}
        try:
            if self.audit_matched_lines_only:
                self._refuse_met_claims(value.decision)
            written = " ".join(block.text for block in value.decision.blocks)
            for conflict in source_conflicts(self.question):
                if not all(term in written for term in conflict["must_mention"]):
                    raise DecisionRejected(conflict["note"])
            named = {code for block in value.decision.blocks
                     for code in canonical_codes_in(block.text)}
            if self.must_name - named:
                raise DecisionRejected(
                    "the prerequisite graph lists these too; name each one and what else it "
                    f"needs: {', '.join(sorted(self.must_name - named))}")
            unsaid = {degree for degree in self.must_name_degrees
                      if not re.search(rf"\b{re.escape(degree)}\b", written)}
            if unsaid:
                raise DecisionRejected(
                    "the student named no degree and this major also exists as a "
                    f"{', '.join(sorted(unsaid))}; say in the first block that both exist")
            unnamed = set(self.unknown_question_codes()) - named
            if unnamed:
                raise DecisionRejected(
                    "the student asked about a course that does not exist; say so plainly, "
                    f"naming it: {', '.join(sorted(unnamed))}")
            validate_final(value.decision, self.state, self.store.evidence(self.run.run_id),
                           self.supplied, finishable_checks, self.entity_evidence,
                           self.artifact_references, self._known_course_codes(),
                           self._retired_courses(value.decision), self.instructions,
                           frozenset(self.unknown_question_codes()),
                           self.artifact_requested)
        except DecisionRejected as exc:
            self.rejected_decision = value.decision
            opened = self._open_courses(value.decision.recommended_course_codes)
            if not opened:
                raise
            raise DecisionRejected(
                f"{exc}; the course records for {', '.join(opened)} are now in "
                "exact_evidence_untrusted: read them and cite them, and do not "
                "say they could not be read") from None
        self.decision = value.decision
        return ResearchResult(data={"accepted": True})

    @staticmethod
    def _refuse_met_claims(decision):
        for block in decision.blocks:
            for claim in _REQUIREMENT_MET_RE.finditer(block.text):
                if not _NEGATED_RE.search(block.text[:claim.start()]):
                    raise DecisionRejected(
                        "no reviewed rules exist for this program, so a reported course cannot "
                        f"be said to meet a requirement ({claim.group()!r}); say which lines name "
                        "the course and which remain")

    def salvage(self):
        """The valid blocks of the last rejected proposal, or None.

        A run that used up its corrections has usually read the right sources
        and failed on one clause. Each block is held to the same rules alone, so
        a salvaged reply claims nothing an accepted one could not. It says less,
        which is why it is published as partial.
        """
        proposal = self.rejected_decision
        if proposal is None:
            return None
        records = self.store.evidence(self.run.run_id)
        known = self.supplied | {record.evidence_id for record in records}
        retired = self._retired_courses(proposal)
        unknown = frozenset(self.unknown_question_codes())
        kept, used = [], 0
        for block in proposal.blocks:
            single = proposal.model_copy(update={
                "outcome": "information", "completeness": "partial", "blocks": (block,),
                "candidate_ids": (), "recommended_course_codes": (), "preparation_check_ids": (),
                "preparation_claims": (), "artifact": None, "unresolved_questions": ()})
            size = len(block.text.encode("utf-8"))
            try:
                validate_final(single, self.state, records, known, {}, self.entity_evidence,
                               None, self._known_course_codes(), retired, self.instructions,
                               unknown, self.artifact_requested)
            except DecisionRejected:
                continue
            if used + size > MAX_DECISION_TEXT_BYTES:
                continue
            kept.append(block)
            used += size
        # What the master already settles is not lost with the block that said it.
        named = {code for block in kept for code in canonical_codes_in(block.text)}
        absent = [DecisionBlock(kind="limitation", text=f"There is no {code} in the Course Atlas.")
                  for code in sorted(unknown - named)]
        # A kept block that already says the course does not exist is the answer.
        if not absent and not named & unknown and not any(block.kind == "fact" for block in kept):
            return None
        kept = [*absent, *kept]
        self.supplied = known
        return proposal.model_copy(update={
            "outcome": "information", "completeness": "partial", "blocks": tuple(kept),
            "candidate_ids": (), "recommended_course_codes": (), "preparation_check_ids": (),
            "preparation_claims": (), "artifact": None})

    def _open_courses(self, codes) -> list[str]:
        """Read named courses the run has not opened, without spending a model round."""
        wanted = {normalize_code(code) for code in codes} - {None}
        unread = sorted(code for code in wanted if not self.entity_evidence.get(code))
        reads = []
        for start in range(0, min(len(unread), MAX_AUTO_READ_COURSES), MAX_COURSE_CODES):
            reads.extend(read_courses(self.conn, self.scope, unread[start:start + MAX_COURSE_CODES]))
            self.conn.commit()
        if not reads:
            return []
        # One acquisition: a tool execution owns exactly one evidence step.
        chunks = _within_read_limit(_opened_chunks(reads))
        try:
            self.acquire(chunks, refs={c.chunk_id: (r.code, r.canonical_code)
                                       for r in reads for c in r.chunks}, action="read_courses",
                         progress_key=("read_courses", tuple(r.code for r in reads)))
        except EvidenceLimit:
            return []
        return [r.code for r in reads]

    def unknown_question_codes(self) -> dict[str, list[str]]:
        """Codes the question names that are not current courses, with their nearest real codes."""
        named = course_codes_in_query(self.question or "")
        if not named:
            return {}
        known = self._known_course_codes()
        # A retired course is real; the retired-course gate says it is not offered.
        real = known | self._retired_course_codes()
        # A subject no course has is a word ("with 88", "top 10"), unless the
        # student wrote it as a code, in capitals.
        subjects = {code.split(" ")[0] for code in known}
        unknown = [code for code in named if code not in real and (
            code.split(" ")[0] in subjects
            or re.search(rf"(?<![A-Za-z]){re.escape(code.split(' ')[0])}(?![a-z])",
                         self.question or ""))]
        if not unknown:
            return {}
        # The Atlas's campus equivalence names the same course on the other
        # campus; a code paired with a current course is real, even when that
        # campus's own record is retired.
        equivalents = load_campus_equivalents(self.conn, collection_id=self.scope.collection_id)
        return {code: sorted(k for k in known if course_stem(k) == course_stem(code))[:6]
                for code in unknown if not equivalents.get(code, frozenset()) & known}

    def _known_course_codes(self) -> frozenset[str]:
        """The collection's course identities, for the identity check.

        A course the answer names must exist. The master is the authority on
        that; the run's evidence says what the run established. A course that
        exists but was not read may still be named, in a clause that says so.

        The catalog names Oxford courses the master carries no row for - a
        requirement that reads "BIOL 142 or BIOL_OX 142" publishes both. The
        campus equivalence states which ones are real, so an answer that names
        one is not naming a course that does not exist.
        """
        if self._course_codes is None:
            rows = self.conn.execute(
                queries.get("list_collection_course_codes"),
                {"collection_id": self.scope.collection_id}).fetchall()
            known = {row["code"] for row in rows}
            self._master_codes = frozenset(known)
            known.update(
                code for code in load_campus_equivalents(
                    self.conn, collection_id=self.scope.collection_id)
                if is_oxford(code)
            )
            # Catalog-named Oxford codes in current requisite text are real
            # identities even when no master row or equivalence exists yet.
            for row in self.conn.execute(
                queries.get("list_collection_course_requisite_texts"),
                {"collection_id": self.scope.collection_id},
            ).fetchall():
                for code in canonical_codes_in(row["requisites_raw"] or ""):
                    if is_oxford(code):
                        known.add(code)
            self._course_codes = frozenset(known)
        return self._course_codes

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
            ("check_preparation", "Run deterministic dependency checks, never eligibility. completed_codes is required and must reflect the student's explicit report; use [] only when the student explicitly reports no completed courses, and ask if history is unknown. In blocker alternative_groups, each inner group requires all entries and the outer list offers alternative paths. If alternative_groups_truncated is true, more paths were omitted; do not treat the shown groups as exhaustive.", PreparationInput, self.preparation, ()),
            ("audit_program", "Check a student's reported courses against a fully read program. Ask if unknown. reviewed_rules false gives only the lines naming them, never that a requirement is met.", AuditInput, self.audit, ("handle",)),
            ("suggest_courses", "Courses related to a goal, from the topic graph: suggestions, never requirements.", SuggestInput, self.suggest, ()),
            ("update_research", "Replace bounded comparison state, set active exact evidence and expected revision.", StateInput, self.update, ()),
            ("finish", "Propose typed final answer. Must be the sole tool call. Cite active evidence IDs, never raw markers. Optional artifact requests a follow-on document (printable plan, checklist or report), workbook (spreadsheet) or html (web page) when the student asks to make, keep or export one; revise requires the exact referenced revision.", FinishInput, self.finish, ()),
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
