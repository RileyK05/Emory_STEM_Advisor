"""Typed, bounded artifact specs. These models contain data, never executable Python."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SpecModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False, allow_inf_nan=False)


class ArtifactReference(SpecModel):
    chunk_id: UUID
    source_id: UUID
    source_name: str = Field(max_length=500)
    locator_label: str = Field(max_length=500)
    text_snapshot: str = Field(max_length=100_000)


class DocumentBlockBase(SpecModel):
    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class ParagraphBlock(DocumentBlockBase):
    type: Literal["paragraph"] = "paragraph"
    text: str = Field(max_length=100_000)


class HeadingBlock(DocumentBlockBase):
    type: Literal["heading"] = "heading"
    level: int = Field(ge=1, le=6)
    text: str = Field(min_length=1, max_length=10_000)

    @field_validator("text")
    @classmethod
    def single_line(cls, value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("heading text must be a single line")
        return value


class ListBlock(DocumentBlockBase):
    type: Literal["list"] = "list"
    ordered: bool = False
    items: list[str] = Field(min_length=1, max_length=10_000)

    @field_validator("items")
    @classmethod
    def bounded_items(cls, value: list[str]) -> list[str]:
        if any(len(item) > 20_000 for item in value):
            raise ValueError("list item exceeds 20000 characters")
        if any("\n" in item or "\r" in item for item in value):
            raise ValueError("list items must be single lines")
        return value


class CodeBlock(DocumentBlockBase):
    type: Literal["code"] = "code"
    text: str = Field(max_length=100_000)
    language: str | None = Field(default=None, max_length=64)

    @field_validator("language")
    @classmethod
    def single_line(cls, value: str | None) -> str | None:
        if value is not None and ("\n" in value or "\r" in value):
            raise ValueError("code fence language must be a single line")
        return value


class QuoteBlock(DocumentBlockBase):
    type: Literal["quote"] = "quote"
    text: str = Field(max_length=100_000)


class TableBlock(DocumentBlockBase):
    type: Literal["table"] = "table"
    headers: list[str] = Field(min_length=1, max_length=256)
    rows: list[list[str]] = Field(max_length=20_000)

    @model_validator(mode="after")
    def rectangular(self) -> TableBlock:
        if any(len(row) != len(self.headers) for row in self.rows):
            raise ValueError("table rows must have the same number of cells as headers")
        if any(len(cell) > 20_000 for row in self.rows for cell in row):
            raise ValueError("table cell exceeds 20000 characters")
        return self


DocumentBlock = Annotated[
    ParagraphBlock | HeadingBlock | ListBlock | TableBlock | CodeBlock | QuoteBlock,
    Field(discriminator="type"),
]


class PageLayoutSpec(SpecModel):
    width_inches: float = Field(default=8.5, gt=1, le=22)
    height_inches: float = Field(default=11, gt=1, le=22)
    margin_top_inches: float = Field(default=1, ge=0, le=5)
    margin_bottom_inches: float = Field(default=1, ge=0, le=5)
    margin_left_inches: float = Field(default=1, ge=0, le=5)
    margin_right_inches: float = Field(default=1, ge=0, le=5)


class DocumentSpec(SpecModel):
    kind: Literal["document"] = "document"
    title: str = Field(min_length=1, max_length=500)
    blocks: list[DocumentBlock] = Field(max_length=20_000)
    page_layout: PageLayoutSpec = Field(default_factory=PageLayoutSpec)
    evidence: dict[str, list[ArtifactReference]] = Field(default_factory=dict, max_length=20_000)
    provenance: dict[str, str | int | float | bool | None] = Field(
        default_factory=dict, max_length=100
    )

    @model_validator(mode="after")
    def bounded_content(self) -> DocumentSpec:
        total = len(self.title) + sum(len(getattr(block, "text", "")) for block in self.blocks)
        total += sum(
            sum(map(len, block.items)) for block in self.blocks if isinstance(block, ListBlock)
        )
        total += sum(
            sum(map(len, block.headers)) + sum(sum(map(len, row)) for row in block.rows)
            for block in self.blocks
            if isinstance(block, TableBlock)
        )
        if total > 2_000_000:
            raise ValueError("document spec exceeds 2 MiB of text")
        return self

    @model_validator(mode="after")
    def unique_block_ids(self) -> DocumentSpec:
        ids = [block.id for block in self.blocks]
        if len(ids) != len(set(ids)):
            raise ValueError("document block IDs must be unique")
        return self


class CellStyle(SpecModel):
    bold: bool = False
    italic: bool = False
    font_color: str | None = Field(default=None, pattern=r"^([A-Fa-f0-9]{6}|[A-Fa-f0-9]{8})$")
    fill_color: str | None = Field(default=None, pattern=r"^([A-Fa-f0-9]{6}|[A-Fa-f0-9]{8})$")
    number_format: str | None = Field(default=None, max_length=128)
    wrap_text: bool | None = Field(default=None, strict=True)


CellScalar = str | int | float | bool | datetime | date | None


class WorkbookCell(SpecModel):
    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    row: int = Field(ge=1, le=1_048_576)
    column: int = Field(ge=1, le=16_384)
    value: CellScalar = None
    value_type: Literal["date", "datetime"] | None = None
    formula: str | None = Field(default=None, max_length=32_767)
    style: CellStyle = Field(default_factory=CellStyle)

    @model_validator(mode="before")
    @classmethod
    def preserve_date_type(cls, payload):
        if isinstance(payload, dict):
            payload = dict(payload)
            value = payload.get("value")
            if isinstance(value, datetime):
                payload["value_type"] = "datetime"
            elif isinstance(value, date):
                payload["value_type"] = "date"
            elif isinstance(value, str) and payload.get("value_type"):
                converter = datetime.fromisoformat if payload["value_type"] == "datetime" else date.fromisoformat
                payload["value"] = converter(value)
        return payload

    @model_validator(mode="after")
    def formula_has_prefix(self) -> WorkbookCell:
        if self.formula is not None and not self.formula.startswith("="):
            raise ValueError("formula must begin with =")
        if self.formula is not None and self.value is not None:
            raise ValueError("formula cells cannot also have a literal value")
        if self.value_type == "datetime" and not isinstance(self.value, datetime):
            raise ValueError("datetime cells require a datetime value")
        if self.value_type == "date" and (not isinstance(self.value, date)
                                          or isinstance(self.value, datetime)):
            raise ValueError("date cells require a date value")
        return self


class WorkbookChart(SpecModel):
    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    type: Literal["bar", "line", "pie"]
    title: str = Field(max_length=500)
    sheet_id: str = Field(min_length=1, max_length=128)
    data_range: str = Field(pattern=r"^[A-Z]{1,3}[1-9][0-9]*:[A-Z]{1,3}[1-9][0-9]*$")
    categories_range: str | None = Field(
        default=None, pattern=r"^[A-Z]{1,3}[1-9][0-9]*:[A-Z]{1,3}[1-9][0-9]*$"
    )
    titles_from_data: bool = True


class WorkbookSheet(SpecModel):
    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    name: str = Field(min_length=1, max_length=31)
    cells: list[WorkbookCell] = Field(max_length=100_000)
    column_widths: dict[str, float] = Field(default_factory=dict, max_length=512)
    row_heights: dict[Annotated[int, Field(ge=1, le=1_048_576)], Annotated[float, Field(gt=0, le=409)]] = Field(default_factory=dict, max_length=10_000)
    freeze_panes: str | None = Field(default=None, pattern=r"^[A-Z]{1,3}[1-9][0-9]*$")

    @field_validator("name")
    @classmethod
    def valid_sheet_name(cls, value: str) -> str:
        if (
            any(char in value for char in ":\\/?*[]")
            or value.startswith("'")
            or value.endswith("'")
        ):
            raise ValueError("worksheet name contains unsupported Excel characters")
        return value

    @model_validator(mode="after")
    def unique_cells(self) -> WorkbookSheet:
        ids = [cell.id for cell in self.cells]
        coords = [(cell.row, cell.column) for cell in self.cells]
        if len(ids) != len(set(ids)) or len(coords) != len(set(coords)):
            raise ValueError("cell IDs and coordinates must be unique within a sheet")
        return self


class WorkbookSpec(SpecModel):
    kind: Literal["workbook"] = "workbook"
    title: str = Field(min_length=1, max_length=500)
    sheets: list[WorkbookSheet] = Field(min_length=1, max_length=100)
    charts: list[WorkbookChart] = Field(default_factory=list, max_length=100)
    evidence: dict[str, list[ArtifactReference]] = Field(default_factory=dict, max_length=100_000)
    provenance: dict[str, str | int | float | bool | None] = Field(
        default_factory=dict, max_length=100
    )

    @model_validator(mode="after")
    def unique_names_ids_and_chart_sheets(self) -> WorkbookSpec:
        if len({sheet.id for sheet in self.sheets}) != len(self.sheets):
            raise ValueError("sheet IDs must be unique")
        if len({sheet.name.casefold() for sheet in self.sheets}) != len(self.sheets):
            raise ValueError("worksheet names must be unique, case-insensitively")
        if any(chart.sheet_id not in {sheet.id for sheet in self.sheets} for chart in self.charts):
            raise ValueError("chart refers to an unknown sheet ID")
        if sum(len(sheet.cells) for sheet in self.sheets) > 100_000:
            raise ValueError("workbook exceeds 100000 cells")
        if (
            sum(
                len(str(cell.value or "")) + len(cell.formula or "")
                for sheet in self.sheets
                for cell in sheet.cells
            )
            > 10_000_000
        ):
            raise ValueError("workbook spec exceeds 10 MiB of cell text")
        return self


class HtmlArtifactSpec(SpecModel):
    kind: Literal["html"] = "html"
    title: str = Field(min_length=1, max_length=500)
    html: str = Field(min_length=1, max_length=2_000_000)
    evidence: dict[str, list[ArtifactReference]] = Field(default_factory=dict, max_length=100)
    provenance: dict[str, str | int | float | bool | None] = Field(
        default_factory=dict, max_length=100
    )


class InsertBlockOperation(SpecModel):
    type: Literal["insert_block"]
    after_id: str | None = None
    block: DocumentBlock


class ReplaceBlockOperation(SpecModel):
    type: Literal["replace_block"]
    block_id: str
    block: DocumentBlock

    @model_validator(mode="after")
    def same_id(self) -> ReplaceBlockOperation:
        if self.block_id != self.block.id:
            raise ValueError("replacement block ID must equal block_id")
        return self


class DeleteBlockOperation(SpecModel):
    type: Literal["delete_block"]
    block_id: str


DocumentOperation = Annotated[
    InsertBlockOperation | ReplaceBlockOperation | DeleteBlockOperation, Field(discriminator="type")
]


class SetCellOperation(SpecModel):
    type: Literal["set_cell"]
    sheet_id: str
    cell_id: str
    cell: WorkbookCell

    @model_validator(mode="after")
    def same_id(self) -> SetCellOperation:
        if self.cell_id != self.cell.id:
            raise ValueError("replacement cell ID must equal cell_id")
        return self


class DeleteCellOperation(SpecModel):
    type: Literal["delete_cell"]
    sheet_id: str
    cell_id: str


WorkbookOperation = Annotated[SetCellOperation | DeleteCellOperation, Field(discriminator="type")]
