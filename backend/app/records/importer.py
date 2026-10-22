"""Deterministic CSV parser and normalizer for academic records and transcripts.

Enforces zero-LLM parsing, course code normalization, duplicate candidate detection,
and explicit error tracking.
"""

from __future__ import annotations

import csv
import io
import math
import re
from typing import Any

from app.atlas.records import normalize_code

TERM_RE = re.compile(
    r"^\s*(Fall|Spring|Summer|Winter|FA|SP|SU|WI)\s*[-_/' ]?\s*(\d{2,4})\s*$",
    re.IGNORECASE,
)

VALID_STATUSES = {
    "completed",
    "in_progress",
    "planned",
    "withdrawn",
    "failed",
    "repeated",
    "transfer",
    "test_credit",
}

PASSING_GRADES = {
    "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "S", "P", "PASS", "SAT"
}
FAILING_GRADES = {"F", "U", "NC", "WF", "IF"}
WITHDRAWN_GRADES = {"W", "WD", "WU"}
IN_PROGRESS_GRADES = {"IP", "INP", "IN PROGRESS"}
TRANSFER_GRADES = {"TR", "TRANSFER", "CR"}
TEST_GRADES = {"AP", "IB", "TEST"}


def validate_credits(value: float) -> float:
    credits = float(value)
    if not math.isfinite(credits) or not 0 <= credits <= 30:
        raise ValueError("credits must be finite and between 0 and 30")
    return credits


def normalize_course_code(raw: str) -> str:
    cleaned = raw.strip()
    normalized = normalize_code(cleaned)
    if normalized is not None:
        return normalized
    # Transcript exports commonly use a separator where the live Atlas code
    # uses a space. Keep campus suffixes (for example _OX) intact.
    match = re.match(r"^([A-Za-z0-9]{2,7})[-_]([0-9].*)$", cleaned)
    if match:
        normalized = normalize_code(f"{match.group(1)} {match.group(2)}")
        if normalized is not None:
            return normalized
    return cleaned.upper()


def normalize_term(raw: str) -> str:
    cleaned = raw.strip()
    match = TERM_RE.match(cleaned)
    if not match:
        return cleaned

    season_raw, year_raw = match.groups()
    season_map = {
        "FA": "Fall",
        "FALL": "Fall",
        "SP": "Spring",
        "SPRING": "Spring",
        "SU": "Summer",
        "SUMMER": "Summer",
        "WI": "Winter",
        "WINTER": "Winter",
    }
    season = season_map.get(season_raw.upper(), season_raw.capitalize())
    year = int(year_raw)
    if year < 100:
        year += 2000 if year < 70 else 1900
    return f"{season} {year}"


def infer_status_and_credits(
    grade: str | None,
    attempted_credits: float,
    explicit_status: str | None = None,
    explicit_earned_credits: float | None = None,
) -> tuple[str, float]:
    attempted_credits = validate_credits(attempted_credits)
    clean_grade = grade.strip().upper() if grade else None
    status = explicit_status.lower().strip() if explicit_status else None
    if status and status not in VALID_STATUSES:
        raise ValueError("invalid attempt status")

    if explicit_earned_credits is not None:
        earned_credits = validate_credits(explicit_earned_credits)
    else:
        earned_credits = attempted_credits

    if status and status in VALID_STATUSES:
        if status in ("withdrawn", "failed", "in_progress", "planned") and explicit_earned_credits is None:
            earned_credits = 0.0
        return status, earned_credits

    if not clean_grade:
        return ("in_progress", 0.0) if attempted_credits == 0.0 else ("completed", earned_credits)

    if clean_grade in WITHDRAWN_GRADES:
        return "withdrawn", 0.0
    if clean_grade in FAILING_GRADES:
        return "failed", 0.0
    if clean_grade in IN_PROGRESS_GRADES:
        return "in_progress", 0.0
    if clean_grade in TRANSFER_GRADES:
        return "transfer", earned_credits
    if clean_grade in TEST_GRADES:
        return "test_credit", earned_credits
    if clean_grade in PASSING_GRADES:
        return "completed", earned_credits

    return "completed", earned_credits


