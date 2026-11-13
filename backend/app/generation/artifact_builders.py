"""Templates that code fills in from program data, so a weak model writes nothing.

Each builder takes what the database already knows about a program (its
header facts and raw requirement lines, each with the chunks that support it)
and returns finished document blocks plus the chunks each block cites. The
artifact pipeline only maps those chunks to source slots. A model's part, if
any, is to pick existing catalog sentences by number (see `sentence_candidates`).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from app.advising.research_repository import ProgramItem

CHECKBOX = "☐ "
MAX_LINES = 120
MAX_COMPARED_COURSES = 60
MAX_CANDIDATES = 24

# Header fields a program record carries, in reading order.
FACT_LABELS = (("degree", "Degree"), ("hours", "Credit hours"),
               ("courses_required", "Courses required"), ("department", "Department"))


@dataclass(frozen=True)
class ProgramData:
    """One program as the builders see it: header facts and raw lines, with support."""

    title: str
    facts: dict[str, tuple[str, tuple]] = field(default_factory=dict)
    items: tuple[ProgramItem, ...] = ()


@dataclass
class Built:
    """Blocks a template produced and, per block id, the chunks it cites."""

    blocks: list[dict] = field(default_factory=list)
    cites: dict[str, list] = field(default_factory=dict)

    def add(self, block: dict, chunks=()) -> None:
        self.blocks.append(block)
        chunks = list(dict.fromkeys(chunks))
        if chunks:
            self.cites[block["id"]] = chunks

    def extend(self, other: Built) -> None:
        self.blocks.extend(other.blocks)
        self.cites.update(other.cites)


def is_heading_item(item: ProgramItem) -> bool:
    """A parsed header ("Requirements", or the section's own title) is not a rule."""
    if item.mentioned_course_codes:
        return False
    text = item.line.text.strip().rstrip(":").casefold()
    return len(text.split()) <= 1 or text == (item.section or "").strip().casefold()


def _support(item: ProgramItem) -> list:
    return [chunk for chunk, _start, _end in item.line.support]


# A line that is only a course code ("Math 111"), which reads better with its title.
_BARE_CODE = re.compile(r"[A-Za-z]{2,8}(?:_OX)?\s?\d{3}[A-Za-z]{0,3}")


def _line_text(item: ProgramItem, titles: Mapping[str, str] | None = None) -> str:
    text = " ".join(item.line.text.split())
    codes = item.mentioned_course_codes
    if (titles and len(codes) == 1 and titles.get(codes[0])
            and _BARE_CODE.fullmatch(text)):
        text = f"{codes[0]} · {titles[codes[0]]}"
    if item.choose and not re.search(r"\b(?:choose|select|pick|one|two|three|four)\b",
                                     text, re.IGNORECASE):
        text += f" (choose {item.choose})"
    return text[:400]


def requirement_blocks(program: ProgramData, prefix: str, *, checklist: bool,
                       heading: str | None = "Requirements",
                       titles: Mapping[str, str] | None = None) -> Built:
    """The program's lines, grouped by its own sections, each group citing its lines."""
    built = Built()
    if heading:
        built.add({"id": f"{prefix}:h", "type": "heading", "level": 2, "text": heading})
    groups: dict[str, list[ProgramItem]] = {}
    for item in program.items[:MAX_LINES]:
        if not is_heading_item(item) and item.line.text.strip():
            groups.setdefault((item.section or "").strip(), []).append(item)
    named = len([name for name in groups if name]) > 1
    for index, (section, items) in enumerate(groups.items(), 1):
        if named and section:
            built.add({"id": f"{prefix}:s{index}", "type": "heading", "level": 3,
                       "text": section[:200]})
        built.add({"id": f"{prefix}:l{index}", "type": "list",
                   "items": [(CHECKBOX if checklist else "") + _line_text(item, titles)
                             for item in items]},
                  [chunk for item in items for chunk in _support(item)])
    return built


def facts_table(programs: Sequence[ProgramData], block_id: str) -> Built:
    """An "At a glance" table: one row per header fact, one column per program."""
    built = Built()
    rows, chunks = [], []
    for key, label in FACT_LABELS:
        values = [program.facts.get(key) for program in programs]
        if not any(values):
            continue
        rows.append([label, *((value[0] if value else "Not listed") for value in values)])
        chunks.extend(chunk for value in values if value for chunk in value[1])
    if rows:
        headers = ["", *(program.title for program in programs)] if len(programs) > 1 \
            else ["Fact", "Value"]
        built.add({"id": block_id, "type": "table", "headers": headers, "rows": rows}, chunks)
    return built


def _codes(program: ProgramData) -> dict[str, list]:
    """Each course a program's lines name, with the chunks of the lines that name it."""
    codes: dict[str, list] = {}
    for item in program.items[:MAX_LINES]:
        for code in item.mentioned_course_codes:
            codes.setdefault(code, []).extend(_support(item))
    return codes


def comparison_blocks(left: ProgramData, right: ProgramData, prefix: str) -> Built:
    """Two programs side by side: a chart of shared and separate courses, then the courses."""
    built = Built()
    a, b = _codes(left), _codes(right)
    shared = sorted(set(a) & set(b))
    only_a, only_b = sorted(set(a) - set(b)), sorted(set(b) - set(a))
    built.add({"id": f"{prefix}:h", "type": "heading", "level": 2,
               "text": "Courses each program names"})
    built.add({
        "id": f"{prefix}:chart", "type": "chart", "chart_type": "stacked_bar",
        "title": "Courses named by each program's requirement lines",
        "caption": ("A course both programs name may still count toward only one of "
                    "them; check each program's rules on double counting."),
        "categories": [left.title[:120], right.title[:120]],
        "series": [{"name": "Named by both", "values": [len(shared), len(shared)]},
                   {"name": "Only this program", "values": [len(only_a), len(only_b)]}],
        "unit": "courses"})
    ordered = [*shared, *only_a, *only_b][:MAX_COMPARED_COURSES]
    if ordered:
        built.add({"id": f"{prefix}:t", "type": "table",
                   "headers": ["Course", left.title[:80], right.title[:80]],
                   "rows": [[code, "✓" if code in a else "—", "✓" if code in b else "—"]
                            for code in ordered]},
                  [chunk for code in ordered for chunk in [*a.get(code, []), *b.get(code, [])]])
    return built


_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def sentence_candidates(chunks: Sequence, skip: Sequence[str] = ()) -> list[tuple[str, object]]:
    """Whole catalog sentences a model may pick by number, each with its chunk.

    Short fragments, headings and sentences the template already shows are left
    out, so a pick can only add a real sentence from a cited source.
    """
    shown = {" ".join(text.split()).casefold() for text in skip}
    found: dict[str, object] = {}
    for chunk in chunks:
        for raw in _SENTENCE.split(" ".join(chunk.text.split())):
            sentence = raw.strip()
            if (40 <= len(sentence) <= 280 and sentence[-1:] in ".!?"
                    and sentence.casefold() not in shown and sentence not in found):
                found[sentence] = chunk
            if len(found) >= MAX_CANDIDATES:
                return list(found.items())
    return list(found.items())
