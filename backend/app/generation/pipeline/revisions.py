"""Revising an existing artifact: the prior outline and the edits to it."""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from app.generation.native_tools import (
    EDITS_TOOL,
    EditOperation,
    OutlineChart,
    OutlineColumn,
    OutlineSection,
    OutlineSheet,
    StageAck,
    StageEdits,
    StageOutline,
    candidate_problem,
)
from app.generation.pipeline.shared import (
    AssembledSection,
    _block_text,
    _sheet_text,
    _stage_messages,
)
from app.jobs.store import SafeJobError
from app.tools.registry import ToolInputError


def _first_sentence(text: str) -> str:
    clean = " ".join(str(text or "").split())
    if not clean:
        return ""
    match = re.search(r"[.!?](?:\s|$)", clean)
    return clean[:match.end()] if match else clean[:240]


def _group_document_blocks(blocks: list[dict]) -> dict[str, list[dict]]:
    prefixed = [block for block in blocks
                if isinstance(block.get("id"), str) and ":" in block["id"]]
    if prefixed and len(prefixed) == len(blocks):
        groups: dict[str, list[dict]] = {}
        for block in blocks:
            groups.setdefault(block["id"].split(":", 1)[0], []).append(block)
        return groups
    groups = {}
    current = "section1"
    groups[current] = []
    for block in blocks:
        if block.get("type") == "heading" and groups[current]:
            current = f"section{len(groups) + 1}"
            groups[current] = []
        groups[current].append(block)
    return groups


def _evidence_for_blocks(evidence: dict, ids) -> dict[str, list[dict]]:
    wanted = set(ids)
    return {key: refs for key, refs in evidence.items() if key in wanted}


def _evidence_for_cells(evidence: dict, sheet: dict) -> dict[str, list[dict]]:
    prefix = f"{sheet.get('id')}/"
    return {key: refs for key, refs in evidence.items() if key.startswith(prefix)}


def _columns_from_sheet(sheet: dict) -> list:

    header_cells = sorted((cell for cell in sheet.get("cells", []) if cell.get("row") == 1),
                          key=lambda cell: cell.get("column", 0))
    if not header_cells:
        return [OutlineColumn(key="c1", header="Item", type="text")]
    cells = sheet.get("cells", [])
    columns = []
    for cell in header_cells:
        position = cell.get("column")
        value = cell.get("value")
        if cell.get("formula") is not None:
            value = None
        # A prior sheet's column type is part of what the student keeps. A
        # formula column is the code-built total; numeric data is a number.
        data = [item for item in cells
                if item.get("column") == position and item.get("row") != 1]
        if any(item.get("formula") is not None for item in data):
            column_type = "total"
        elif (any(isinstance(item.get("value"), (int, float))
                  and not isinstance(item.get("value"), bool) for item in data)
              and not any(item.get("value") is not None
                          and not (isinstance(item.get("value"), (int, float))
                                   and not isinstance(item.get("value"), bool))
                          for item in data)):
            column_type = "number"
        else:
            column_type = "text"
        columns.append(OutlineColumn(
            key=f"c{len(columns) + 1}", header=str(value or f"Column {len(columns) + 1}")[:80],
            type=column_type))
    return columns


def _inherit_columns(prior: list, columns: list) -> list:
    """Keep a prior sheet's column types when an edit rewrites the sheet.

    A revision's model sees headings, not the column schema, so it often writes
    a total or credit column as text. The prior type is the student's data.
    """
    by_key = {column.key: column for column in prior}
    by_header = {column.header.casefold(): column for column in prior}
    inherited = []
    for position, column in enumerate(columns):
        match = by_header.get(column.header.casefold()) or by_key.get(column.key)
        if match is None and len(columns) == len(prior):
            match = prior[position]
        if match is not None:
            column = column.model_copy(update={
                "key": match.key or column.key, "type": match.type})
        inherited.append(column)
    return inherited


def _range_column(value) -> int | None:
    """The 1-based column a range such as "B1:B9" starts in."""
    match = re.match(r"([A-Z]+)", str(value or "").upper())
    if match is None:
        return None
    position = 0
    for letter in match.group(1):
        position = position * 26 + (ord(letter) - 64)
    return position


def _prior_charts(spec: dict | None, sheets: list) -> list:
    """Rebuild a prior workbook's charts from its stored ranges and headers.

    A revision edits sheets, not charts. Keeping the chart means matching its
    value and category columns back to the current outline's columns by header.
    """
    by_id = {str(sheet.get("id")): sheet for sheet in (spec or {}).get("sheets", [])}
    outlines = {sheet.id: sheet for sheet in sheets}
    charts = []
    for chart in (spec or {}).get("charts", []):
        sheet_id = str(chart.get("sheet_id") or "")
        prior, outline = by_id.get(sheet_id), outlines.get(sheet_id)
        if prior is None or outline is None:
            continue
        headers = {cell.get("column"): str(cell.get("value") or "")
                   for cell in prior.get("cells", []) if cell.get("row") == 1}
        value_key = next((column.key for column in outline.columns
                          if column.header == headers.get(_range_column(chart.get("data_range")))), None)
        category_key = next((column.key for column in outline.columns
                             if column.header == headers.get(
                                 _range_column(chart.get("categories_range")))), None)
        if value_key is None or category_key is None:
            continue
        charts.append(OutlineChart(
            sheet_id=sheet_id,
            type=chart.get("type") if chart.get("type") in {"bar", "line", "pie"} else "bar",
            title=str(chart.get("title") or "Chart")[:200],
            value_column=value_key, category_column=category_key,
        ))
    return charts


