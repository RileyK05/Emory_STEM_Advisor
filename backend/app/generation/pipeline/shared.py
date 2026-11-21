"""Helpers and records more than one stage of the artifact pipeline uses."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import UUID

from pydantic import TypeAdapter

from app.advising.research_repository import (
    build_scope,
    read_courses,
    read_program,
)
from app.advising.research_store import ResearchStore
from app.artifacts.models import (
    DocumentBlock,
    DocumentSpec,
    HtmlArtifactSpec,
    WorkbookSpec,
)
from app.atlas.requisites import canonical_codes_in
from app.db import queries
from app.generation.native_tools import (
    OutlineSection,
    OutlineSheet,
)
from app.llm.base import GenerationResult, ToolCall
from app.programs.naming import (
    choose_program_title,
)
from app.util.digest import json_digest

_BLOCK_ADAPTER: TypeAdapter = TypeAdapter(DocumentBlock)


_MAX_WORKBOOK_ROWS = 200


_MAX_GROUNDED_COURSES = 40


_MAX_PROGRAM_ITEMS = 120


# Opencode's agent loop allows an output-limit response two synthetic
# continuations before it gives up. The stage keeps the same bound, and gives
# transient provider failures their own retries so a dropped connection never
# consumes a correction or a continuation.
_OUTPUT_LIMIT_CONTINUATIONS = 2


_TRANSIENT_RETRIES = 2


@dataclass(frozen=True)
class ArtifactSlot:
    """One numbered source slot an artifact section may cite."""

    chunk_id: UUID
    source_id: UUID
    source_name: str
    locator_label: str
    text: str
    entity: tuple[str, str] | None = None


@dataclass
class Grounding:
    slots: list[ArtifactSlot]
    trace_id: UUID
    collection_id: UUID


@dataclass
class AssembledSection:
    section: OutlineSection | OutlineSheet
    blocks: list[dict[str, Any]] = field(default_factory=list)
    html: str = ""
    sheet: dict[str, Any] | None = None
    evidence: dict[str, list[dict]] = field(default_factory=dict)
    text: str = ""
    skip_elements: set[str] = field(default_factory=set)


@dataclass
class PipelineResult:
    spec: DocumentSpec | WorkbookSpec | HtmlArtifactSpec


class _StageProvider:
    """Namespace model call IDs per stage so the operation ledger never collides."""

    def __init__(self, provider, prefix: str):
        self.provider = provider
        self.prefix = json_digest(prefix)[:12]

    def generate(self, request):
        result: GenerationResult = self.provider.generate(request)
        if not result.tool_calls:
            return result
        calls = []
        for call in result.tool_calls:
            call_id = f"{self.prefix}:{call.id}"
            calls.append(ToolCall(call_id[:256], call.name, call.arguments))
        return replace(result, tool_calls=tuple(calls))


def _cell_type(value):
    if isinstance(value, bool) or value is None:
        return "text"
    if isinstance(value, (int, float)):
        return "number"
    return "text"


@dataclass
class _Lane:
    """One connection and operation handle that model calls run on.

    Parallel section calls each get their own lane: a psycopg connection and a
    budget reservation must never be shared between threads. Every lane points
    at the run's single persisted operation, whose row locks keep concurrent
    reservations inside one budget.
    """

    conn: object
    operation: object
    started: float


class ArtifactTargetError(ValueError):
    """The request names sources, a revision, or evidence that cannot support the artifact."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def load_research_grounding(conn, workspace_key: str, collection_id: UUID,
                            run_id: UUID, source_ids) -> Grounding:
    """The research run's evidence, in its stored order, as numbered slots."""
    from app.advising.research_store import RunNotFound

    try:
        store = ResearchStore(conn, workspace_key)
        run = store.get(run_id)
    except RunNotFound as exc:
        raise ArtifactTargetError("no_grounding", "The research run could not be found.") from exc
    if run.collection_id != collection_id:
        raise ArtifactTargetError("no_grounding", "The research run used a different collection.")
    records = store.evidence(run_id)
    slots = [
        ArtifactSlot(
            chunk_id=UUID(str(record.chunk_id)),
            source_id=UUID(str(record.source_id)),
            source_name=record.source_name,
            locator_label=record.locator_label,
            text=record.exact_text,
        )
        for record in records
    ]
    trace_id = run.root_trace_id
    if trace_id is None:
        raise ArtifactTargetError("no_grounding", "The research run has no trace to ground on.")
    return Grounding(slots=slots, trace_id=trace_id, collection_id=collection_id)


