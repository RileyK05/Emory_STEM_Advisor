"""Atlas records: the JSONL input format and code normalization."""

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

# Atlas includes short ARCH numbers, four-digit health-science numbers,
# digit-bearing subjects, and longer section/program suffixes. Preserve the
# displayed identity instead of truncating it to the College catalog shape.
_CODE_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9]{0,5}[A-Za-z](?:_OX)?)\s*"
    r"(\d{2,4})([A-Za-z][A-Za-z0-9]{0,7})?\s*$"
)
_REFERENCE_RE = re.compile(
    r"(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9]{0,5}?[A-Za-z](?:_OX)?)\s*"
    r"(\d{2,4}(?:[A-Za-z][A-Za-z0-9]{0,7})?)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_PROSE_LABELS = frozenset({
    "ALL", "AND", "ANY", "OR", "THE", "NOT", "ONLY", "OF", "ONE", "TWO", "SOME",
    "EACH", "IN", "AT", "ON", "FOR", "IS", "SPRING", "SUMMER", "FALL", "AUTUMN",
    "WINTER", "YEAR", "AY", "TERM", "SECTION", "CLASS", "PAGE", "CHAPTER", "TABLE",
    "FIGURE", "CREDIT", "CREDITS", "LEVEL",
})


class AtlasCourse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str
    title: str
    description: str = ""
    credits: str | None = None
    requisites: str | None = None
    # Official cross-listed codes from the catalog's explicit relation. Names and
    # descriptions never establish an identity; only this field does.
    crosslisted: list[str] = []
    # None preserves the legacy catalog representation. A reconciled snapshot
    # explicitly labels both live Atlas records and retained catalog fallbacks.
    authority: Literal["atlas", "catalog"] | None = None
    # A retired record is a known historical identity, not a current offering.
    # It stays resolvable for evidence but is excluded from current catalog reads.
    lifecycle: Literal["current", "retired"] = "current"
    term: str | None = None
    careers: list[str] = []
    evidence: list[dict] = []
    registration_notes: list[str] = []


def normalize_code(value: str) -> str | None:
    """Canonicalize a course code. Returns None if it does not fit the shape."""
    match = _CODE_RE.match(value)
    if match is None:
        return None
    subject, digits, letter = match.groups()
    return f"{subject.upper()} {digits}{(letter or '').upper()}"


def course_codes_in(text: str) -> list[str]:
    """Find literal course identities in prose, preserving order and campus."""
    codes: list[str] = []
    for subject, tail in _REFERENCE_RE.findall(text):
        if subject.upper() in _PROSE_LABELS:
            continue
        code = normalize_code(f"{subject} {tail}")
        if code is not None and code not in codes:
            codes.append(code)
    return codes


def _normalize_crosslisted(code: str, raw: list) -> list[str]:
    aliases: list[str] = []
    for value in raw:
        alias = normalize_code(str(value))
        if alias is not None and alias != code and alias not in aliases:
            aliases.append(alias)
    return aliases


def load_atlas_lines(lines) -> list[AtlasCourse]:
    courses: list[AtlasCourse] = []
    seen: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        record = json.loads(stripped)
        course = AtlasCourse.model_validate(record)
        code = normalize_code(course.code)
        if code is None:
            raise ValueError(f"invalid course code: {course.code!r}")
        if code in seen:
            raise ValueError(f"duplicate course code: {code!r}")
        seen.add(code)
        aliases = _normalize_crosslisted(code, course.crosslisted)
        courses.append(course.model_copy(update={"code": code, "crosslisted": aliases}))
    return courses


def canonical_code_map(courses: list[AtlasCourse]) -> dict[str, str]:
    """Map every course code to the anchor of its verified cross-list group.

    Only explicit cross-list relations create an identity; similarly titled
    courses, equivalencies, and ordinary/W variants are distinct. The anchor is
    the lexicographically smallest code in the group, so it stays stable while
    the official relation exists. Aliases missing from this import are retained
    in the union (so removing one listing does not silently split a group) but
    never appear as their own course.
    """
    parent: dict[str, str] = {}

    def find(node: str) -> str:
        parent.setdefault(node, node)
        root = node
        while parent[root] != root:
            root = parent[root]
        while parent[node] != root:
            parent[node], node = root, parent[node]
        return root

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            high, low = max(left_root, right_root), min(left_root, right_root)
            parent[high] = low

    for course in courses:
        find(course.code)
        for alias in course.crosslisted:
            union(course.code, alias)
    return {course.code: find(course.code) for course in courses}


def load_atlas(path: Path) -> list[AtlasCourse]:
    return load_atlas_lines(path.read_text(encoding="utf-8").splitlines())


def render_courses(courses: list[AtlasCourse]) -> tuple[str, list[tuple[str, str, int, int]]]:
    """Render courses in code order with one locator per course.

    Returns (text, locators) where each locator is
    (locator_type, label, start_char, end_char).
    """
    ordered = sorted(courses, key=lambda c: c.code)
    parts: list[str] = []
    locators: list[tuple[str, str, int, int]] = []
    offset = 0
    for course in ordered:
        block = f"{course.code}: {course.title}"
        if course.authority == "atlas":
            scope = "; ".join(filter(None, [course.term, ", ".join(course.careers)]))
            block += f"\n\nRequirement authority: Emory Course Atlas ({scope})."
            block += "\nSource: https://atlas.emory.edu/"
            if course.code.split(" ", 1)[0].endswith("_OX"):
                block += "\nCampus: Oxford. The exact campus course code is retained."
        elif course.authority == "catalog":
            block += ("\n\nCatalog fallback: current Course Atlas requirements have not "
                      "been verified for this record.")
        block += f"\n\n{course.description}"
        if course.requisites:
            block += f"\n\nRequisites: {course.requisites}"
        for note in course.registration_notes:
            block += f"\n\nAtlas registration context: {note}"
        if parts:
            offset += len("\n\n")
        start = offset
        offset += len(block)
        parts.append(block)
        locators.append(("course", course.code, start, offset))
    return "\n\n".join(parts), locators
