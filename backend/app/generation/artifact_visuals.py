"""Diagram and chart blocks built from deterministic advising results.

A model never supplies a node, an edge or a number here. The plan map draws
the term packer's columns and the prerequisite pairs the sequence planner read
from the Course Atlas graph. The what-if chart counts which program lines name
a student's reported courses, using the same matcher the advisor's audit uses
when a program has no reviewed rules: a line that names a course is not a met
requirement, and the chart and its caption say so.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from app.advising.comparison import LineMatch
from app.advising.sequence import SequencePlan
from app.generation.artifact_builders import is_heading_item

MAX_MAP_NODES = 80
PLAN_MAP_SECTION = "planmap"
WHATIF_SECTION = "whatif"

_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")


def _node_id(code: str) -> str:
    return _SAFE_ID.sub("-", code).strip("-") or "course"


def _same(code: str, equivalents: Mapping[str, frozenset[str]]) -> set[str]:
    return {code, *equivalents.get(code, frozenset())}


def plan_diagram(
    plan: SequencePlan,
    terms: Sequence[Sequence[str]],
    *,
    blocked: Sequence[str] = (),
    completed: Sequence[str] = (),
    titles: Mapping[str, str] | None = None,
    prerequisite_codes: Mapping[str, set[str]] | None = None,
    equivalents: Mapping[str, frozenset[str]] | None = None,
    credits: Mapping[str, float] | None = None,
    row_label: str = "Term",
    title: str = "How your courses connect",
    caption: str = ("Arrows run from a prerequisite to the course that needs it, as the "
                    "Course Atlas lists them. Term 1 is what you can take next."),
) -> dict | None:
    """A top-to-bottom map: what is done, what to take next, and what each course unlocks.

    Rows are the packed terms (or, for a program overview, prerequisite levels). Arrows between planned courses are the
    planner's own (course, prerequisite) pairs. A completed course appears
    only when a planned course lists it as a prerequisite.
    """
    titles = titles or {}
    prerequisite_codes = prerequisite_codes or {}
    equivalents = equivalents or {}
    planned = [code for term in terms for code in term]
    if not planned:
        return None
    columns: list[tuple[str, str, list[str], str]] = []
    done_needed = [code for code in completed
                   if any(_same(code, equivalents) & prerequisite_codes.get(course, set())
                          for course in [*planned, *blocked])]
    if done_needed:
        columns.append(("done", "Done", done_needed, "done"))
    for index, term in enumerate(terms, 1):
        columns.append((f"term{index}", f"{row_label} {index}", list(term),
                        "next" if index == 1 else "planned"))
    if blocked:
        columns.append(("advisor", "Ask an advisor", list(blocked), "blocked"))
    nodes, groups, placed = [], [], {}
    for group_id, label, codes, status in columns:
        kept = [code for code in codes if code not in placed][:MAX_MAP_NODES - len(nodes)]
        if not kept:
            continue
        total = term_credits(kept, credits)
        groups.append({"id": group_id, "label": label,
                       **({"note": f"{_number(total)} credits"}
                          if total and group_id.startswith("term") else {})})
        for code in kept:
            placed[code] = _node_id(code)
            course_title = titles.get(code)
            nodes.append({"id": placed[code], "group": group_id, "status": status,
                          "label": f"{code} {course_title}" if course_title else code})
    edges = []
    seen = set()

    def connect(prerequisite: str, course: str):
        source, target = placed.get(prerequisite), placed.get(course)
        if source and target and source != target and (source, target) not in seen:
            seen.add((source, target))
            edges.append({"source": source, "target": target})

    for course, prerequisite in plan.prerequisites:
        connect(prerequisite, course)
    for course in [*planned, *blocked]:
        needs = prerequisite_codes.get(course, set())
        for code in done_needed:
            if _same(code, equivalents) & needs:
                connect(code, course)
    return {
        "id": f"{PLAN_MAP_SECTION}:diagram", "type": "diagram",
        "title": title, "caption": caption, "groups": groups, "nodes": nodes, "edges": edges[:200],
    }


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def term_credits(codes: Sequence[str], credits: Mapping[str, float] | None) -> float | None:
    """A term's credit total, only when every course's record states its credits."""
    values = [(credits or {}).get(code) for code in codes]
    return sum(values) if values and all(values) else None


def credit_chart(terms: Sequence[Sequence[str]], credits: Mapping[str, float]) -> dict | None:
    """Credits per term from the course records, when every term's total is known."""
    totals = [term_credits(term, credits) for term in terms]
    if len(totals) < 2 or not all(totals):
        return None
    return {"id": f"{PLAN_MAP_SECTION}:load", "type": "chart", "chart_type": "bar",
            "title": "Credits per term",
            "caption": "Credit hours from each course's Course Atlas record.",
            "categories": [f"Term {index}" for index in range(1, len(terms) + 1)],
            "series": [{"name": "Credits", "values": totals}], "unit": "credit hours"}


def _is_heading_line(match: LineMatch) -> bool:
    return match.state == "no_course_named" and is_heading_item(match.item)


def whatif_blocks(
    programs: Sequence[tuple[str, Sequence[LineMatch], Sequence[str]]],
    *,
    completed: Sequence[str],
) -> list[dict]:
    """A chart and one table per program: which requirement lines name your courses.

    `programs` holds (title, line matches, reported codes no line names). Lines
    that name no course (a credit count, an elective rule) are listed but not
    charted: a course list cannot speak to them.
    """
    if not programs:
        return []
    states = (("all_named_reported", "Your courses are named"),
              ("some_named_reported", "Some named"),
              ("none_reported", "Not yet"))
    chart = {
        "id": f"{WHATIF_SECTION}:chart", "type": "chart", "chart_type": "stacked_bar",
        "title": "Requirement lines your courses appear on",
        "caption": ("Counts requirement lines that name a course you reported. A named "
                    "course is not a met requirement: grades, credit hours and choose-N "
                    "rules still need an advisor or the official degree audit."),
        "categories": [title[:120] for title, _matches, _unused in programs],
        "series": [{"name": label,
                    "values": [sum(m.state == state for m in matches)
                               for _title, matches, _unused in programs]}
                   for state, label in states],
        "unit": "lines",
    }
    blocks: list[dict] = [chart]
    words = {"all_named_reported": "Named", "some_named_reported": "Partly named",
             "none_reported": "Not yet", "no_course_named": "No course listed"}
    for index, (title, matches, unused) in enumerate(programs, 1):
        blocks.append({"id": f"{WHATIF_SECTION}:h{index}", "type": "heading", "level": 3,
                       "text": title[:200]})
        rows = [[match.item.line.text[:400], words[match.state],
                 ", ".join(match.reported) or "—"] for match in matches
                if not _is_heading_line(match)]
        if rows:
            blocks.append({"id": f"{WHATIF_SECTION}:t{index}", "type": "table",
                           "headers": ["Requirement line", "Status", "Your matching courses"],
                           "rows": rows[:200]})
        if unused:
            blocks.append({"id": f"{WHATIF_SECTION}:u{index}", "type": "paragraph",
                           "text": "No line of this program names: " + ", ".join(unused) + "."})
    if completed:
        blocks.insert(0, {"id": f"{WHATIF_SECTION}:p", "type": "paragraph",
                          "text": "Courses you reported: " + ", ".join(completed) + "."})
    return blocks
