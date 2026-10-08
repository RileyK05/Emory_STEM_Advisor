"""Atlas records: the JSONL input format and code normalization."""

import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict

_CODE_RE = re.compile(r"^\s*([A-Za-z]{2,5})\s*(\d{3})([A-Za-z]?)\s*$")


class AtlasCourse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str
    title: str
    description: str = ""
    credits: str | None = None
    requisites: str | None = None


def normalize_code(value: str) -> str | None:
    """Canonicalize a course code. Returns None if it does not fit the shape."""
    match = _CODE_RE.match(value)
    if match is None:
        return None
    subject, digits, letter = match.groups()
    return f"{subject.upper()} {digits}{letter.upper()}"


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
        courses.append(course.model_copy(update={"code": code}))
    return courses


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
