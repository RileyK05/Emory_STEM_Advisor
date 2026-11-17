"""Deterministic artifact checks shared by generation and the manual benchmark.

Nothing here calls a model. The benchmark imports these same functions, so the
product and the benchmark cannot drift on what a passing artifact means.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.advising.catalog import _restore_tree
from app.artifacts.models import DocumentSpec, HtmlArtifactSpec, WorkbookSpec
from app.atlas.requisites import AllOf, CourseRef, canonical_codes_in
from app.db import queries

FORBIDDEN_LEAK_PATTERNS = [
    re.compile(r"(?i)\b(?:minimax|minicpm|gpt-[34]|claude-[23]|anthropic|openai)\b"),
    re.compile(r"(?i)\b(?:system_prompt|You are an advising assistant|Ground your answer)\b"),
    re.compile(r"(?i)\bTraceback \(most recent call last\):"),
    re.compile(r'(?i)File "[^"]+", line \d+'),
    re.compile(r"(?i)\b(?:HTTP 500|status_code 422|503 Service Unavailable)\b"),
]

TERM_PATTERNS = [
    re.compile(r"(?i)\b(?:semester|term|fall|spring|summer)\s*(\d+)\b"),
    re.compile(r"(?i)\b(year\s*\d+[\s\-_]*(?:fall|spring|summer))\b"),
]


# Joins the pieces of an artifact (title, blocks, list items, table cells) so that a
# course-code reader cannot read across two of them: a table row ["Section A — Physics",
# "12"] is a heading cell and a course count, not "PHYS 12". A plain newline lets the
# reader continue a subject or a code list into the next piece.
PIECE_SEPARATOR = "\n|\n"


def extract_artifact_text(spec: dict) -> str:
    """Extract all student-visible text from an artifact specification, piece by piece."""
    kind = spec.get("kind")
    texts: list[str] = [spec.get("title") or ""]
    if kind == "document":
        for block in spec.get("blocks", []):
            btype = block.get("type")
            if block.get("text"):
                texts.append(block["text"])
            if btype == "list" and "items" in block:
                texts.extend(block["items"])
            elif btype == "table":
                texts.extend(block.get("headers", []))
                for row in block.get("rows", []):
                    texts.extend(row)
            elif btype in {"diagram", "chart"}:
                # A drawing shows words too: K4 and K10 read its labels.
                texts.extend(filter(None, [block.get("title"), block.get("caption")]))
                texts.extend(group.get("label", "") for group in block.get("groups", []))
                texts.extend(node.get("label", "") for node in block.get("nodes", []))
                texts.extend(block.get("categories", []))
                texts.extend(series.get("name", "") for series in block.get("series", []))
    elif kind == "workbook":
        for sheet in spec.get("sheets", []):
            texts.append(sheet.get("name", ""))
            for cell in sheet.get("cells", []):
                if cell.get("value") is not None:
                    texts.append(str(cell["value"]))
    elif kind == "html":
        texts.append(spec.get("html", ""))
    return PIECE_SEPARATOR.join(texts)


def extract_planned_terms(spec: dict) -> dict[str, int]:
    """Map scheduled course codes to a 1-based term/semester index from a plan."""
    mapping: dict[str, int] = {}
    kind = spec.get("kind")
    current_term = 1
    if kind == "document":
        in_term = True
        for block in spec.get("blocks", []):
            block_id = str(block.get("id") or "")
            if block_id.startswith(("assumptions:", "advisor:")):
                continue
            text = block.get("text", "")
            matched_term = False
            for pattern in TERM_PATTERNS:
                m = pattern.search(text)
                if m:
                    try:
                        current_term = int(m.group(1))
                    except ValueError:
                        current_term += 1
                    matched_term = True
                    in_term = True
                    break
            if block.get("type") == "heading" and not matched_term and block.get("level", 2) <= 2:
                in_term = False
                continue
            if not in_term:
                continue
            codes = _course_codes_in(text)
            for code in codes:
                mapping.setdefault(code, current_term)
            if block.get("type") == "list":
                for item in block.get("items", []):
                    for code in _course_codes_in(item):
                        mapping.setdefault(code, current_term)
            elif block.get("type") == "table":
                headers = block.get("headers", [])
                col_terms = {}
                for idx, header in enumerate(headers):
                    for pattern in TERM_PATTERNS:
                        m = pattern.search(header)
                        if m:
                            try:
                                col_terms[idx] = int(m.group(1))
                            except ValueError:
                                col_terms[idx] = idx + 1
                            break
                for row in block.get("rows", []):
                    for col_idx, cell_text in enumerate(row):
                        term_idx = col_terms.get(col_idx, current_term)
                        for code in _course_codes_in(cell_text):
                            mapping.setdefault(code, term_idx)
    elif kind == "workbook":
        for s_idx, sheet in enumerate(spec.get("sheets", []), start=1):
            s_id = str(sheet.get("id") or "")
            s_name = sheet.get("name", "")
            if (s_id.lower() in {"advisor", "assumptions"}
                    or s_id.lower().startswith(("advisor:", "assumptions:"))
                    or "advisor" in s_name.lower()
                    or "assumptions" in s_name.lower()):
                continue
            s_term = s_idx
            for pattern in TERM_PATTERNS:
                m = pattern.search(s_name)
                if m:
                    try:
                        s_term = int(m.group(1))
                    except ValueError:
                        s_term = s_idx
                    break
            for cell in sheet.get("cells", []):
                val = str(cell.get("value") or "")
                for code in _course_codes_in(val):
                    mapping.setdefault(code, s_term)
    return mapping


def content_elements(spec) -> set[str]:
    """The model-written content elements of a validated native spec."""
    if isinstance(spec, DocumentSpec):
        return {block.id for block in spec.blocks if
                getattr(block, "text", "") or getattr(block, "items", ())
                or getattr(block, "rows", ()) or getattr(block, "headers", ())}
    if isinstance(spec, WorkbookSpec):
        return {f"{sheet.id}/{cell.id}" for sheet in spec.sheets for cell in sheet.cells
                if cell.value is not None or cell.formula is not None}
    if isinstance(spec, HtmlArtifactSpec):
        return {"html"}
    return set()


_MEETING_DAY_SUBJECTS = frozenset({
    "M", "T", "W", "R", "F", "S", "U",
    "MW", "TR", "MF", "WF", "MT", "WR", "FS", "MR", "TW",
})


def _is_meeting_time_code(code: str) -> bool:
    return code.split(" ", 1)[0] in _MEETING_DAY_SUBJECTS


def _course_codes_in(text: str) -> list[str]:
    return [code for code in canonical_codes_in(text)
            if not _is_meeting_time_code(code)]


def invalid_course_codes(codes, known_courses: set[str],
                         campus_equivalents: dict[str, frozenset[str]],
                         catalog_aliases: dict[str, frozenset[str]] | None = None) -> set[str]:
    """Codes that are neither a real course nor the other campus's same course.

    ``catalog_aliases`` are catalog spellings the program import linked to a
    current course (``load_catalog_code_aliases``); they name that course.
    """
    invalid = set()
    for code in codes:
        # A schedule can print a meeting pattern such as "MW 11" that looks
        # exactly like a course code. Day letters are not a course subject.
        if _is_meeting_time_code(code):
            continue
        if code in known_courses:
            continue
        if campus_equivalents.get(code, frozenset()) & known_courses:
            continue
        if catalog_aliases and catalog_aliases.get(code, frozenset()) & known_courses:
            continue
        invalid.add(code)
    return invalid


# "(LING 401 is not in the current Course Atlas)": the note a catalog line keeps
# beside a code it quotes that the Atlas does not have (user decision, 2026-11-16).
_ABSENT_NOTE_RE = re.compile(r"\(([^()]*?)\s+(?:is|are)\s+not in the current Course Atlas\)")


def absent_note(codes) -> str:
    codes = list(codes)
    if len(codes) == 1:
        return f" ({codes[0]} is not in the current Course Atlas)"
    return f" ({', '.join(codes[:-1])} and {codes[-1]} are not in the current Course Atlas)"


def label_absent_codes(text: str, known_courses, campus_equivalents,
                       catalog_aliases=None) -> str:
    """Keep a quoted line as written and say which of its codes the Atlas lacks.

    The line is read with the same parser K4 uses, so a range ("SOC 497R-499R"),
    a spelled subject ("Linguistics 401") or a bare number in a list is labeled
    exactly when K4 would reject it.
    """
    codes = dict.fromkeys(canonical_codes_in(text))
    missing = invalid_course_codes(codes, known_courses, campus_equivalents, catalog_aliases)
    missing -= _disclosed(text)
    return text.rstrip() + absent_note(code for code in codes if code in missing) if missing \
        else text


def _disclosed(text: str) -> set[str]:
    return {code for match in _ABSENT_NOTE_RE.finditer(text)
            for code in canonical_codes_in(match.group(1))}


def undisclosed_course_codes(text: str, known_courses, campus_equivalents,
                             catalog_aliases=None, quoted_codes=frozenset()) -> set[str]:
    """K4: codes that are not a current course, unless labeled as a program's quote.

    A program line can print a code the Atlas lacks (retired, or never imported).
    Kept with its note it is the catalog's own requirement, not an invented
    course; ``quoted_codes`` are the codes active program lines name, so a
    note on any other code does not pass.
    """
    invalid = set()
    for piece in text.split(PIECE_SEPARATOR):
        bad = invalid_course_codes(set(canonical_codes_in(piece)), known_courses,
                                   campus_equivalents, catalog_aliases)
        invalid |= bad - (_disclosed(piece) & set(quoted_codes))
    return invalid


def program_requirement_check(
    conn,
    collection_id,
    program_title: str,
    artifact_text: str,
    campus_equivalents: dict[str, frozenset[str]],
    completed_courses=(),
    additional_courses=(),
) -> tuple[bool, str, set[str]]:
    """K5's program-truth check, exactly as the benchmark applies it."""
    program_row = conn.execute(
        queries.get("artifact_program_by_title"),
        {"collection_id": collection_id, "title": program_title,
         "title_pattern": f"%{program_title}%"},
    ).fetchone()
    if program_row is None:
        return True, f"program '{program_title}' not in dev database", set()
    items = conn.execute(
        queries.get("list_program_items"), {"program_id": program_row["program_id"]}
    ).fetchall()
    allowed: set[str] = set()
    for item in items:
        for code in _course_codes_in(item["text"]):
            allowed.add(code)
            allowed.update(campus_equivalents.get(code, frozenset()))
    artifact_codes = set(_course_codes_in(artifact_text))
    unsupported = artifact_codes - allowed
    unsupported -= set(completed_courses)
    unsupported -= set(additional_courses)
    ok = len(unsupported) == 0 or len(unsupported) <= len(artifact_codes) * 0.4
    detail = f"{len(artifact_codes) - len(unsupported)}/{len(artifact_codes)} courses in program requirements"
    return ok, detail, unsupported