def add_database_slots(conn, *, collection_id: UUID, workspace_key: str, source_ids,
                       slots: list[ArtifactSlot], prompt: str,
                       program_titles: list[str]) -> tuple[list[ArtifactSlot], str | None]:
    """Append exact course records and program requirement lines as fixed slots."""
    scope = build_scope(conn, collection_id=collection_id, workspace_key=workspace_key,
                        source_ids=source_ids)
    conn.commit()
    codes = list(dict.fromkeys(canonical_codes_in(prompt)))[:_MAX_GROUNDED_COURSES]
    seen = {slot.chunk_id for slot in slots}
    added = list(slots)
    entities: dict[UUID, tuple[str, str] | None] = {}

    def include(chunk, entity: tuple[str, str]):
        if entities.setdefault(chunk.chunk_id, entity) != entity:
            entities[chunk.chunk_id] = None
        if chunk.chunk_id not in seen:
            seen.add(chunk.chunk_id)
            added.append(_slot_from_chunk(chunk, scope))

    program_title = choose_program_title(prompt, program_titles)
    if codes:
        pending = list(codes)
        while pending:
            batch, pending = pending[:4], pending[4:]
            for read in read_courses(conn, scope, batch):
                for chunk in read.chunks:
                    include(chunk, ("course", read.code))
            conn.commit()
    if program_title:
        row = conn.execute(
            queries.get("artifact_program_by_title"),
            {"collection_id": collection_id, "title": program_title,
             "title_pattern": f"%{program_title}%"},
        ).fetchone()
        if row is not None:
            read = read_program(conn, scope, row["program_id"])
            conn.commit()
            for item in read.items[:_MAX_PROGRAM_ITEMS]:
                for chunk, _start, _end in item.line.support:
                    include(chunk, ("program", program_title))
            for field in read.fields.values():
                for chunk, _start, _end in field.support:
                    include(chunk, ("program", program_title))
    return [replace(slot, entity=entities.get(slot.chunk_id)) for slot in added], program_title


def _slot_from_chunk(chunk, scope) -> ArtifactSlot:
    return ArtifactSlot(
        chunk_id=chunk.chunk_id, source_id=chunk.source_id,
        source_name=scope.sources[chunk.source_id].filename,
        locator_label=chunk.locator_label, text=chunk.text,
    )


def list_program_titles(conn, collection_id: UUID) -> list[str]:
    rows = conn.execute(queries.get("list_programs"), {"collection_id": collection_id}).fetchall()
    conn.commit()
    return [row["title"] for row in rows]


def _stage_messages(system: str, payload: dict) -> list[dict]:
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


# A degree ("BA", "B.S.", "bachelor") names a major, so "the Physics minor and the
# Physics and Astronomy BA" asks for both kinds (round 8).
_PROGRAM_KIND_ASKED = re.compile(r"\b(minor|major|b\.?a\.?|b\.?s\.?|bba|bachelor)s?\b",
                                 re.IGNORECASE)


_CREDITS_IN_RECORD = re.compile(
    r"Credits:\s*(\d+(?:\.\d+)?)(?:\s*-\s*(\d+(?:\.\d+)?))?", re.IGNORECASE)


def _stated_credits(text: str) -> float | None:
    """The credit hours a course record states, or None when it states none."""
    match = _CREDITS_IN_RECORD.search(text or "")
    values = [float(value) for value in match.groups() if value] if match else []
    return max(values) if values else None


_NON_REQUIRED_SECTION = re.compile(
    r"\b(?:electives?|choices?|options?|tracks?|concentrations?|emphases|emphasis|"
    r"breadth|distribution|general education|ger|survey|honors?|colloqui(?:um|a)|"
    r"seminars?|independent study|research|internship|capstone|recommended|"
    r"suggested|related|cognate|notes?|footnotes?|prerequisites?)\b",
    re.IGNORECASE,
)


