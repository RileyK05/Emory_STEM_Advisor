"""Audit the published course data: integrity, coverage, unresolved codes, gold set.

Read-only. Four checks, in order:

1. Integrity: each stored tree restores the way advising restores it, its course
   leaves equal its mentions, only parsed courses hold trees, and a fresh parse
   of the stored requisite text reproduces the stored status and trees.
2. Coverage: how many courses with requirement text have a usable tree. The
   planner blocks every course that is not ``parsed`` or ``none``.
3. Resolution: prerequisite links and program slots whose code matches no
   current course, grouped by the likely reason.
4. Gold set: hand-checked expected trees (``configs/requirement_gold.toml``)
   compared with what is stored. Only a person's reading of Emory's page makes
   an entry ground truth; unreviewed entries are reported apart.

Exits 1 when an integrity check or a reviewed gold entry fails. Run from
backend with ``python -m scripts.manual.data_audit --collection NAME``.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from collections import Counter, defaultdict
from pathlib import Path

from app.advising.catalog import _restore_tree
from app.atlas.requisites import (
    AllOf,
    AnyOf,
    Condition,
    CourseRef,
    Req,
    parse_requisites,
)
from app.config import load_settings
from app.db import queries
from scripts._db import open_connection

GOLD = Path(__file__).resolve().parents[2] / "configs" / "requirement_gold.toml"
ATLAS = Path(__file__).resolve().parents[3] / "data" / "atlas" / "atlas-primary.jsonl"
KINDS = ("prerequisite", "corequisite", "recommended")


def _atlas_capture() -> dict[str, str] | None:
    """The Atlas capture's own requirement text, keyed by course code.

    The capture is the primary source. The gold file is compared with it so
    the gold file and the database cannot drift together unnoticed.
    """
    if not ATLAS.exists():
        return None
    from app.atlas.records import load_atlas

    return {course.code: (course.requisites or "") for course in load_atlas(ATLAS)}


def parse_expression(text: str) -> Req:
    """Read the gold notation: all(...), any(...), cond:TEXT, or a course code."""
    text = text.strip()
    match = re.fullmatch(r"(all|any)\((.*)\)", text, re.DOTALL)
    if match:
        parts, depth, start = [], 0, 0
        body = match.group(2)
        for index, char in enumerate(body):
            depth += {"(": 1, ")": -1}.get(char, 0)
            if char == "," and depth == 0:
                parts.append(body[start:index])
                start = index + 1
        parts.append(body[start:])
        items = tuple(parse_expression(part) for part in parts)
        return AllOf(items) if match.group(1) == "all" else AnyOf(items)
    if text.startswith("cond:"):
        return Condition(text[5:].strip())
    return CourseRef(text)


def render(tree: Req) -> str:
    if isinstance(tree, CourseRef):
        return tree.code
    if isinstance(tree, Condition):
        return f"cond:{tree.text}"
    name = "all" if isinstance(tree, AllOf) else "any"
    return f"{name}({', '.join(render(item) for item in tree.items)})"


def canonical(tree: Req) -> str:
    """Render with nested same-type groups flattened and children sorted, so
    any(any(A, B), C) and any(C, A, B) compare equal; they mean the same thing."""
    if isinstance(tree, (CourseRef, Condition)):
        return render(tree)
    kind = type(tree)
    flat: list[Req] = []
    pending = list(tree.items)
    while pending:
        item = pending.pop()
        if isinstance(item, kind):
            pending.extend(item.items)
        else:
            flat.append(item)
    items = sorted(canonical(item) for item in flat)
    if len(items) == 1:
        return items[0]
    return f"{'all' if kind is AllOf else 'any'}({', '.join(items)})"


def leaves(tree: Req) -> set[str]:
    if isinstance(tree, CourseRef):
        return {tree.code}
    if isinstance(tree, Condition):
        return set()
    return set().union(*(leaves(item) for item in tree.items))


def show(title: str, lines: list[str], limit: int) -> None:
    print(f"\n{title}: {len(lines)}")
    for line in lines[:limit]:
        print(f"  {line}")
    if len(lines) > limit:
        print(f"  ... {len(lines) - limit} more (raise --limit to see them)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--collection", default="default")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--gold", type=Path, default=GOLD)
    parser.add_argument("--subjects", default="BIOL,CHEM,CS,DATASCI,MATH,NBB,PHYS",
                        help="comma-separated subjects to break coverage down by")
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()

    with open_connection(load_settings(), args.database_url) as conn:
        collection = conn.execute(queries.get("get_collection_by_name"), {
            "name": args.collection,
        }).fetchone()
        if collection is None:
            parser.error(f"no collection named {args.collection!r}")
        scope = {"collection_id": collection["collection_id"]}
        courses = conn.execute(queries.get("diagnostic_audit_courses"), scope).fetchall()
        nodes = conn.execute(queries.get("diagnostic_audit_prereq_nodes"), scope).fetchall()
        mentions = conn.execute(queries.get("diagnostic_audit_prereq_mentions"), scope).fetchall()
        slots = conn.execute(queries.get("diagnostic_audit_program_slots"), scope).fetchall()
        equivalents = conn.execute(queries.get("list_campus_equivalents"), scope).fetchall()
        retired = {row["code"] for row in conn.execute(
            queries.get("diagnostic_audit_retired_codes"), scope)}

    by_code = {row["code"]: row for row in courses}
    nodes_by = defaultdict(lambda: defaultdict(list))
    for row in nodes:
        nodes_by[row["course_id"]][row["kind"]].append(row)
    mentions_by = defaultdict(lambda: defaultdict(set))
    for row in mentions:
        mentions_by[row["course_id"]][row["kind"]].add(row["target_code"])

    # 1. Integrity
    stored: dict[str, dict[str, Req]] = {}
    problems: list[str] = []
    for pair in equivalents:
        if pair["atlanta_code"] not in by_code and pair["atlanta_code"] not in retired:
            problems.append(f"campus equivalence {pair['oxford_code']} -> "
                            f"{pair['atlanta_code']} names an Atlanta course the "
                            "master does not carry")
    for course in courses:
        code, status = course["code"], course["parse_status"]
        kinds = nodes_by.get(course["course_id"], {})
        trees: dict[str, Req] = {}
        for kind, rows in kinds.items():
            try:
                trees[kind] = _restore_tree(rows)
            except ValueError as exc:
                problems.append(f"{code}: stored {kind} tree is malformed ({exc})")
        stored[code] = trees
        if status == "parsed" and not kinds:
            problems.append(f"{code}: parsed but has no stored tree")
        if status != "parsed" and kinds:
            problems.append(f"{code}: {status} but holds a stored tree")
        if status == "parsed":
            for kind, tree in trees.items():
                if leaves(tree) != mentions_by[course["course_id"]].get(kind, set()):
                    problems.append(f"{code}: {kind} tree leaves differ from its mentions")
        fresh = parse_requisites(course["requisites_raw"], course_code=code)
        if fresh.status != status or (status == "parsed" and fresh.trees != trees):
            problems.append(f"{code}: stored {status} (parser v{course['parser_version']}) "
                            f"but the current parser gives {fresh.status}; republish or reparse")
    show("Integrity problems", problems, args.limit)

    # 2. Coverage
    with_text = [c for c in courses if (c["requisites_raw"] or "").strip()]
    # "none" with text means the text holds only exclusions or pointers.
    usable = [c for c in with_text if c["parse_status"] in {"parsed", "none"}]
    print(f"\nCoverage: {len(usable)} of {len(with_text)} courses with requirement text are "
          f"usable by the planner ({len(courses)} current courses in all)")
    print("  all:", dict(Counter(c["parse_status"] for c in courses)))
    for subject in args.subjects.split(","):
        rows = [c for c in courses if c["subject"] in (subject, f"{subject}_OX")]
        print(f"  {subject:8}", dict(Counter(c["parse_status"] for c in rows)) or "no courses")
    blocked = [f"{c['code']} [{c['parse_status']}] {c['requisites_raw'][:110]}"
               for c in with_text if c["parse_status"] in {"ambiguous", "unparsed"}
               and c["subject"].replace("_OX", "") in args.subjects.split(",")]
    show("Blocked courses in those subjects", blocked, args.limit)

    # 3. Resolution
    subjects = {c["subject"] for c in courses}

    def reason(code: str) -> str:
        if code.split(" ")[0] not in subjects:
            return "subject has no courses"
        variants = sorted(other for other in by_code
                          if other != code and (other.startswith(code) or code.startswith(other)))
        return f"suffix variant exists: {', '.join(variants[:3])}" if variants else "number missing"

    unresolved = Counter(row["target_code"] for row in mentions if row["target_course_id"] is None)
    print(f"\nUnresolved prerequisite links: {sum(unresolved.values())} "
          f"to {len(unresolved)} codes")
    print("  by reason:", dict(Counter(reason(code).split(":")[0] for code in unresolved)))
    show("Top unresolved prerequisite codes",
         [f"{code:14} x{count:<4} {reason(code)}" for code, count in unresolved.most_common()],
         args.limit)

    open_slots = Counter(row["target_code"] for row in slots if row["resolved_code"] is None)
    stale = [row for row in slots if row["resolved_code"] and row["resolved_lifecycle"] != "current"]
    print(f"\nProgram slots: {len(slots)}, unresolved {sum(open_slots.values())} "
          f"({len(open_slots)} codes), resolved to a non-current course {len(stale)}")
    print("  by reason:", dict(Counter(reason(code).split(":")[0] for code in open_slots)))
    example = {row["target_code"]: row["program_title"] for row in slots}
    show("Top unresolved program codes",
         [f"{code:14} x{count:<4} {reason(code):40} e.g. {example[code][:40]}"
          for code, count in open_slots.most_common()], args.limit)

    # 3b. Campus equivalence
    by_basis = Counter(row["basis"] for row in equivalents)
    oxford = {row["oxford_code"] for row in equivalents}
    print(f"\nCampus equivalence: {len(equivalents)} pairs over {len(oxford)} "
          f"Oxford courses, by basis {dict(by_basis)}")
    oxford_unresolved = {code: count for code, count in unresolved.items()
                         if code in oxford}
    print(f"  unresolved links still naming an equivalent Oxford course: "
          f"{sum(oxford_unresolved.values())} to {len(oxford_unresolved)} codes")

    # 4. Gold set
    failures = 0
    if args.gold.exists():
        entries = tomllib.loads(args.gold.read_text(encoding="utf-8")).get("course", [])
        results: dict[bool, list[str]] = {True: [], False: []}
        capture = _atlas_capture()
        for entry in entries:
            code, reviewed = entry["code"], bool(entry.get("reviewed"))
            course = by_code.get(code)
            if course is None:
                results[reviewed].append(f"{code}: not a current course")
                continue
            issues = []
            if (course["requisites_raw"] or "").strip() != entry.get("text", "").strip():
                issues.append("source text changed since the entry was checked")
            # The gold text is checked against the Atlas capture, the primary
            # source, so the gold file and the database cannot drift together
            # and pass unnoticed.
            if capture is not None:
                source = capture.get(code)
                if source is None:
                    issues.append("the course is not in the Atlas capture")
                elif (source or "").strip() != entry.get("text", "").strip():
                    issues.append("the gold text differs from the Atlas capture")
            if course["parse_status"] != entry["status"]:
                issues.append(f"status {course['parse_status']}, expected {entry['status']}")
            expected = {kind: parse_expression(entry[kind]) for kind in KINDS if kind in entry}
            if entry["status"] == "parsed" and (
                    {kind: canonical(tree) for kind, tree in stored[code].items()}
                    != {kind: canonical(tree) for kind, tree in expected.items()}):
                got = {kind: render(tree) for kind, tree in stored[code].items()}
                want = {kind: render(tree) for kind, tree in expected.items()}
                issues.append(f"tree {got}, expected {want}")
            if issues:
                results[reviewed].append(f"{code}: {'; '.join(issues)}")
        reviewed_count = sum(1 for entry in entries if entry.get("reviewed"))
        print(f"\nGold set: {len(entries)} entries, {reviewed_count} reviewed by a person")
        if capture is None:
            print(f"  (no Atlas capture found at {ATLAS}; the gold text was not "
                  "checked against the primary source)")
        show("Reviewed gold mismatches", results[True], args.limit)
        show("Unreviewed gold mismatches", results[False], args.limit)
        failures = len(results[True])
    else:
        print(f"\nGold set: {args.gold} not found")

    sys.exit(1 if problems or failures else 0)


if __name__ == "__main__":
    main()
