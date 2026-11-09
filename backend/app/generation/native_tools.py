"""Request-bound artifact stage tools; all writes remain staged until publication.

The pipeline calls one terminal tool per stage. A terminal tool that rejects its
input raises ``ToolInputError``; the controller hands the refusal back to the
model in the same conversation, which is the repair loop.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, ValidationError

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


class OutlineSection(ToolInputModel):
    id: str = Field(min_length=1, max_length=32, pattern=_ID_PATTERN)
    heading: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=1000)
    block_types: list[Literal["paragraph", "heading", "list", "table", "code", "quote"]] = Field(
        min_length=1, max_length=6)
    source_slots: list[int] = Field(default_factory=list, max_length=50)
    courses: list[str] = Field(default_factory=list, max_length=20)
    blocked_reasons: list[str] = Field(default_factory=list, max_length=20)


class OutlineColumn(ToolInputModel):
    key: str = Field(min_length=1, max_length=32, pattern=_ID_PATTERN)
    header: str = Field(min_length=1, max_length=80)
    type: Literal["text", "number", "date", "total"] = "text"


class OutlineSheet(ToolInputModel):
    id: str = Field(min_length=1, max_length=32, pattern=_ID_PATTERN)
    name: str = Field(min_length=1, max_length=31)
    columns: list[OutlineColumn] = Field(min_length=1, max_length=12)
    row_purpose: str = Field(min_length=1, max_length=1000)
    source_slots: list[int] = Field(default_factory=list, max_length=50)
    courses: list[str] = Field(default_factory=list, max_length=20)
    blocked_reasons: list[str] = Field(default_factory=list, max_length=20)


class OutlineChart(ToolInputModel):
    sheet_id: str = Field(min_length=1, max_length=32, pattern=_ID_PATTERN)
    type: Literal["bar", "line", "pie"] = "bar"
    title: str = Field(min_length=1, max_length=200)
    value_column: str = Field(min_length=1, max_length=32, pattern=_ID_PATTERN)
    category_column: str = Field(min_length=1, max_length=32, pattern=_ID_PATTERN)


class StageOutline(ToolInputModel):
    title: str = Field(min_length=1, max_length=200)
    sections: list[OutlineSection] = Field(default_factory=list, max_length=20)
    sheets: list[OutlineSheet] = Field(default_factory=list, max_length=20)
    charts: list[OutlineChart] = Field(default_factory=list, max_length=20)
    program: str | None = Field(default=None, max_length=200)
    selected_courses: list[str] = Field(default_factory=list, max_length=40)
    completed_courses: list[str] = Field(default_factory=list, max_length=100)


class SectionRow(ToolInputModel):
    values: list[str | int | float | bool | None] = Field(max_length=12)
    source_slots: list[int] = Field(default_factory=list, max_length=50)


class StageSection(ToolInputModel):
    blocks: list[dict[str, Any]] = Field(default_factory=list, max_length=40)
    html: str = Field(default="", max_length=64_000)
    rows: list[SectionRow] = Field(default_factory=list, max_length=200)
    courses: list[str] = Field(default_factory=list, max_length=20)
    bindings: list[ElementBinding] = Field(default_factory=list, max_length=1000)


class EditOperation(ToolInputModel):
    action: Literal["keep", "replace", "insert_after", "delete"]
    section_id: str = Field(min_length=1, max_length=32, pattern=_ID_PATTERN)
    purpose: str = Field(default="", max_length=1000)
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
