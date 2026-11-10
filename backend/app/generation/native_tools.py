"""Request-bound artifact stage tools; all writes remain staged until publication.

The pipeline calls one terminal tool per stage. A terminal tool that rejects its
input raises ``ToolInputError``; the controller hands the refusal back to the
model in the same conversation, which is the repair loop.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, Field, ValidationError, model_validator

from app.generation.artifact_candidate import ElementBinding
from app.tools.registry import (
    ToolCapability,
    ToolInputModel,
    ToolOutputModel,
    ToolScope,
    ToolSpec,
)

OUTLINE_TOOL = "advisor_stage_outline"
SECTION_TOOL = "advisor_stage_section"
EDITS_TOOL = "advisor_stage_edits"

BLOCK_TYPES = ("paragraph", "heading", "list", "table", "code", "quote")
COLUMN_TYPES = ("text", "number", "date", "total")

_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
_NOTE_CHARS = 600


def _short_note(value):
    """Keep a planning note to one paragraph.

    A purpose is the outline's instruction to the call that writes the
    section; the student never sees it. A model that writes the section into
    its purpose would otherwise be refused for length on every retry, so the
    note is cut at a sentence or word boundary instead.
    """
    if not isinstance(value, str) or len(value) <= _NOTE_CHARS:
        return value
    cut = value[:_NOTE_CHARS]
    end = max(cut.rfind(". "), cut.rfind("; "))
    return (cut[:end + 1] if end > _NOTE_CHARS // 2 else cut.rsplit(" ", 1)[0]).strip()


Note = Annotated[str, BeforeValidator(_short_note),
                 Field(description="One or two sentences on what this part covers.")]


def _ignore_outline_courses(value):
    if not isinstance(value, list):
        return value
    return [{key: field for key, field in item.items() if key != "courses"}
            if isinstance(item, dict) else item for item in value]


class OutlineSection(ToolInputModel):
    id: str = Field(min_length=1, max_length=32, pattern=_ID_PATTERN)
    heading: str = Field(min_length=1, max_length=200)
    purpose: Note = Field(min_length=1)
    block_types: list[Literal["paragraph", "heading", "list", "table", "code", "quote"]] = Field(
        min_length=1, max_length=6)
    source_slots: list[int] = Field(default_factory=list, max_length=50)
    courses: list[str] = Field(default_factory=list)
    blocked_reasons: list[str] = Field(default_factory=list)


class OutlineColumn(ToolInputModel):
    key: str = Field(min_length=1, max_length=32, pattern=_ID_PATTERN)
    header: str = Field(min_length=1, max_length=80)
    type: Literal["text", "number", "date", "total"] = "text"


class OutlineSheet(ToolInputModel):
    id: str = Field(min_length=1, max_length=32, pattern=_ID_PATTERN)
    name: str = Field(min_length=1, max_length=31)
    columns: list[OutlineColumn] = Field(min_length=1, max_length=12)
    row_purpose: Note = Field(min_length=1)
    source_slots: list[int] = Field(default_factory=list, max_length=50)
    courses: list[str] = Field(default_factory=list)
    blocked_reasons: list[str] = Field(default_factory=list)


class OutlineChart(ToolInputModel):
    sheet_id: str = Field(min_length=1, max_length=32, pattern=_ID_PATTERN)
    type: Literal["bar", "line", "pie"] = "bar"
    title: str = Field(min_length=1, max_length=200)
    value_column: str = Field(min_length=1, max_length=32, pattern=_ID_PATTERN)
    category_column: str = Field(min_length=1, max_length=32, pattern=_ID_PATTERN)


class StageOutline(ToolInputModel):
    title: str = Field(min_length=1, max_length=200)
    sections: Annotated[list[OutlineSection], BeforeValidator(_ignore_outline_courses)] = Field(
        default_factory=list, max_length=20)
    sheets: Annotated[list[OutlineSheet], BeforeValidator(_ignore_outline_courses)] = Field(
        default_factory=list, max_length=20)
    charts: list[OutlineChart] = Field(default_factory=list, max_length=20)
    program: str | None = Field(default=None, max_length=200)
    selected_courses: list[str] = Field(default_factory=list, max_length=40)
    completed_courses: list[str] = Field(default_factory=list, max_length=100)


class SectionRow(ToolInputModel):
    values: list[str | int | float | bool | None] = Field(max_length=12)
    source_slots: list[int] = Field(default_factory=list, max_length=50)


class StageSection(ToolInputModel):
    """The checked draft of one section, whatever kind of artifact it belongs to."""
    blocks: list[dict[str, Any]] = Field(default_factory=list, max_length=41)
    html: str = Field(default="", max_length=64_000)
    rows: list[SectionRow] = Field(default_factory=list, max_length=200)
    bindings: list[ElementBinding] = Field(default_factory=list, max_length=1000)


class _SectionInput(ToolInputModel):
    """What the model sends for one section. Each artifact kind offers its own fields."""

    @model_validator(mode="before")
    @classmethod
    def _drop_empty_extras(cls, data):
        # A provider fills in every field it has seen, including ones another kind
        # uses. An empty value there means "not used", so it is dropped, not refused.
        if isinstance(data, dict):
            return {key: value for key, value in data.items()
                    if key in cls.model_fields or value not in (None, "", [], {})}
        return data


class StageDocumentSection(_SectionInput):
    # A block may carry `source_slots`: the numbered slots it uses.
    blocks: list[dict[str, Any]] = Field(default_factory=list, max_length=41)
    bindings: list[ElementBinding] = Field(default_factory=list, max_length=1000)

    def draft(self) -> StageSection:
        return StageSection(blocks=self.blocks, bindings=self.bindings)


class StageHtmlSection(_SectionInput):
    html: str = Field(default="", max_length=64_000)
    source_slots: list[int] = Field(default_factory=list, max_length=50)

    @model_validator(mode="before")
    @classmethod
    def _fold_bindings(cls, data):
        # A page has one piece of content, so any bindings it sends are just its slots.
        if isinstance(data, dict) and isinstance(data.get("bindings"), list):
            data = dict(data)
            slots = list(data.get("source_slots") or [])
            for binding in data.pop("bindings"):
                if isinstance(binding, dict) and isinstance(binding.get("source_slots"), list):
                    slots += [slot for slot in binding["source_slots"] if slot not in slots]
            data["source_slots"] = slots
        return data

    def draft(self) -> StageSection:
        bindings = ([ElementBinding(element_id="html", source_slots=self.source_slots)]
                    if self.source_slots else [])
        return StageSection(html=self.html, bindings=bindings)


class StageWorkbookSection(_SectionInput):
    rows: list[SectionRow] = Field(default_factory=list, max_length=200)

    def draft(self) -> StageSection:
        return StageSection(rows=self.rows)


SECTION_INPUTS = {"document": StageDocumentSection, "html": StageHtmlSection,
                  "workbook": StageWorkbookSection}


class StagePlanNote(ToolInputModel):
    note: str = Field(default="", max_length=_NOTE_CHARS)


class EditOperation(ToolInputModel):
    action: Literal["keep", "replace", "insert_after", "delete"]
    section_id: str = Field(min_length=1, max_length=32, pattern=_ID_PATTERN)
    purpose: Note = ""
    section: OutlineSection | None = None
    sheet: OutlineSheet | None = None


class StageEdits(ToolInputModel):
    title: str = Field(min_length=1, max_length=200)
    operations: list[EditOperation] = Field(min_length=1, max_length=40)


class StageAck(ToolOutputModel):
    accepted: bool = True


def candidate_problem(exc: Exception) -> str:
    """A short, model-readable reason a stage input was refused."""
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'input'}: {error['msg']}"
            for error in exc.errors()[:5]
        )
    return str(exc)[:500]


def stage_spec(tool_id: str, description: str, input_model, handler, *,
               max_output_bytes: int = 4_000) -> ToolSpec:
    return ToolSpec(
        tool_id=tool_id,
        description=description,
        input_model=input_model,
        output_model=StageAck,
        capability=ToolCapability.ARTIFACT_WRITE,
        handler=handler,
        max_output_bytes=max_output_bytes,
    )


def stage_scope(principal_id: str, check_cancelled) -> ToolScope:
    return ToolScope(
        principal_id=principal_id,
        capabilities=frozenset({ToolCapability.ARTIFACT_WRITE}),
        resource_ids=frozenset(),
        check_cancelled=check_cancelled,
    )


def validate_generation_size(spec):
    from app.artifacts.models import DocumentSpec, WorkbookSpec

    if (isinstance(spec, DocumentSpec) and len(spec.blocks) > 200
            or isinstance(spec, WorkbookSpec) and sum(len(s.cells) for s in spec.sheets) > 1_000):
        raise ValueError("artifact exceeds the generation plan")