def plan_order_violations(
    conn,
    collection_id,
    plan_terms: dict[str, int],
    completed_courses,
    campus_equivalents: dict[str, frozenset[str]],
) -> list[str]:
    """K6: no course in or before the term of a required prerequisite."""
    completed = set(completed_courses)
    for code in list(completed):
        completed.update(campus_equivalents.get(code, frozenset()))
    violations: list[str] = []
    for code, term_index in plan_terms.items():
        node_rows = conn.execute(
            queries.get("artifact_prerequisite_nodes_for_code"),
            {"collection_id": collection_id, "code": code},
        ).fetchall()
        if not node_rows:
            continue
        try:
            tree = _restore_tree([dict(row) for row in node_rows])
        except ValueError:
            continue
        required: set[str] = set()
        stack = [tree]
        while stack:
            node = stack.pop()
            if isinstance(node, CourseRef):
                required.add(node.code)
            elif isinstance(node, AllOf):
                stack.extend(node.items)
        earlier = {other for other, other_term in plan_terms.items() if other_term < term_index}
        earlier |= completed
        later_or_same = {other for other, other_term in plan_terms.items()
                         if other_term >= term_index and other != code}
        for prerequisite in required:
            names = {prerequisite} | set(campus_equivalents.get(prerequisite, frozenset()))
            if names & later_or_same and not names & earlier:
                violations.append(f"{code} (term {term_index}) before/with {prerequisite}")
    return violations


