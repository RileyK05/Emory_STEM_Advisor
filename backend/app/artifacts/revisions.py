"""Explicit-ID artifact revisions. Each operation returns a new validated spec."""

from __future__ import annotations

import json

from pydantic import TypeAdapter

from .errors import ArtifactError
from .models import (
    DeleteBlockOperation,
    DeleteCellOperation,
    DocumentOperation,
    DocumentSpec,
    HtmlArtifactSpec,
    InsertBlockOperation,
    ReplaceBlockOperation,
    SetCellOperation,
    WorkbookOperation,
    WorkbookSpec,
)

_DOCUMENT_OPERATION = TypeAdapter(DocumentOperation)
_WORKBOOK_OPERATION = TypeAdapter(WorkbookOperation)


def manualize_spec(spec, previous=None):
    """Discard client claims and carry forward evidence only for unchanged content."""
    evidence = {}
    unverified: set[str] = set()
    previous_unverified: set[str] = set()
    if previous is not None:
        raw = previous.provenance.get("unverifiedElements")
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    previous_unverified = {item for item in parsed if isinstance(item, str)}
            except ValueError:
                pass

    if isinstance(spec, DocumentSpec):
        old_blocks = {block.id: block for block in previous.blocks} if isinstance(previous, DocumentSpec) else {}
        for block in spec.blocks:
            old = old_blocks.get(block.id)
            key = block.id
            if old is None or old.model_dump(mode="json") != block.model_dump(mode="json"):
                unverified.add(key)
            else:
                if key in previous.evidence:
                    evidence[key] = previous.evidence[key]
                if key in previous_unverified:
                    unverified.add(key)
    elif isinstance(spec, WorkbookSpec):
        old_cells = {
            f"{sheet.id}/{cell.id}": cell
            for sheet in previous.sheets for cell in sheet.cells
        } if isinstance(previous, WorkbookSpec) else {}
        for sheet in spec.sheets:
            for cell in sheet.cells:
                key = f"{sheet.id}/{cell.id}"
                old = old_cells.get(key)
                if old is None or old.model_dump(mode="json") != cell.model_dump(mode="json"):
                    unverified.add(key)
                else:
                    if key in previous.evidence:
                        evidence[key] = previous.evidence[key]
                    if key in previous_unverified:
                        unverified.add(key)
        # Workbook structure and charts can affect meaning even when cell values do not.
        if isinstance(previous, WorkbookSpec) and (
            [(s.id, s.name, s.column_widths, s.freeze_panes) for s in previous.sheets]
            != [(s.id, s.name, s.column_widths, s.freeze_panes) for s in spec.sheets]
            or [c.model_dump(mode="json") for c in previous.charts]
            != [c.model_dump(mode="json") for c in spec.charts]
        ):
            unverified.update(evidence)
            evidence = {}
    elif isinstance(spec, HtmlArtifactSpec):
        unchanged = isinstance(previous, HtmlArtifactSpec) and previous.html == spec.html
        if unchanged:
            evidence = {"html": previous.evidence["html"]} if "html" in previous.evidence else {}
            if "html" in previous_unverified:
                unverified.add("html")
        else:
            unverified.add("html")

    if previous is None:
        unverified = set(_content_ids(spec))
    provenance = {"authority": "manual_draft", "manual_edit": True}
    if unverified:
        provenance["unverifiedElements"] = json.dumps(sorted(unverified), separators=(",", ":"))
    return spec.model_copy(update={"evidence": evidence, "provenance": provenance})


def _content_ids(spec) -> set[str]:
    if isinstance(spec, DocumentSpec):
        return {block.id for block in spec.blocks}
    if isinstance(spec, WorkbookSpec):
        return {f"{sheet.id}/{cell.id}" for sheet in spec.sheets for cell in sheet.cells}
    return {"html"}


def revise_document(spec: DocumentSpec, operations: list[DocumentOperation]) -> DocumentSpec:
    blocks = list(spec.blocks)
    operations = [_DOCUMENT_OPERATION.validate_python(operation) for operation in operations]
    for operation in operations:
        if isinstance(operation, InsertBlockOperation):
            if any(block.id == operation.block.id for block in blocks):
                raise ArtifactError(
                    "duplicate_id", f"Block ID already exists: {operation.block.id}"
                )
            if operation.after_id is None:
                blocks.insert(0, operation.block)
            else:
                index = next(
                    (i for i, block in enumerate(blocks) if block.id == operation.after_id), None
                )
                if index is None:
                    raise ArtifactError("unknown_block", f"No block has ID {operation.after_id}")
                blocks.insert(index + 1, operation.block)
        elif isinstance(operation, ReplaceBlockOperation):
            index = next(
                (i for i, block in enumerate(blocks) if block.id == operation.block_id), None
            )
            if index is None:
                raise ArtifactError("unknown_block", f"No block has ID {operation.block_id}")
            blocks[index] = operation.block
        elif isinstance(operation, DeleteBlockOperation):
            index = next(
                (i for i, block in enumerate(blocks) if block.id == operation.block_id), None
            )
            if index is None:
                raise ArtifactError("unknown_block", f"No block has ID {operation.block_id}")
            del blocks[index]
        else:  # pragma: no cover - discriminated Pydantic union prevents this
            raise ArtifactError("invalid_operation", "Unsupported document revision operation")
    return DocumentSpec.model_validate(
        {**spec.model_dump(), "blocks": [block.model_dump() for block in blocks]}
    )


def revise_workbook(spec: WorkbookSpec, operations: list[WorkbookOperation]) -> WorkbookSpec:
    sheets = [sheet.model_copy(deep=True) for sheet in spec.sheets]
    operations = [_WORKBOOK_OPERATION.validate_python(operation) for operation in operations]
    for operation in operations:
        sheet = next(
            (candidate for candidate in sheets if candidate.id == operation.sheet_id), None
        )
        if sheet is None:
            raise ArtifactError("unknown_sheet", f"No sheet has ID {operation.sheet_id}")
        if isinstance(operation, SetCellOperation):
            index = next(
                (i for i, cell in enumerate(sheet.cells) if cell.id == operation.cell_id), None
            )
            if index is None:
                sheet.cells.append(operation.cell)
            else:
                sheet.cells[index] = operation.cell
        elif isinstance(operation, DeleteCellOperation):
            index = next(
                (i for i, cell in enumerate(sheet.cells) if cell.id == operation.cell_id), None
            )
            if index is None:
                raise ArtifactError("unknown_cell", f"No cell has ID {operation.cell_id}")
            del sheet.cells[index]
        else:  # pragma: no cover - discriminated Pydantic union prevents this
            raise ArtifactError("invalid_operation", "Unsupported workbook revision operation")
    return WorkbookSpec.model_validate(
        {**spec.model_dump(), "sheets": [sheet.model_dump() for sheet in sheets]}
    )
