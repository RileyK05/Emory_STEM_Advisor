"""Base structures for common artifacts.

A weak model should not have to invent a layout. A template fixes the sections
or sheet columns, says what each one holds, and leaves the model to fill the
blanks from the supplied evidence. The template is a hint for the outline call
and the fallback when that call never succeeds.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TemplateSection:
    id: str
    heading: str
    purpose: str
    block_types: tuple[str, ...]
    pattern: str


@dataclass(frozen=True)
class TemplateColumn:
    key: str
    header: str
    type: str = "text"


@dataclass(frozen=True)
class TemplateSheet:
    id: str
    name: str
    row_purpose: str
    columns: tuple[TemplateColumn, ...]
    pattern: str


@dataclass(frozen=True)
class ArtifactTemplate:
    id: str
    kind: str
    title: str
    sections: tuple[TemplateSection, ...] = ()
    sheets: tuple[TemplateSheet, ...] = ()

    def describe(self) -> dict:
        """The structure the outline model is asked to keep."""
        described: dict = {"id": self.id, "title": self.title}
        if self.sections:
            described["sections"] = [
                {"id": section.id, "heading": section.heading, "purpose": section.purpose,
                 "block_types": list(section.block_types)}
                for section in self.sections
            ]
        if self.sheets:
            described["sheets"] = [
                {"id": sheet.id, "name": sheet.name, "row_purpose": sheet.row_purpose,
                 "columns": [{"key": column.key, "header": column.header,
                              "type": column.type} for column in sheet.columns]}
                for sheet in self.sheets
            ]
        return described

    def guidance(self, item_id: str) -> str | None:
        for section in self.sections:
            if section.id == item_id:
                return section.pattern
        for sheet in self.sheets:
            if sheet.id == item_id:
                return sheet.pattern
        return None


_CHECKLIST = re.compile(r"\bcheck[- ]?lists?\b", re.IGNORECASE)
_COMPARISON = re.compile(r"\bcompar(?:e|es|ed|ing|ison|isons)\b", re.IGNORECASE)
_PREREQUISITE = re.compile(r"\bprereq", re.IGNORECASE)
_REPORT = re.compile(r"\b(?:guide|report|handout|overview)\b", re.IGNORECASE)


_CHECKLIST_TEMPLATE = ArtifactTemplate(
    id="checklist", kind="document", title="Requirements checklist",
    sections=(
        TemplateSection(
            "requirements", "Requirements", "The program's required course lines.",
            ("list", "paragraph"),
            "One required course or requirement line per bullet: the exact course code "
            "first, then a short phrase from its slot. Do not merge two requirements.",
        ),
        TemplateSection(
            "choices", "Choices and electives", "Choose-N groups and elective lines.",
            ("list", "paragraph"),
            "One choice group per bullet, keeping its choose-N count and every alternative "
            "from the slot. Write 'Not listed' when a line names no courses.",
        ),
        TemplateSection(
            "notes", "Notes and next steps", "Restrictions, sequencing and a short next step.",
            ("paragraph", "list"),
            "One short paragraph on sequencing or restrictions from the slots, then at most "
            "three next steps. Do not put course codes in the next steps.",
        ),
    ),
)


_COMPARISON_TEMPLATE = ArtifactTemplate(
    id="comparison", kind="document", title="Program comparison",
    sections=(
        TemplateSection(
            "overview", "At a glance", "The two programs and their main difference.",
            ("paragraph",),
            "Two or three sentences naming both programs and the single biggest difference.",
        ),
        TemplateSection(
            "table", "Requirements side by side", "One row per requirement, one column per program.",
            ("table",),
            "One requirement per row and one column per program. Put 'Not listed' in a cell "
            "when that program's slot does not name the requirement.",
        ),
        TemplateSection(
            "differences", "Where they differ", "Only the real differences.",
            ("list",),
            "One real difference per bullet. Do not repeat rows that match in both programs.",
        ),
        TemplateSection(
            "fit", "Which fits you", "Who each program suits.",
            ("paragraph",),
            "Two sentences on who each program suits, using only the supplied slots.",
        ),
    ),
)


_REPORT_TEMPLATE = ArtifactTemplate(
    id="report", kind="document", title="Advising report",
    sections=(
        TemplateSection(
            "overview", "Overview", "What the program is and its size.",
            ("paragraph",),
            "One short paragraph: the program, its degree, and its credit or course count.",
        ),
        TemplateSection(
            "requirements", "Requirements", "The required course lines.",
            ("list", "table", "paragraph"),
            "One requirement group per bullet or table row. Keep every course code and "
            "choose-N count from the slot.",
        ),
        TemplateSection(
            "sequence", "Prerequisites and sequencing", "Order and blocked courses.",
            ("paragraph", "list"),
            "One short paragraph on order, then a bullet for each course that needs another "
            "first. Never invent a prerequisite edge.",
        ),
        TemplateSection(
            "next", "Next steps", "A short, concrete next step.",
            ("paragraph",),
            "One or two sentences: what to check with an advisor or the department, and why.",
        ),
    ),
)


_PAGE_TEMPLATE = ArtifactTemplate(
    id="page", kind="html", title="Program page",
    sections=(
        TemplateSection(
            "summary", "Summary", "What the page is about.",
            ("paragraph",),
            "One short paragraph naming the program and its overall shape.",
        ),
        TemplateSection(
            "glance", "Plan at a glance", "The main requirement groups.",
            ("list",),
            "A short bullet list with one requirement group per bullet and its course codes.",
        ),
        TemplateSection(
            "details", "Details", "The requirement detail.",
            ("paragraph", "list", "table"),
            "The detail from the slots. Use a small table when several lines share the same "
            "columns; otherwise use bullets.",
        ),
        TemplateSection(
            "next", "Next steps", "What to do next.",
            ("paragraph", "list"),
            "One short paragraph or three bullets. Do not add a code that is not in a slot.",
        ),
    ),
)


_TRACKER_TEMPLATE = ArtifactTemplate(
    id="tracker", kind="workbook", title="Credit tracker",
    sheets=(
        TemplateSheet(
            "tracker", "Tracker", "One course per row.",
            (TemplateColumn("course", "Course"), TemplateColumn("credits", "Credits", "number"),
             TemplateColumn("term", "Term"), TemplateColumn("status", "Status"),
             TemplateColumn("total", "Total", "total")),
            "One course per row with its credit value, term and status. Leave the total "
            "column to code. Use only courses from the slots.",
        ),
    ),
)


_COMPARISON_SHEET_TEMPLATE = ArtifactTemplate(
    id="comparison_sheet", kind="workbook", title="Program comparison",
    sheets=(
        TemplateSheet(
            "comparison", "Comparison", "One requirement per row.",
            (TemplateColumn("requirement", "Requirement"),
             TemplateColumn("left", "Program A"), TemplateColumn("right", "Program B"),
             TemplateColumn("notes", "Notes")),
            "One requirement per row. Put the first program in Program A and the second in "
            "Program B, with 'Not listed' when a program does not name it.",
        ),
    ),
)


_PREREQUISITE_TEMPLATE = ArtifactTemplate(
    id="prerequisite", kind="workbook", title="Prerequisite map",
    sheets=(
        TemplateSheet(
            "prerequisites", "Prerequisites", "One prerequisite edge per row.",
            (TemplateColumn("course", "Course"),
             TemplateColumn("prerequisite", "Prerequisite"),
             TemplateColumn("notes", "Notes")),
            "One course and one of its prerequisites per row. Copy only edges that appear "
            "in the supplied course records; never infer one.",
        ),
    ),
)


def choose_template(kind: str, request_text: str) -> ArtifactTemplate | None:
    """The base structure for a common artifact request, if one fits."""
    text = request_text or ""
    if kind == "document":
        if _CHECKLIST.search(text):
            return _CHECKLIST_TEMPLATE
        if _COMPARISON.search(text):
            return _COMPARISON_TEMPLATE
        if _REPORT.search(text):
            return _REPORT_TEMPLATE
        return None
    if kind == "workbook":
        if _COMPARISON.search(text):
            return _COMPARISON_SHEET_TEMPLATE
        if _PREREQUISITE.search(text):
            return _PREREQUISITE_TEMPLATE
        # A tracker is the safest base for any other spreadsheet.
        return _TRACKER_TEMPLATE
    if kind == "html":
        return _PAGE_TEMPLATE
    return None
