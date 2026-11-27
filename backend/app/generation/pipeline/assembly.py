"""Assembling the finished artifact, checking it, and repairing what fails."""

from __future__ import annotations

import json
import logging
import re

from app.artifacts.checks import (
    ArtifactCheckFinding,
    check_generated_artifact,
    content_elements,
    extract_planned_terms,
)
from app.artifacts.inert_html import render_inert_markup
from app.artifacts.markdown import render_blocks_html
from app.artifacts.storage import validate_spec
from app.generation.artifact_visuals import (
    PLAN_MAP_SECTION,
)
from app.generation.native_tools import (
    StageSection,
    validate_generation_size,
)
from app.generation.pipeline.shared import (
    _BLOCK_ADAPTER,
    _BUILDER_LABELS,
    PipelineResult,
    _block_text,
    _coerce_cell,
    _sheet_text,
    _slot_refs,
)
from app.jobs.store import SafeJobError

logger = logging.getLogger(__name__)


def _column_letters(index: int) -> str:
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _all_evidence_refs(evidence: dict) -> list[dict]:
    return [ref for refs in evidence.values() for ref in refs]


def _dedupe_refs(refs: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for ref in refs:
        key = ref.get("chunk_id")
        if key in seen:
            continue
        seen.add(key)
        result.append(ref)
    return result


class AssemblyStages:
    """Assembling the finished artifact, checking it, and repairing what fails. A mixin of ArtifactPipeline."""

    def _assemble_and_check(self):
        findings = []
        for round_index in range(self.settings.artifact_repair_rounds + 1):
            try:
                spec = self._assemble()
            except SafeJobError:
                raise
            except (ValueError, TypeError) as exc:
                # A spec that only fails once sheets/blocks are combined is a
                # server-side assembly fault, not a model refusal. Keep the
                # traceback in the log and give the student a safe message.
                logger.exception("artifact job %s failed to assemble", self.job.job_id)
                raise SafeJobError("invalid_output",
                                   "The artifact could not be assembled; try a shorter version.") from exc
            try:
                validate_generation_size(spec)
            except ValueError as exc:
                raise SafeJobError("invalid_output",
                                   "The artifact exceeded the supported size.") from exc
            self.context.progress({"stage": "checking"})
            findings = self._check(spec)
            if not findings:
                return PipelineResult(spec)
            if round_index >= self.settings.artifact_repair_rounds:
                break
            self._repair(findings)
        # Check text names codes and sections, never slot or model text.
        logger.warning("artifact job %s failed its checks: %s", self.job.job_id,
                       "; ".join(f"{finding.check} {finding.message}" for finding in findings))
        message = findings[0].student_message if findings else "Artifact generation failed validation."
        raise SafeJobError("invalid_output", message)

    def _check(self, spec) -> list[ArtifactCheckFinding]:
        payload = spec.model_dump(mode="json")
        plan_terms = (extract_planned_terms(payload)
                      if self.plan is not None or self._prior_plan else None)
        term_sections = {}
        if plan_terms:
            for index, section in enumerate(self._current_sections(), 1):
                if getattr(section, "courses", []):
                    heading = getattr(section, "heading", getattr(section, "name", ""))
                    match = re.search(r"(\d+)", heading)
                    if match:
                        term_sections[int(match.group(1))] = section.id
        return check_generated_artifact(
            self.conn,
            collection_id=self.collection_id,
            spec=spec,
            known_courses=self.known_courses,
            campus_equivalents=self.campus_equivalents,
            program_title=self.program_title,
            completed_courses=self._completed_courses(),
            additional_courses=self.extra_courses,
            plan_terms=plan_terms,
            plan_term_sections=term_sections,
            section_texts=self.section_texts,
            skip_elements=(set(content_elements(spec)) if self.inputs.mode == "authoring"
                           else self._skip_elements()),
            catalog_aliases=self.catalog_aliases,
            quoted_codes=self.quoted_codes,
        )

    def _skip_elements(self) -> set[str]:
        skipped = set(getattr(self, "_skip", set()))
        for assembly in self.kept.values():
            skipped.update(assembly.skip_elements)
        return skipped

    def _repair(self, findings: list[ArtifactCheckFinding]):
        by_section: dict[str, list[ArtifactCheckFinding]] = {}
        for finding in findings:
            if not finding.section_id:
                # No section to repair: name the problem in the log before failing.
                logger.warning("artifact job %s failed a check no section owns: %s %s",
                               self.job.job_id, finding.check, finding.message)
                raise SafeJobError("invalid_output", finding.student_message)
            by_section.setdefault(finding.section_id, []).append(finding)
        by_id = {section.id: section for section in self._current_sections()}
        items = []
        for section_id, section_findings in by_section.items():
            section = by_id.get(section_id)
            if section is None:
                raise SafeJobError("invalid_output", section_findings[0].student_message)
            if section_id in self.kept and self.prior is None:
                # A built section is assembled as built: a rewrite would be
                # ignored and the same finding would come back every round.
                logger.warning("artifact job %s: code-built section %s failed %s: %s",
                               self.job.job_id, section_id, section_findings[0].check,
                               section_findings[0].message)
                raise SafeJobError("invalid_output", section_findings[0].student_message)
            logger.warning("artifact job %s repairing section %s: %s", self.job.job_id, section_id,
                           "; ".join(f"{f.check}: {f.message}" for f in section_findings[:5]))
            items.append((section,
                          " ".join(finding.message for finding in section_findings[:5]),
                          getattr(section, "heading", getattr(section, "name", ""))))
        self._write_many(items, stage="repairing")

    def _assemble(self):
        self.section_texts = {}
        if self.kind == "document":
            return self._assemble_document()
        if self.kind == "workbook":
            return self._assemble_workbook()
        return self._assemble_html()

    def _provenance(self) -> dict:
        """Scalar metadata a later revision needs to re-check the same artifact."""
        provenance = dict((self.prior or {}).get("provenance") or {})
        # The artifact benchmark uses this to tell a corpus-cited artifact
        # from a file that only holds the student's own text.
        provenance["generation_mode"] = self.inputs.mode
        if self.outline_fallback:
            provenance["outline_fallback"] = True
        if self.template is not None:
            provenance["template"] = self.template.id
            if self.template_fallback:
                provenance["template_fallback"] = True
        if self.plan is not None:
            provenance.update({
                "plan": True,
                "program_title": self.program_title or "",
                "completed_courses": json.dumps(list(self.outline.completed_courses)),
            })
        elif self._prior_plan:
            provenance.update({
                "plan": True,
                "program_title": self.program_title or "",
                "completed_courses": json.dumps(self._prior_completed),
            })
        elif self.program_title:
            provenance["program_title"] = self.program_title
        if self.whatif_programs:
            provenance["whatif_programs"] = json.dumps(self.whatif_programs)
        if self.builder and self.built_programs:
            provenance["built_template"] = self.builder
            provenance["artifact_label"] = _BUILDER_LABELS[self.builder]
        return provenance

    def _assemble_document(self):
        blocks: list[dict] = []
        evidence: dict[str, list[dict]] = {}
        skipped: set[str] = set()
        current = self._current_sections()
        rewritten = any(section.id not in self.kept for section in current)
        for section in current:
            if section.id == PLAN_MAP_SECTION and (self.plan is not None or rewritten):
                # A kept map is rebuilt below for a new plan, and may be stale
                # once a revision rewrites any term.
                continue
            kept = self.kept.get(section.id)
            if kept is not None:
                blocks.extend(kept.blocks)
                evidence.update(kept.evidence)
                self.section_texts[section.id] = "\n".join(
                    _block_text(block) for block in kept.blocks)
                continue
            payload = self.section_payloads[section.id]
            section_blocks = []
            heading_id = f"{section.id}:h"
            section_blocks.append({
                "id": heading_id, "type": "heading", "level": 2,
                "text": getattr(section, "heading", section.id),
            })
            skipped.add(heading_id)
            for block in payload.blocks:
                item = dict(block)
                item["id"] = f"{section.id}:{block['id']}"
                section_blocks.append(item)
            for binding in payload.bindings or []:
                evidence[f"{section.id}:{binding.element_id}"] = _slot_refs(
                    binding.source_slots, self.grounding.slots)
            blocks.extend(section_blocks)
            self.section_texts[section.id] = "\n".join(
                _block_text(block) for block in section_blocks)
        assumptions = self._plan_assumptions_block()
        if assumptions is not None:
            blocks.append(assumptions)
            skipped.add(assumptions["id"])
        visuals = self._plan_visuals()
        if visuals:
            heading = {"id": f"{PLAN_MAP_SECTION}:h", "type": "heading", "level": 2,
                       "text": "Course map"}
            blocks.extend([heading, *visuals])
            skipped.update({heading["id"], *(visual["id"] for visual in visuals)})
        title = self.outline.title if self.outline else "Artifact"
        spec = validate_spec("document", {
            "kind": "document", "title": title, "blocks": blocks,
            "evidence": evidence, "provenance": self._provenance(),
        })
        self._skip = skipped
        return spec

    def _assemble_workbook(self):
        sheets: list[dict] = []
        evidence: dict[str, list[dict]] = {}
        skipped: set[str] = set()
        for section in self._current_sections():
            kept = self.kept.get(section.id)
            if kept is not None:
                sheets.append(kept.sheet)
                evidence.update(kept.evidence)
                self.section_texts[section.id] = _sheet_text(kept.sheet)
                continue
            sheet, sheet_evidence, sheet_skipped = self._build_sheet(section,
                                                                     self.section_payloads[section.id])
            sheets.append(sheet)
            evidence.update(sheet_evidence)
            skipped.update(sheet_skipped)
            self.section_texts[section.id] = _sheet_text(sheet)
        title = self.outline.title if self.outline else "Workbook"
        charts = []
        if self.outline:
            by_id = {sheet["id"]: sheet for sheet in sheets}
            for index, chart in enumerate(self.outline.charts, 1):
                sheet = by_id.get(chart.sheet_id)
                if sheet is None:
                    continue
                columns = {column.key: position + 1
                           for position, column in enumerate(
                               next(item.columns for item in self.outline.sheets
                                    if item.id == chart.sheet_id))}
                value_column = columns.get(chart.value_column)
                category_column = columns.get(chart.category_column)
                if value_column is None or category_column is None:
                    continue
                last_row = max((cell["row"] for cell in sheet["cells"]), default=1)
                charts.append({
                    "id": f"{chart.sheet_id}:chart{index}", "type": chart.type,
                    "title": chart.title, "sheet_id": chart.sheet_id,
                    "data_range": f"{_column_letters(value_column)}1:"
                                  f"{_column_letters(value_column)}{last_row}",
                    "categories_range": f"{_column_letters(category_column)}1:"
                                        f"{_column_letters(category_column)}{last_row}",
                    "titles_from_data": True,
                })
        spec = validate_spec("workbook", {
            "kind": "workbook", "title": title, "sheets": sheets,
            "charts": charts, "evidence": evidence, "provenance": self._provenance(),
        })
        self._skip = skipped
        return spec

    def _build_sheet(self, section, payload: StageSection):
        columns = section.columns
        cells: list[dict] = []
        evidence: dict[str, list[dict]] = {}
        skipped: set[str] = set()
        for position, column in enumerate(columns, 1):
            cell_id = f"{section.id}:h{position}"
            cells.append({"id": cell_id, "row": 1, "column": position,
                          "value": column.header,
                          "style": {"bold": True}})
            skipped.add(f"{section.id}/{cell_id}")
        last_row = 1
        for row_index, row in enumerate(payload.rows, 2):
            last_row = row_index
            refs = _slot_refs(row.source_slots, self.grounding.slots)
            for position, column in enumerate(columns, 1):
                if column.type == "total":
                    continue
                raw = row.values[position - 1]
                if raw in (None, ""):
                    continue
                value = _coerce_cell(raw, column.type)
                cell_id = f"{section.id}:r{row_index}c{position}"
                cells.append({"id": cell_id, "row": row_index, "column": position,
                              "value": value})
                skip_cell = (section.id, row_index, position) in getattr(
                    self, "plan_skip_cells", set())
                if refs and not skip_cell:
                    evidence[f"{section.id}/{cell_id}"] = refs
                elif skip_cell:
                    skipped.add(f"{section.id}/{cell_id}")
        has_plan_credits_col = (
            self.plan is not None
            and len(columns) >= 2
            and columns[0].key == "course"
            and columns[1].key == "credits"
            and last_row >= 2
        )
        if has_plan_credits_col:
            sum_row = last_row + 1
            label_id = f"{section.id}:total-label"
            total_id = f"{section.id}:total"
            cells.append({"id": label_id, "row": sum_row, "column": 1,
                          "value": "Total Credits", "style": {"bold": True}})
            cells.append({"id": total_id, "row": sum_row, "column": 2,
                          "formula": f"=SUM(B2:B{last_row})", "style": {"bold": True}})
            skipped.update({f"{section.id}/{label_id}", f"{section.id}/{total_id}"})
        total_columns = [position for position, column in enumerate(columns, 1)
                         if column.type == "total"
                         or column.header.strip().casefold() == "total"]
        if self.total_requested and not total_columns and not has_plan_credits_col:
            position = len(columns) + 1
            sources = [source_position for source_position, column in enumerate(columns, 1)
                       if column.type == "number"]
            if sources:
                header_id = f"{section.id}:h{position}"
                cells.append({"id": header_id, "row": 1, "column": position,
                              "value": "Total", "style": {"bold": True}})
                skipped.add(f"{section.id}/{header_id}")
                letters = [_column_letters(source) for source in sources]
                formula = "=" + "+".join(
                    f"SUM({letter}2:{letter}{max(last_row, 2)})" for letter in letters)
                cell_id = f"{section.id}:t{position}"
                cells.append({"id": cell_id, "row": max(last_row, 2), "column": position,
                              "formula": formula})
                if payload.rows and payload.rows[-1].source_slots:
                    evidence[f"{section.id}/{cell_id}"] = _slot_refs(
                        payload.rows[-1].source_slots, self.grounding.slots)
        for position in total_columns:
            sources = [source_position for source_position, column in enumerate(columns, 1)
                       if column.type == "number" and source_position != position]
            if not sources:
                sources = [source_position for source_position in range(1, len(columns) + 1)
                           if source_position != position]
            letters = [_column_letters(source) for source in sources]
            formula = "=" + "+".join(
                f"SUM({letter}2:{letter}{max(last_row, 2)})" for letter in letters)
            cell_id = f"{section.id}:t{position}"
            cells.append({"id": cell_id, "row": max(last_row, 2), "column": position,
                          "formula": formula})
            if payload.rows and payload.rows[-1].source_slots:
                evidence[f"{section.id}/{cell_id}"] = _slot_refs(
                    payload.rows[-1].source_slots, self.grounding.slots)
        widths = {}
        for position, column in enumerate(columns, 1):
            longest = max((len(str(cell.get("value") or "")) for cell in cells
                           if cell["column"] == position), default=len(column.header))
            widths[_column_letters(position)] = min(max(longest + 2, 10), 40)
        sheet = {
            "id": section.id, "name": section.name, "cells": cells,
            "column_widths": widths, "freeze_panes": "A2",
        }
        return sheet, evidence, skipped

    def _assemble_html(self):
        fragments = []
        evidence: dict[str, list[dict]] = {}
        refs: list[dict] = []
        for section in self._current_sections():
            kept = self.kept.get(section.id)
            if kept is not None:
                fragments.append(kept.html)
                refs.extend(_all_evidence_refs(kept.evidence))
                self.section_texts[section.id] = kept.text
                continue
            payload = self.section_payloads[section.id]
            fragment = f'<div class="artifact-section" id="{section.id}">{payload.html}</div>'
            fragments.append(fragment)
            self.section_texts[section.id] = payload.html
            for binding in payload.bindings or []:
                refs.extend(_slot_refs(binding.source_slots, self.grounding.slots))
        assumptions = self._plan_assumptions_block()
        if assumptions is not None:
            fragments.append(render_blocks_html([_BLOCK_ADAPTER.validate_python(assumptions)]))
        visuals = self._plan_visuals()
        if visuals:
            fragments.append(render_blocks_html(
                [_BLOCK_ADAPTER.validate_python(block) for block in [
                    {"id": f"{PLAN_MAP_SECTION}:h", "type": "heading", "level": 2,
                     "text": "Course map"}, *visuals]]))
        page = render_inert_markup(
            '<div class="artifact-page">' + "".join(fragments) + "</div>")
        if not page.strip():
            raise SafeJobError("invalid_output", "The page had no renderable content.")
        if refs:
            evidence["html"] = _dedupe_refs(refs)
        title = self.outline.title if self.outline else "Page"
        spec = validate_spec("html", {
            "kind": "html", "title": title, "html": page, "evidence": evidence,
            "provenance": self._provenance(),
        })
        self._skip = set()
        return spec