def leak_matches(text: str) -> list[str]:
    """Prompt text, model names, status codes, and stack traces that must not leak."""
    leaks = []
    for pattern in FORBIDDEN_LEAK_PATTERNS:
        match = pattern.search(text)
        if match:
            leaks.append(match.group(0))
    return leaks


def evidence_chunk_ids(spec: dict) -> list[str]:
    """Every chunk ID an artifact cites, as strings."""
    refs: list[str] = []
    for ref_list in (spec.get("evidence") or {}).values():
        for ref in ref_list:
            if isinstance(ref, dict):
                refs.append(str(ref.get("chunk_id") or ref.get("chunkId") or ""))
    return refs


def missing_evidence_elements(spec, skip_ids: set[str] | None = None) -> set[str]:
    """Content elements without a source binding, excluding code-built structure."""
    skip_ids = skip_ids or set()
    evidence = set(spec.evidence)
    return {element for element in content_elements(spec)
            if element not in evidence and element not in skip_ids}


def evidence_is_empty(spec: dict) -> bool:
    return not spec.get("evidence")


@dataclass(frozen=True)
class ArtifactCheckFinding:
    """One deterministic problem, named in student-safe language for repair."""

    check: str
    message: str
    student_message: str
    section_id: str | None = None


def _section_for_text(text: str, section_texts: dict[str, str]) -> str | None:
    for section_id, section_text in section_texts.items():
        if text and text in section_text:
            return section_id
    # A course code is reported in canonical form ("MATH_OX 112", "DATASCI 110"); the
    # section may spell it the way its source does ("Math_OX 112", "QTM 110").
    for section_id, section_text in section_texts.items():
        if text and text in canonical_codes_in(section_text):
            return section_id
    return None


