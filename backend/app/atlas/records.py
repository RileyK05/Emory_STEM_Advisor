"""Atlas records: the JSONL input format and code normalization."""

import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict

# Subjects run 2-7 letters (CS, MATH, DATASCI, ANCMED); numbers take up to two
# suffix letters (CS 371W, AAS 490RW).
_CODE_RE = re.compile(r"^\s*([A-Za-z]{2,7}(?:_OX)?)\s*(\d{3})([A-Za-z]{0,2})\s*$")


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


def normalize_code(value: str) -> str | None:
    """Canonicalize a course code. Returns None if it does not fit the shape."""
    match = _CODE_RE.match(value)
    if match is None:
        return None
    subject, digits, letter = match.groups()
    return f"{subject.upper()} {digits}{letter.upper()}"


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
        block = f"{course.code}: {course.title}\n\n{course.description}"
        if course.requisites:
            block += f"\n\nRequisites: {course.requisites}"
        if parts:
            offset += len("\n\n")
        start = offset
        offset += len(block)
        parts.append(block)
        locators.append(("course", course.code, start, offset))
    return "\n\n".join(parts), locators