def _find_field(row: dict[str, str], aliases: list[str]) -> str | None:
    for alias in aliases:
        for key, value in row.items():
            if key and key.strip().lower() == alias.lower():
                val_str = str(value).strip()
                if val_str:
                    return val_str
    return None


def parse_csv_content(
    csv_text: str,
    existing_attempts: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse CSV content and produce parsed rows, validation errors, and duplicate candidates."""
    parsed_rows: list[dict[str, Any]] = []
    row_errors: list[dict[str, Any]] = []
    duplicate_candidates: list[dict[str, Any]] = []

    existing_set = set()
    if existing_attempts:
        for att in existing_attempts:
            code = att.get("submitted_course_code") or att.get("resolved_course_code") or ""
            term = att.get("term") or ""
            if code and term:
                existing_set.add((normalize_course_code(code), normalize_term(term)))

    csv_seen = set()

    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        row_errors.append({
            "rowIndex": 0,
            "error": "CSV header is empty or missing",
            "raw": {},
        })
        return parsed_rows, row_errors, duplicate_candidates

    for idx, row in enumerate(reader, start=1):
        raw_code = _find_field(
            row,
            ["course", "course_code", "course code", "code", "class", "course_id", "course id", "subject_catalog"],
        )
        if not raw_code:
            row_errors.append({
                "rowIndex": idx,
                "error": "Missing course code column",
                "raw": row,
            })
            continue

        course_code = normalize_course_code(raw_code)

        raw_term = _find_field(row, ["term", "semester", "session", "term_name", "period"])
        if not raw_term:
            row_errors.append({
                "rowIndex": idx,
                "error": "Missing term or semester column",
                "raw": row,
            })
            continue

        term = normalize_term(raw_term)

        raw_credits = _find_field(
            row,
            ["attempted_credits", "credits", "credit_hours", "credit hours", "units", "hrs", "attempted"],
        )
        try:
            attempted_credits = validate_credits(raw_credits) if raw_credits is not None else 3.0
        except ValueError as exc:
            row_errors.append({
                "rowIndex": idx,
                "error": f"Invalid credits value '{raw_credits}': {exc}",
                "raw": row,
            })
            continue

        raw_earned_credits = _find_field(row, ["earned_credits", "earned", "credits_earned"])
        explicit_earned: float | None = None
        if raw_earned_credits is not None:
            try:
                explicit_earned = validate_credits(raw_earned_credits)
            except ValueError as exc:
                row_errors.append({
                    "rowIndex": idx,
                    "error": f"Invalid earned credits value '{raw_earned_credits}': {exc}",
                    "raw": row,
                })
                continue

        title = _find_field(row, ["title", "course_title", "course title", "name", "description"])
        grade = _find_field(row, ["grade", "letter_grade", "mark", "score"])
        explicit_status = _find_field(row, ["status", "attempt_status"])
        institution = _find_field(row, ["institution", "school", "college", "university"]) or "Emory University"

        try:
            status, earned_credits = infer_status_and_credits(
                grade, attempted_credits, explicit_status, explicit_earned
            )
        except ValueError as exc:
            row_errors.append({"rowIndex": idx, "error": str(exc), "raw": row})
            continue

        key = (course_code, term)
        if key in existing_set:
            duplicate_candidates.append({
                "rowIndex": idx,
                "courseCode": course_code,
                "term": term,
                "reason": "existing_attempt_in_record",
            })
        elif key in csv_seen:
            duplicate_candidates.append({
                "rowIndex": idx,
                "courseCode": course_code,
                "term": term,
                "reason": "duplicate_row_in_csv",
            })
        csv_seen.add(key)

        parsed_rows.append({
            "rowIndex": idx,
            "institution": institution,
            "submittedCourseCode": course_code,
            "resolvedCourseCode": course_code,
            "courseTitle": title,
            "term": term,
            "attemptedCredits": attempted_credits,
            "earnedCredits": earned_credits,
            "grade": grade.strip().upper() if grade else None,
            "status": status,
        })

    return parsed_rows, row_errors, duplicate_candidates