def check_generated_artifact(
    conn,
    *,
    collection_id,
    spec,
    known_courses: set[str],
    campus_equivalents: dict[str, frozenset[str]],
    program_title: str | None = None,
    completed_courses=(),
    additional_courses=(),
    plan_terms: dict[str, int] | None = None,
    plan_term_sections: dict[int, str] | None = None,
    section_texts: dict[str, str] | None = None,
    skip_elements: set[str] | None = None,
    catalog_aliases: dict[str, frozenset[str]] | None = None,
    quoted_codes: frozenset[str] = frozenset(),
) -> list[ArtifactCheckFinding]:
    """K4, K5, K6, K7 and K10 on one assembled artifact, attributed to sections."""
    payload = spec.model_dump(mode="json")
    text = extract_artifact_text(payload)
    section_texts = section_texts or {}
    findings: list[ArtifactCheckFinding] = []

    invalid = undisclosed_course_codes(text, known_courses, campus_equivalents,
                                       catalog_aliases, quoted_codes)
    for code in sorted(invalid):
        findings.append(ArtifactCheckFinding(
            "K4", f"{code} is not in the current Course Atlas; remove the code and name the "
            "course by its title instead.",
            "The artifact named a course that is not in the catalog.", _section_for_text(code, section_texts)))

    if program_title:
        ok, _detail, unsupported = program_requirement_check(
            conn, collection_id, program_title, text, campus_equivalents, completed_courses,
            additional_courses)
        if not ok:
            for code in sorted(unsupported):
                findings.append(ArtifactCheckFinding(
                    "K5",
                    f"{code} is not on {program_title}'s requirement lines; use only courses "
                    "the program lists.",
                    "The plan named a course the program does not require.",
                    _section_for_text(code, section_texts)))

    if plan_terms:
        violations = plan_order_violations(conn, collection_id, plan_terms,
                                           completed_courses, campus_equivalents)
        for violation in violations:
            # "MATH 111 (term 2) before/with MATH 110": the course is the
            # code before the term, not its subject alone.
            code = violation.split(" (term ", 1)[0]
            section_id = _section_for_text(code, section_texts)
            if section_id is None and plan_term_sections:
                term = plan_terms.get(code)
                section_id = plan_term_sections.get(term) if term else None
            findings.append(ArtifactCheckFinding(
                "K6", violation + "; move it to a later term.",
                "A course is scheduled before its prerequisite.", section_id))

    missing = missing_evidence_elements(spec, skip_elements)
    for element in sorted(missing):
        findings.append(ArtifactCheckFinding(
            "K7", f"{element} has no source slot; bind it to one of this section's slots.",
            "A part of the artifact had no supporting source.",
            element.split(":", 1)[0] if ":" in element else None))

    for leak in leak_matches(text):
        findings.append(ArtifactCheckFinding(
            "K10", f"Remove the internal text {leak!r} from the artifact.",
            "The artifact contained internal implementation text.",
            _section_for_text(leak, section_texts)))
    return findings
