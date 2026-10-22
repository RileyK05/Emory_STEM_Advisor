"""Build a deterministic review queue from the course master and saved Atlas captures.

This is an offline evidence report. It does not write to the database, fetch pages,
or infer course equivalencies. Run from backend with ``python -m
scripts.manual.review_atlas_cleanup``; pass ``--write`` to update the marked block
in the repository-root ``personal-check.md``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path

from app.atlas.live import DetailRecord, IndexRecord, read_capture
from app.atlas.records import AtlasCourse, load_atlas, normalize_code
from app.atlas.requisites import (
    AllOf,
    AnyOf,
    CourseRef,
    ParsedRequisites,
    parse_requisites,
    requisite_text,
)

BEGIN = "<!-- BEGIN GENERATED ATLAS CLEANUP QUEUE -->"
END = "<!-- END GENERATED ATLAS CLEANUP QUEUE -->"
MAX_PROSE = 360


def _cell(value: object) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|")
    return re.sub(r"\s+", " ", text).strip()


def _clip(text: str | None) -> str:
    if not text:
        return "(none)"
    value = _cell(text)
    return value if len(value) <= MAX_PROSE else value[: MAX_PROSE - 1].rstrip() + "…"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _link(path: Path, root: Path) -> str:
    relative = path.relative_to(root).as_posix()
    return f"[`{relative}`](<{relative}>) (SHA-256 `{_digest(path)}`)"


def _refs(node: object) -> Iterable[str]:
    if isinstance(node, CourseRef):
        yield node.code
    elif isinstance(node, (AllOf, AnyOf)):
        for child in node.items:
            yield from _refs(child)


def _master_findings(courses: list[AtlasCourse], master_path: Path, root: Path) -> tuple[list[str], set[str]]:
    codes = {course.code for course in courses}
    parsed_by_code: dict[str, ParsedRequisites] = {}
    refs_by_code: dict[str, set[str]] = defaultdict(set)
    lines = [
        "### Master prerequisite review",
        (
            "Unresolved targets are exact parser-emitted codes absent from this imported master. "
            "`needsreview` marks higher-numbered prerequisite edges for human review; it is not "
            "a parser error. No Spring 2027 absence is interpreted as course absence. "
            f"Full raw requisite content: {_link(master_path, root)}."
        ),
        "",
        "| Course | Finding | Requisite excerpt |",
        "|---|---|---|",
    ]
    findings = 0
    for course in sorted(courses, key=lambda item: item.code):
        raw = requisite_text(course.requisites, course.description)
        parsed = parse_requisites(raw, course_code=course.code)
        parsed_by_code[course.code] = parsed
        refs = {mention.code for mention in parsed.mentions if mention.kind == "prerequisite"}
        unresolved = sorted({mention.code for mention in parsed.mentions if mention.code not in codes})
        self_reference = any(mention.code == course.code for mention in parsed.mentions)
        if parsed.status in {"ambiguous", "unparsed"}:
            lines.append(f"| `{_cell(course.code)}` | `{parsed.status}` ({_cell(', '.join(parsed.leftover) or 'structure unclear')}) | {_clip(raw)} |")
            findings += 1
        # Only accepted prerequisite trees define graph edges. Mentions from an
        # ambiguous or unparsed clause are evidence, not deterministic edges.
        if parsed.status == "parsed" and "prerequisite" in parsed.trees:
            refs_by_code[course.code].update(_refs(parsed.trees["prerequisite"]))
        if unresolved:
            lines.append(f"| `{_cell(course.code)}` | unresolved exact target(s): `{_cell(', '.join(unresolved))}` | {_clip(raw)} |")
            findings += 1
        if self_reference:
            lines.append(f"| `{_cell(course.code)}` | self-prerequisite reference; explicit review required | {_clip(raw)} |")
            findings += 1
        higher = sorted(
            target for target in refs
            if target in codes and _course_level(target) > _course_level(course.code)
        )
        if higher:
            lines.append(f"| `{_cell(course.code)}` | needsreview: higher-numbered prerequisite edge(s) `{_cell(', '.join(higher))}` | {_clip(raw)} |")
            findings += 1

    # The deterministic parse trees are the only graph source for this report.
    graph = {code: {target for target in refs if target in codes} for code, refs in refs_by_code.items()}
    for component in _strong_components(graph):
        cyclic = len(component) > 1 or (len(component) == 1 and component[0] in graph.get(component[0], set()))
        if cyclic:
            names = ", ".join(f"`{_cell(code)}`" for code in component)
            raw_by_code = {course.code: requisite_text(course.requisites, course.description) for course in courses}
            lines.append(f"| {names} | prerequisite cycle candidate; needsreview | {_clip(raw_by_code.get(component[0]))} |")
            findings += 1
    if findings == 0:
        lines.append("| — | No unresolved or unsupported prerequisite findings in this master snapshot. | — |")
    return lines, codes


def _course_level(code: str) -> int:
    match = re.search(r"\d{3}", code)
    return int(match.group()) // 100 if match else -1


def _strong_components(graph: dict[str, set[str]]) -> list[list[str]]:
    """Tarjan SCCs, bounded to the finite parsed master graph."""
    index = 0
    indices: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = low[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in graph.get(node, set()):
            if target not in indices:
                visit(target)
                low[node] = min(low[node], low[target])
            elif target in on_stack:
                low[node] = min(low[node], indices[target])
        if low[node] == indices[node]:
            component: list[str] = []
            while True:
                child = stack.pop()
                on_stack.remove(child)
                component.append(child)
                if child == node:
                    break
            components.append(sorted(component))

    for node in graph:
        if node not in indices:
            visit(node)
    return components


def _capture_findings(
    capture_dir: Path, root: Path, master_courses: dict[str, AtlasCourse], master_path: Path
) -> tuple[list[str], dict[str, int]]:
    lines = [
        "### Saved Spring 2027 capture evidence",
        (
            "Capture JSON files and their hashes form the complete evidence inventory; only unresolved "
            "items appear below. Offerings demonstrate presence for this term, while an absent offering "
            "does not demonstrate course absence. Group/detail variation and raw restriction text "
            "that differs after case/whitespace-only normalization require review; semantic significance "
            "is unresolved here."
        ),
        "",
        "| Capture | Type / scope | Review item | Exact evidence summary |",
        "|---|---|---|---|",
    ]
    counts = {"files": 0, "valid": 0, "held": 0, "superseded_invalid": 0,
              "unusual": 0, "conflicts": 0, "grouped": 0}
    restrictions: dict[str, list[tuple[Path | None, str, DetailRecord | None]]] = defaultdict(list)
    for code, course in master_courses.items():
        raw = requisite_text(course.requisites, course.description)
        if raw:
            restrictions[code].append((None, raw, None))
    if not capture_dir.exists():
        return lines + [f"| — | directory | capture directory missing: `{_cell(capture_dir)}` | — |"], counts
    valid_indexes: list[tuple[Path, IndexRecord]] = []
    detail_records: list[tuple[Path, DetailRecord]] = []
    invalid: list[tuple[Path, object, str]] = []
    for path in sorted(capture_dir.glob("*.json")):
        counts["files"] += 1
        envelope = None
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            capture = read_capture(envelope)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
            invalid.append((path, envelope, str(exc)))
            continue
        counts["valid"] += 1
        if isinstance(capture, IndexRecord):
            valid_indexes.append((path, capture))
        else:
            detail_records.append((path, capture))
            if capture.restrictions is not None:
                restrictions[capture.code].append((path, capture.restrictions, capture))

    # Only the latest valid capture for each career/term is final. Old failed
    # captures remain in the inventory but stop polluting the uncertainty queue.
    latest_indexes: dict[tuple[str, str], tuple[Path, IndexRecord]] = {}
    for item in valid_indexes:
        path, capture = item
        key = (capture.career.casefold(), capture.term.casefold())
        if key not in latest_indexes or capture.observed_at > latest_indexes[key][1].observed_at:
            latest_indexes[key] = item
    active_indexes = list(latest_indexes.values())
    active_by_career_term = {
        (capture.career.casefold(), capture.term.casefold())
        for _, capture in active_indexes
    }
    for path, envelope, error in invalid:
        if isinstance(envelope, dict) and envelope.get("kind") == "index":
            key = (str(envelope.get("career", "")).casefold(), str(envelope.get("term", "")).casefold())
            if key in active_by_career_term:
                counts["superseded_invalid"] += 1
                continue
        counts["held"] += 1
        meta = " / ".join(_cell(envelope.get(key, "?")) for key in ("career", "term")) if isinstance(envelope, dict) else "unknown scope"
        lines.append(f"| {_link(path, root)} | held invalid final capture | `{meta}`: {_cell(error)} | Review completeness and source identity before relying on it. |")

    # Flag only known anomalies in valid, latest index captures, never dump the
    # complete index. A code absent from the College master alone is expected
    # for other careers and is not treated as evidence of an error.
    career_by_code: dict[str, set[str]] = defaultdict(set)
    for _, capture in active_indexes:
        for row in capture.rows:
            career_by_code[row.code].add(capture.career)
    for path, capture in active_indexes:
        for row in capture.rows:
            placeholder = bool(re.search(r"\b(?:AAAA|TBA|TEST)\b|to be scheduled later|placeholder", row.code + " " + row.title, re.IGNORECASE))
            unusual = normalize_code(row.code) is None
            ambiguous_career = len(career_by_code[row.code]) > 1
            if not (placeholder or unusual or ambiguous_career):
                continue
            if unusual:
                counts["unusual"] += 1
            status = []
            if placeholder:
                status.append("placeholder/test identity")
            if unusual:
                status.append("exact code unsupported by master code shape")
            if ambiguous_career:
                status.append("same exact code appears in multiple career searches")
            lines.append(
                f"| {_link(path, root)} | `{_cell(capture.career)}` / `{_cell(capture.term)}` | "
                f"`{_cell(row.code)}`: {'; '.join(status)} | { _clip(row.title) }; "
                f"group `{_cell(row.group)}`, key `{_cell(row.key)}`, matched `{_cell(row.matched)}` |"
            )

    for path, capture in detail_records:
        unusual = normalize_code(capture.code) is None
        if capture.is_grouped or capture.section_variation_review:
            counts["grouped"] += 1
            crns = ", ".join(section.crn for section in capture.sections)
            lines.append(
                f"| {_link(path, root)} | `{_cell(capture.career)}` / `{_cell(capture.term)}` | "
                f"grouped/section-variation review: `{_cell(capture.code)}`; CRNs `{_cell(crns)}` | "
                f"{_clip(capture.title)}; restrictions: {_clip(capture.restrictions)}; "
                f"notes: {_clip(capture.notes)}; all notices retained in raw capture |"
            )
        if unusual:
            counts["unusual"] += 1
            lines.append(
                f"| {_link(path, root)} | exact code shape review | `{_cell(capture.code)}` unsupported by current master normalizer | "
                f"career `{_cell(capture.career)}`, term `{_cell(capture.term)}`, CRNs "
                f"`{_cell(', '.join(section.crn for section in capture.sections))}` |"
            )

    for code, entries in sorted(restrictions.items()):
        texts: dict[str, list[tuple[Path, DetailRecord]]] = defaultdict(list)
        for path, raw, detail in entries:
            texts[_raw_key(raw)].append((path, detail))
        if len(texts) < 2:
            continue
        counts["conflicts"] += 1
        for raw, grouped in sorted(texts.items()):
            for path, detail in grouped:
                if path is None or detail is None:
                    # The master source is already on disk; cite its exact code
                    # and leave the full prose visible in the clipped cell.
                    course = master_courses[code]
                    source_path = master_path
                    evidence_link = _link(source_path, root) if source_path.exists() else "local master"
                    career, term = "catalog master", "local snapshot"
                    exact_text = requisite_text(course.requisites, course.description)
                else:
                    evidence_link = _link(path, root)
                    career, term = detail.career, detail.term
                    exact_text = detail.restrictions
                lines.append(
                    f"| {evidence_link} | raw restriction text differs; semantics require review | `{_cell(code)}` "
                    f"`{_cell(career)}` / `{_cell(term)}`; comparison ignores only case/whitespace | "
                    f"{_clip(exact_text)}; full content in linked source |"
                )
    return lines, counts


def _raw_key(text: str) -> str:
    return " ".join(text.casefold().split())


def _master_summary(courses: list[AtlasCourse]) -> tuple[list[str], dict[str, int]]:
    codes = {course.code for course in courses}
    statuses: Counter[str] = Counter()
    unresolved: set[str] = set()
    for course in courses:
        parsed = parse_requisites(
            requisite_text(course.requisites, course.description), course_code=course.code,
        )
        statuses[parsed.status] += 1
        unresolved.update(mention.code for mention in parsed.mentions if mention.code not in codes)
    oxford = sum(course.code.split(" ", 1)[0].endswith("_OX") for course in courses)
    counts = {
        "parsed": statuses["parsed"], "none": statuses["none"],
        "ambiguous": statuses["ambiguous"], "unparsed": statuses["unparsed"],
        "unresolved_targets": len(unresolved), "oxford_codes": oxford,
        "other_campus_codes": len(courses) - oxford,
    }
    lines = [
        "### Master coverage",
        (
            "Parser states and unresolved target counts describe engineering coverage for this "
            "snapshot; they are not a student homework list. Oxford codes retain the `_OX` "
            "campus identity."
        ),
        "",
        "| Coverage measure | Count |",
        "|---|---:|",
        f"| Courses with parsed prerequisite text | {counts['parsed']} |",
        f"| Records with parser status `none` | {counts['none']} |",
        f"| Ambiguous parse cases | {counts['ambiguous']} |",
        f"| Unparsed cases | {counts['unparsed']} |",
        f"| Distinct prerequisite/corequisite target codes absent from this master | {counts['unresolved_targets']} |",
        f"| Oxford campus course codes (`_OX`) | {counts['oxford_codes']} |",
        f"| Other campus course codes | {counts['other_campus_codes']} |",
    ]
    return lines, counts


def _atlas_restriction_variants(courses: list[AtlasCourse]) -> list[str]:
    variants = [course for course in courses
                if course.requisites and course.requisites.startswith(
                    "Atlas section restrictions differ")]
    lines = [
        "### Atlas section restriction variants",
        "These course records have distinct restriction text across cited Spring 2027 sections.",
        "",
    ]
    if not variants:
        lines.append("No Atlas-primary course has conflicting section restriction text.")
        return lines
    lines.extend(("| Course | Career and section scope | Exact captured restriction |",
                  "|---|---|---|"))
    for course in sorted(variants, key=lambda item: item.code):
        for item in course.evidence:
            if not isinstance(item, dict):
                continue
            class_number = item.get("class_number")
            scope = (f"class {class_number}" if class_number
                     else f"group `{_cell(item.get('group_identity') or 'unknown')}`")
            context = " / ".join(filter(None, (
                _cell(item.get("career") or "career unknown"), scope,
            )))
            restriction = item.get("restrictions") or "(no restriction text captured)"
            lines.append(
                f"| `{_cell(course.code)}` | {context} | {_clip(str(restriction))} |"
            )
    return lines


def render(
    master_path: Path, capture_dir: Path, root: Path, *, details: bool = False,
) -> tuple[str, dict[str, int]]:
    courses = load_atlas(master_path)
    master_summary, master_counts = _master_summary(courses)
    capture_lines, capture_counts = _capture_findings(
        capture_dir, root, {course.code: course for course in courses}, master_path,
    )
    if details:
        master_lines, _master_codes = _master_findings(courses, master_path, root)
        review_lines = [*master_lines, "", *capture_lines]
    else:
        capture_counts.pop("conflicts", None)
        capture_summary = [
            "### Saved capture inventory",
            "Counts cover saved JSON envelopes; grouped result cards are normal Atlas records.",
            "",
            "| Capture coverage measure | Count |",
            "|---|---:|",
            *[f"| {label} | {capture_counts[key]} |" for key, label in (
                ("files", "Saved capture files"), ("valid", "Valid capture files"),
                ("held", "Invalid final captures held from use"),
                ("superseded_invalid", "Superseded invalid index captures"),
                ("unusual", "Unusual exact code shapes"),
                ("grouped", "Grouped or section-variation cards"),
            )],
        ]
        review_lines = [*master_summary, "", *capture_summary, "",
                        *_atlas_restriction_variants(courses)]
    block = [
        BEGIN,
        "## Generated Atlas cleanup queue",
        (
            "Generated deterministically from the local course master and saved Spring 2027 captures. "
            "This is a review queue, not a verified replacement curriculum or enrollment decision."
        ),
        "",
        *review_lines,
        END,
    ]
    counts = {"master_courses": len(courses), **master_counts, **capture_counts}
    return "\n".join(block), counts


def _replace_marked(existing: str, generated: str) -> str:
    begin_count, end_count = existing.count(BEGIN), existing.count(END)
    if begin_count != end_count or begin_count > 1:
        raise ValueError("personal-check.md has malformed or duplicate generated-section markers")
    if begin_count == 1:
        start = existing.index(BEGIN)
        finish = existing.index(END) + len(END)
        return existing[:start] + generated + existing[finish:]
    separator = "" if not existing or existing.endswith("\n") else "\n"
    return existing + separator + "\n" + generated + "\n"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def main() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description=__doc__)
    default_primary = repo_root / "data/atlas/atlas-primary-spring-2027.jsonl"
    default_master = (default_primary if default_primary.exists()
                      else repo_root / "data/atlas/emory_college.jsonl")
    parser.add_argument("--master", type=Path, default=default_master)
    parser.add_argument("--captures", type=Path, default=repo_root / "data/atlas/spring-2027-capture")
    parser.add_argument("--report", type=Path, default=repo_root / "personal-check.md")
    parser.add_argument("--write", action="store_true", help="atomically replace only this script's marked section")
    parser.add_argument("--details", action="store_true",
                        help="include per-course master findings and per-capture review rows")
    args = parser.parse_args()
    generated, counts = render(args.master, args.captures, repo_root, details=args.details)
    print("Atlas cleanup queue: " + ", ".join(f"{key}={value}" for key, value in counts.items()))
    if args.write:
        existing = args.report.read_bytes().decode("utf-8") if args.report.exists() else ""
        _atomic_write(args.report, _replace_marked(existing, generated))
        print(f"Updated marked section in {args.report}")


if __name__ == "__main__":
    main()