class RevisionStages:
    """Revising an existing artifact: the prior outline and the edits to it. A mixin of ArtifactPipeline."""

    def _run_edits(self):
        title, sections, payloads = self._prior_outline()
        prior_provenance = (self.prior or {}).get("provenance") or {}
        self.program_title = str(prior_provenance.get("program_title") or "") or None
        self._prior_plan = bool(prior_provenance.get("plan"))
        try:
            completed = json.loads(prior_provenance.get("completed_courses") or "[]")
        except (TypeError, ValueError):
            completed = []
        self._prior_completed = [str(code) for code in completed] if isinstance(completed, list) else []
        if self.kind == "workbook":
            self.outline = StageOutline(title=title, sheets=sections,
                                        charts=_prior_charts(self.prior, sections))
        else:
            self.outline = StageOutline(title=title, sections=sections)
        payload = {
            "request": self.inputs.prompt,
            "kind": self.kind,
            "current": {
                "title": title,
                "sections": [
                    {"id": section.id,
                     "heading": getattr(section, "heading", getattr(section, "name", "")),
                     "first_sentence": _first_sentence(
                         payloads[section.id].text if section.id in payloads else "")}
                    for section in sections
                ],
            },
            "slots": [{"slot": index, "source": slot.source_name,
                       "locator": slot.locator_label, "excerpt": slot.text[:300]}
                      for index, slot in self._outline_slots()],
        }
        self.kept = {section.id: payloads[section.id] for section in sections
                     if section.id in payloads}
        messages = _stage_messages(
            "You revise an existing artifact by targeted edits only. Do not rewrite kept "
            "sections. Call advisor_stage_edits alone with keep, replace, insert_after or "
            "delete operations; every current section must have exactly one keep, replace or "
            "delete. A replacement keeps its section id. Use the numbered source slots.",
            payload,
        )
        self._run_stage(
            EDITS_TOOL,
            "Validate and accept the artifact edits. Call this tool alone to finish.",
            StageEdits,
            lambda value, _scope: self._stage_edits(value),
            messages,
            stage_key="edits",
        )
        if self.edits is None:
            raise SafeJobError("invalid_output", "The model did not return complete edits.")

    def _stage_edits(self, value: StageEdits) -> StageAck:
        try:
            self._apply_edits(value)
        except (ValueError, ValidationError) as exc:
            raise ToolInputError("The edits were not accepted; fix them and call again: "
                                 + candidate_problem(exc)) from exc
        self.edits = value
        return StageAck()

    def _prior_outline(self):
        spec = self.prior or {}
        kind = spec.get("kind")
        sections: list[OutlineSection] = []
        payloads: dict[str, AssembledSection] = {}
        title = str(spec.get("title") or "Artifact")
        if kind == "document":
            groups = _group_document_blocks(spec.get("blocks", []))
            for index, (section_id, blocks) in enumerate(groups.items(), 1):
                heading = next((block.get("text") for block in blocks
                                if block.get("type") == "heading"), f"Section {index}")
                first = next((block.get("text") or " ".join(block.get("items", []))
                              for block in blocks
                              if block.get("type") != "heading"), "")
                section = OutlineSection(
                    id=section_id, heading=str(heading)[:200],
                    purpose=_first_sentence(first) or "Keep this section.",
                    block_types=sorted({block.get("type") for block in blocks
                                        if block.get("type") in {
                                            "paragraph", "heading", "list", "table", "code", "quote"}})
                    or ["paragraph"],
                    source_slots=[],
                )
                sections.append(section)
                payloads[section_id] = AssembledSection(
                    section=section, blocks=blocks,
                    evidence=_evidence_for_blocks(
                        spec.get("evidence", {}), [block.get("id") for block in blocks]),
                    text="\n".join(_block_text(block) for block in blocks),
                    # Kept content already passed the checks when it was made.
                    # Its headings are structure, not claims that need a citation.
                    skip_elements={block["id"] for block in blocks
                                   if block.get("type") == "heading"},
                )
        elif kind == "workbook":
            for sheet in spec.get("sheets", []):
                columns = _columns_from_sheet(sheet)
                section = OutlineSheet(
                    id=str(sheet.get("id") or f"sheet{len(sections) + 1}"),
                    name=str(sheet.get("name") or "Sheet")[:31],
                    columns=columns, row_purpose="Keep this sheet's data.",
                    source_slots=[],
                )
                sections.append(section)
                payloads[section.id] = AssembledSection(
                    section=section, sheet=sheet,
                    evidence=_evidence_for_cells(spec.get("evidence", {}), sheet),
                    text=_sheet_text(sheet),
                    skip_elements={f"{sheet.get('id')}/{cell.get('id')}"
                                   for cell in sheet.get("cells", [])
                                   if cell.get("row") == 1},
                )
        else:
            section = OutlineSection(
                id="body", heading=title[:200], purpose="Revise this page.",
                block_types=["paragraph", "heading", "list", "table"], source_slots=[])
            sections.append(section)
            payloads["body"] = AssembledSection(
                section=section, html=str(spec.get("html") or ""),
                evidence=dict(spec.get("evidence") or {}),
                text=str(spec.get("html") or ""),
            )
        return title, sections, payloads

    def _apply_edits(self, value: StageEdits):
        current = self._current_sections()
        prior_sheets = ({sheet.id: sheet for sheet in current}
                        if self.kind == "workbook" else {})
        by_id = {self._section_id(section): section for section in current}
        operations: dict[str, EditOperation] = {}
        inserts: dict[str, list[EditOperation]] = {}
        for operation in value.operations:
            if operation.action == "insert_after":
                if operation.section_id not in by_id:
                    raise ValueError(f"insert_after names unknown section {operation.section_id!r}")
                section = operation.section or operation.sheet
                if section is None:
                    raise ValueError("insert_after needs a complete new section")
                if section.id in by_id:
                    raise ValueError(f"inserted section ID {section.id!r} already exists")
                inserts.setdefault(operation.section_id, []).append(operation)
                continue
            if operation.action not in {"keep", "replace", "delete"}:
                raise ValueError(f"unknown edit action {operation.action!r}")
            if operation.section_id not in by_id:
                raise ValueError(f"{operation.action} names unknown section {operation.section_id!r}")
            if operation.section_id in operations:
                raise ValueError(f"section {operation.section_id!r} has more than one edit")
            operations[operation.section_id] = operation
        missing = set(by_id) - set(operations)
        if missing:
            raise ValueError("every current section needs keep, replace or delete: "
                             + ", ".join(sorted(missing)))
        new_sections: list[OutlineSection | OutlineSheet] = []
        write: set[str] = set()
        for section in current:
            section_id = self._section_id(section)
            operation = operations[section_id]
            if operation.action == "keep":
                new_sections.append(section)
            elif operation.action == "delete":
                self.kept.pop(section_id, None)
            else:
                replacement = operation.section if self.kind != "workbook" else operation.sheet
                if replacement is None:
                    raise ValueError(f"replace {section_id!r} needs a complete section")
                if replacement.id != section_id:
                    raise ValueError("a replacement keeps its section id")
                if (self.plan is not None and getattr(section, "courses", [])
                        and list(getattr(replacement, "courses", [])) != list(section.courses)):
                    raise ValueError("a plan section's replacement cannot add, drop or move a course")
                self.kept.pop(section_id, None)
                new_sections.append(replacement)
                write.add(section_id)
            for insert in sorted(inserts.get(section_id, []), key=lambda item: item.section.id):
                inserted = insert.section if self.kind != "workbook" else insert.sheet
                if inserted is None:
                    raise ValueError("insert_after needs a complete new section")
                if inserted.id in {self._section_id(item) for item in new_sections}:
                    raise ValueError(f"inserted section ID {inserted.id!r} already exists")
                if self.plan is not None and getattr(inserted, "courses", []):
                    raise ValueError("a plan revision cannot insert a new scheduled term")
                new_sections.append(inserted)
                write.add(inserted.id)
        if not new_sections:
            raise ValueError("edits would remove every section")
        if self.kind == "workbook":
            self.outline.sheets = [section for section in new_sections
                                   if isinstance(section, OutlineSheet)]
            self.outline.sections = []
            self.outline.title = value.title
            if not self.outline.sheets:
                raise ValueError("edits would remove every sheet")
            for sheet in self.outline.sheets:
                prior = prior_sheets.get(sheet.id)
                if prior is not None:
                    sheet.columns = _inherit_columns(prior.columns, sheet.columns)
            names = [sheet.name.casefold() for sheet in self.outline.sheets]
            if len(names) != len(set(names)):
                raise ValueError("sheet names must be unique")
            for sheet in self.outline.sheets:
                keys = [column.key for column in sheet.columns]
                if len(keys) != len(set(keys)):
                    raise ValueError(f"sheet {sheet.id} has duplicate column keys")
        else:
            self.outline.sections = [section for section in new_sections
                                     if isinstance(section, OutlineSection)]
            self.outline.sheets = []
            self.outline.title = value.title
            if not self.outline.sections:
                raise ValueError("edits would remove every section")
        self._to_write = write