_NON_REQUIRED_TEXT = re.compile(
    r"\b(?:prerequisites?|cross[- ]listed|may be counted|ap score|transfer|"
    r"notes?|electives?|concentration|breadth|survey|honors?|colloqui(?:um|a)|"
    r"seminars?|recommended)\b",
    re.IGNORECASE,
)


def _required_program_codes(items, course_exists) -> list[str]:
    """Course codes on a program's required lines, not its elective/notes lines."""
    codes: list[str] = []
    for item in items:
        if item["choose"] is not None:
            continue
        section = str(item["section"] or "")
        text = str(item["text"] or "")
        if _NON_REQUIRED_SECTION.search(section) or _NON_REQUIRED_TEXT.search(text):
            continue
        choices = [str(code) for code in (item["courses"] or []) if code]
        if not choices:
            continue
        if len(choices) > 3 and " and " not in text.casefold():
            continue
        # "BIOL 495 (Honors) or BIOL 499R" is one requirement, not both.
        if re.search(r"\bor\b", text, re.IGNORECASE) and len(choices) > 1:
            choices = choices[:1]
        for code in choices:
            if course_exists(code) and code not in codes:
                codes.append(code)
    return codes


def _program_plan_codes(items, course_exists, *, target: int = 16) -> list[str]:
    """Core required courses plus representative elective choices up to target."""
    codes = _required_program_codes(items, course_exists)
    if len(codes) >= target:
        return codes
    for item in items:
        if len(codes) >= target:
            break
        text = str(item["text"] or "")
        if re.search(r"(?i)\b(?:prerequisites?|cross[- ]listed|may be counted|"
                     r"ap score|transfer|notes?)\b", text):
            continue
        choices = [str(code) for code in (item["courses"] or [])
                   if code and course_exists(str(code))]
        if not choices:
            continue
        if re.search(r"\bor\b", text, re.IGNORECASE) and item["choose"] is None:
            # Already represented by a choice on this "or" line; do not take both.
            if any(c in codes for c in choices):
                continue
            limit = 1
        elif item["choose"] is not None:
            limit = max(1, int(item["choose"]))
        else:
            limit = target - len(codes)
        added = sum(1 for c in choices if c in codes)
        for code in choices:
            if added >= limit or len(codes) >= target:
                break
            if code not in codes:
                codes.append(code)
                added += 1
    return codes


_BUILDER_LABELS = {"checklist": "Requirements checklist", "overview": "Program overview",
                   "comparison": "Program comparison", "tracker": "Credit tracker"}


def _check_program_kind(brief: str, title: str) -> None:
    """A request naming only a minor, or only a major, gets that kind of program."""
    asked = {"minor" if word.casefold() == "minor" else "major"
             for word in _PROGRAM_KIND_ASKED.findall(brief)}
    if len(asked) != 1:
        return  # both, or neither: the request does not decide it
    wanted = asked.pop()
    if (wanted == "minor") != ("minor" in title.casefold()):
        raise ValueError(f"the student asked about a {wanted}, and {title} is not one; "
                         f"choose the {wanted} from the supplied program titles")


def _block_text(block: dict) -> str:
    if block.get("text"):
        return str(block["text"])
    if block.get("items"):
        return " ".join(str(item) for item in block["items"])
    if block.get("rows"):
        return " ".join(str(cell) for row in block["rows"] for cell in row)
    if block.get("headers"):
        return " ".join(str(cell) for cell in block["headers"])
    return ""


def _slot_refs(slots, grounding_slots):
    refs = []
    for slot in dict.fromkeys(slots):
        if 1 <= slot <= len(grounding_slots):
            source = grounding_slots[slot - 1]
            refs.append({
                "chunk_id": str(source.chunk_id), "source_id": str(source.source_id),
                "source_name": source.source_name, "locator_label": source.locator_label,
                "text_snapshot": source.text,
            })
    return refs


def _coerce_cell(value, column_type: str):
    if column_type == "number":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{value!r} is not a number") from exc
        return int(number) if number.is_integer() else number
    if column_type == "date":
        return str(value)
    return value


def _sheet_text(sheet: dict) -> str:
    return "\n".join([str(sheet.get("name") or "")] + [
        str(cell.get("value") or "") for cell in sheet.get("cells", [])])
