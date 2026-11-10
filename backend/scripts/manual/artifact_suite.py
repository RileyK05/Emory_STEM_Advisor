"""Live artifact suite: generated cases, repeated runs, and a diagnosis of every failure.

The frozen benchmark (`artifact_benchmark`, 30 hand-written artifact cases) is the
P3.2 bar. This suite is the wider net around it. It generates cases from the
development database, so every program, course and Oxford pairing it names is
real, and a new seed draws a new sample instead of the same 30 prompts. It runs
the cases through the unchanged benchmark runner and grades them with the same
K-checks, repeats the run to expose flaky cases, and reads each case back from
the database to say where it failed: no handoff, outline, sections, checks,
deadline or provider.

Manual and live: it calls the configured model and costs real tokens. Never add
it to the test suite.

Usage, from backend/::

    python -m scripts.manual.artifact_suite generate --seed 11 --per-family 4 --out CASES.jsonl
    python -m scripts.manual.artifact_suite run --cases CASES.jsonl --repeat 3 --out-dir DIR
    python -m scripts.manual.artifact_suite report --dir DIR [--out SUMMARY.json]

`run` runs each case in its own benchmark process (a revise case together with
the case it revises) and keeps each one's report and diagnosis under `runN/`, so
a stopped run continues with `--resume` and loses at most the case in progress.
When a repeat finishes it writes the merged `runN.json`, `runN.log` and
`diagN.json`. `report` reads them back; it needs no model. A case takes one to
four minutes, so a 50-case repeat takes about two hours.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.advising.catalog import load_campus_equivalents
from app.artifacts.checks import extract_artifact_text, extract_planned_terms
from app.atlas.requisites import canonical_codes_in
from app.config import load_settings
from app.db import queries
from app.db.connection import open_connection

WORKSPACE = "development"

# The advisor is for STEM students; humanities programs are drawn only with
# --all-programs.
STEM_DEPARTMENTS = (
    "Biology", "Chemistry", "Computer Science", "Data and Decision Sciences",
    "Environmental Sciences", "Human Health", "Mathematics", "Neuroscience",
    "Physics", "Psychology", "Economics",
)

# Each family is one kind of request. `artifact` families must publish an
# artifact; `control` families must not. Phrasings vary so a pass is not tied
# to one sentence.
FAMILIES = {
    "plan_fresh": [
        "Make me a four-semester plan for the {p} I can print.",
        "Plan out my {p} semester by semester.",
        "Can you put together a printable plan for the {p}?",
        "Build me a four-year plan for the {p}.",
    ],
    "plan_completed": [
        "I've done {c1} and {c2}. Make a plan for the rest of the {p}.",
        "I already took {c1} and {c2}. Plan the rest of my {p}.",
    ],
    "plan_oxford": [
        "I took {c1} and {c2} at Oxford. Plan my {p} from here.",
        "I finished {c1} and {c2} on the Oxford campus. Make me a plan for the {p}.",
    ],
    "checklist": [
        "Make a checklist of everything the {p} requires.",
        "Give me a requirements checklist for the {p}.",
        "I want a printable checklist for the {p}.",
    ],
    "workbook_tracker": [
        "Make a spreadsheet that tracks my {p} credits, with totals.",
        "Build a spreadsheet to track the {p} requirements.",
    ],
    "workbook_plan": [
        "A semester-by-semester spreadsheet for the {p} with a credit column.",
        "Make a spreadsheet plan for the {p}, one row per course and term.",
    ],
    "compare": [
        "Compare the {a} and {b} in a document I can keep.",
        "Make a side-by-side comparison of the {a} and the {b} I can print.",
    ],
    "html_page": [
        "Make a one-page web page roadmap of the {p}.",
        "Make a web page I can share that lays out the {p}.",
    ],
    "revise": [
        "Make the first term lighter.",
        "Add a short note at the top about meeting my advisor.",
        "Add a section for electives.",
    ],
    "summary": [
        "Summarize this conversation into a document I can keep.",
        "Turn this chat into a document I can print.",
    ],
    "control_factual": [
        "What does {c1} require?",
        "How many hours is the {p}?",
        "Does the {p} require {c1}?",
    ],
    "control_fake_course": [
        "Make a plan that includes {fake}.",
    ],
    "control_fake_program": [
        "Make me a plan for the {fake} BS.",
    ],
}
FAKE_PROGRAMS = ("Underwater Basket Weaving", "Quantum Gastronomy", "Competitive Astrology",
                 "Cryptozoology", "Theoretical Juggling")


# --- generation -------------------------------------------------------------


_ACRONYMS = {"BBA", "QTM", "NBB", "BA", "BS"}


def _course_number(code: str) -> int:
    match = re.search(r"(\d{3})", code)
    return int(match.group(1)) if match else 999


@dataclass
class Program:
    title: str
    kind: str
    degree: str
    department: str
    codes: list[str]
    entry: list[str]

    @property
    def spoken(self) -> str:
        """How a student names it: "Computer Science BS", "Astronomy minor"."""
        match = re.match(r"^(.*?)\s*\((.*)\)\s*$", self.title)
        name, tag = (match.group(1), match.group(2)) if match else (self.title, self.degree)
        if name.isupper():
            name = " ".join(word if word in _ACRONYMS else
                            word.lower() if word.lower() in {"and", "of", "for"} else
                            word.capitalize() for word in name.split())
        tag = tag.upper() if tag.upper() in {"BA", "BS", "BBA"} else tag.lower()
        return f"{name} {tag}"


def _load_programs(conn, collection_id, *, all_programs: bool) -> list[Program]:
    rows = conn.execute(queries.get("list_programs"), {"collection_id": collection_id}).fetchall()
    programs = []
    for row in rows:
        department = row["department"] or ""
        if not all_programs and not any(name in department for name in STEM_DEPARTMENTS):
            continue
        items = conn.execute(queries.get("list_program_items"),
                             {"program_id": row["program_id"]}).fetchall()
        codes = sorted({code for item in items for code in (item["courses"] or [])})
        if len(codes) < 4:
            continue
        # A first-year course: below 300 and no parsed prerequisite. A senior
        # seminar with no parsed prerequisite is not something a student
        # "already took" early.
        entry = [code for code in codes if _course_number(code) < 300 and not conn.execute(
            queries.get("artifact_prerequisite_nodes_for_code"),
            {"collection_id": collection_id, "code": code}).fetchall()]
        programs.append(Program(row["title"], row["kind"] or "", row["degree"] or "",
                                department, codes, entry))
    conn.commit()
    return programs


def _fake_course(program: Program, known: set[str], rng: random.Random) -> str:
    subject = program.codes[0].split(" ")[0]
    for _ in range(50):
        code = f"{subject} {rng.randint(900, 989)}"
        if code not in known:
            return code
    return f"{subject} 999"


def _oxford_pair(program: Program, equivalents) -> list[str] | None:
    pairs = [twin for code in program.entry or program.codes
             for twin in sorted(equivalents.get(code, ())) if "_OX" in twin]
    return pairs[:2] if len(pairs) >= 2 else None


def _comparison_pairs(programs: list[Program]) -> list[tuple[Program, Program]]:
    by_department = defaultdict(list)
    for program in programs:
        by_department[program.department].append(program)
    pairs = []
    for group in by_department.values():
        group = sorted(group, key=lambda p: p.title)
        for i, first in enumerate(group):
            for second in group[i + 1:]:
                pairs.append((first, second))
    return pairs


def generate(args) -> None:
    rng = random.Random(args.seed)
    settings = load_settings()
    with open_connection(settings) as conn:
        collection = conn.execute(queries.get("get_collection_by_name"),
                                  {"name": settings.default_collection}).fetchone()
        if collection is None:
            raise SystemExit(f"collection {settings.default_collection!r} not found")
        collection_id = collection["collection_id"]
        known = {row["code"] for row in conn.execute(
            queries.get("list_collection_course_codes"), {"collection_id": collection_id})}
        equivalents = load_campus_equivalents(conn, collection_id=collection_id)
        programs = _load_programs(conn, collection_id, all_programs=args.all_programs)
    if not programs:
        raise SystemExit("no programs with at least four course mentions")
    majors = [p for p in programs if p.kind == "major"] or programs
    cases: list[dict[str, Any]] = []
    revise_bases: dict[str, list[str]] = defaultdict(list)

    def add(family, conversation, expected, **meta):
        case_id = f"G{args.seed}-{family}-{sum(c['suite']['family'] == family for c in cases) + 1}"
        artifact_case = family not in {"control_factual", "control_fake_course",
                                       "control_fake_program"}
        cases.append({"id": case_id, "category": "artifact" if artifact_case else "control",
                      "conversation": conversation, "expected": expected,
                      "suite": {"family": family, "seed": args.seed, **meta}})
        return case_id

    def pick(pool, n):
        return rng.sample(pool, min(n, len(pool)))

    def phrase(family):
        return rng.choice(FAMILIES[family])

    per = args.per_family
    for p in pick(majors, per):
        case_id = add("plan_fresh", [phrase("plan_fresh").format(p=p.spoken)],
                      {"artifact": True, "kind": "document", "purpose": "create",
                       "program": p.title, "plan": True}, program=p.title)
        revise_bases["document"].append(case_id)
    for p in pick([p for p in majors if len(p.entry) >= 2], per):
        done = sorted(rng.sample(p.entry, 2))
        add("plan_completed", [phrase("plan_completed").format(p=p.spoken, c1=done[0], c2=done[1])],
            {"artifact": True, "kind": "document", "purpose": "create", "program": p.title,
             "completed_courses": done, "plan": True, "no_completed_in_plan": True},
            program=p.title)
    oxford = [(p, pair) for p in majors if (pair := _oxford_pair(p, equivalents))]
    for p, pair in pick(oxford, per):
        add("plan_oxford", [phrase("plan_oxford").format(p=p.spoken, c1=pair[0], c2=pair[1])],
            {"artifact": True, "kind": "document", "purpose": "create", "program": p.title,
             "completed_courses": pair, "plan": True, "no_completed_in_plan": True},
            program=p.title)
    for p in pick(programs, per):
        add("checklist", [phrase("checklist").format(p=p.spoken)],
            {"artifact": True, "kind": "document", "purpose": "create",
             "program": p.title, "checklist": True}, program=p.title)
    for p in pick(majors, per):
        text = phrase("workbook_tracker")
        case_id = add("workbook_tracker", [text.format(p=p.spoken)],
                      {"artifact": True, "kind": "workbook", "purpose": "create",
                       "program": p.title, **({"totals_are_formulas": True}
                                               if "total" in text else {})},
                      program=p.title)
        revise_bases["workbook"].append(case_id)
    for p in pick(majors, per):
        add("workbook_plan", [phrase("workbook_plan").format(p=p.spoken)],
            {"artifact": True, "kind": "workbook", "purpose": "create",
             "program": p.title, "plan": True}, program=p.title)
    for a, b in pick(_comparison_pairs(programs), per):
        add("compare", [phrase("compare").format(a=a.spoken, b=b.spoken)],
            {"artifact": True, "kind": "document", "purpose": "create",
             "comparison": [a.title, b.title]}, programs=[a.title, b.title])
    for p in pick(programs, per):
        case_id = add("html_page", [phrase("html_page").format(p=p.spoken)],
                      {"artifact": True, "kind": "html", "purpose": "create",
                       "program": p.title}, program=p.title)
        revise_bases["html"].append(case_id)
    bases = [(kind, case_id) for kind, ids in revise_bases.items() for case_id in ids]
    by_id = {case["id"]: case for case in cases}
    for kind, base_id in pick(bases, per):
        base = by_id[base_id]
        instruction = {"workbook": "Add a row for a summer course and update the total.",
                       "html": "Add a section for electives."}.get(kind, phrase("revise"))
        add("revise", [base["conversation"][0], instruction],
            {"artifact": True, "kind": kind, "purpose": "revise", "revises_case": base_id,
             "new_revision": True, "parent_unchanged": True}, base=base_id)
    for p in pick([p for p in majors if p.entry], per):
        course = rng.choice(p.entry)
        add("summary", [f"What are the core requirements for the {p.spoken}?",
                        f"Does the {p.spoken} require {course}?",
                        phrase("summary")],
            {"artifact": True, "kind": "document", "purpose": "summary"}, program=p.title)
    for p in pick(programs, per):
        course = rng.choice(p.codes)
        add("control_factual", [phrase("control_factual").format(p=p.spoken, c1=course)],
            {"artifact": False, "answer": True}, program=p.title)
    for p in pick(majors, max(1, per // 2)):
        fake = _fake_course(p, known, rng)
        add("control_fake_course", [phrase("control_fake_course").format(p=p.spoken, fake=fake)],
            {"artifact": False, "no_hallucinated_course": fake}, program=p.title)
    for fake in pick(list(FAKE_PROGRAMS), max(1, per // 2)):
        add("control_fake_program", [phrase("control_fake_program").format(fake=fake)],
            {"artifact": False, "unknown_program": True})

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases),
                   encoding="utf-8")
    counts = Counter(case["suite"]["family"] for case in cases)
    print(f"wrote {len(cases)} cases to {out} (seed {args.seed}, {len(programs)} programs)")
    for family in FAMILIES:
        print(f"  {family:<22} {counts.get(family, 0)}")


# --- running ----------------------------------------------------------------


def run(args) -> None:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cases_path = Path(args.cases).resolve()
    cases = _read_cases(cases_path)
    (out_dir / "meta.json").write_text(json.dumps({
        "cases": str(cases_path), "repeat": args.repeat, "started": _now().isoformat(),
    }, indent=2), encoding="utf-8")
    units = _units(cases)
    for repeat in range(args.repeat):
        report = out_dir / f"run{repeat}.json"
        if args.resume and (out_dir / f"diag{repeat}.json").exists():
            print(f"repeat {repeat}: already done, skipped")
            continue
        unit_dir = out_dir / f"run{repeat}"
        unit_dir.mkdir(exist_ok=True)
        if not args.resume:
            for stale in unit_dir.iterdir():
                stale.unlink()
        _settle_open_jobs()
        print(f"repeat {repeat + 1}/{args.repeat}: {len(cases)} cases", flush=True)
        started = _now()
        for index, unit in enumerate(units):
            name = f"{index:02d}"
            bench_path, diag_path = unit_dir / f"{name}.json", unit_dir / f"{name}.diag.json"
            if bench_path.exists() and diag_path.exists():
                continue
            unit_cases = unit_dir / f"{name}.cases.jsonl"
            unit_cases.write_text("".join(json.dumps(case) + "\n" for case in unit),
                                  encoding="utf-8")
            unit_started = _now()
            with (unit_dir / f"{name}.log").open("w", encoding="utf-8") as log:
                subprocess.run([sys.executable, "-m", "scripts.manual.artifact_benchmark",
                                "--cases", str(unit_cases), "--out", str(bench_path)],
                               stdout=log, stderr=subprocess.STDOUT, check=False)
            diagnosis = diagnose(unit, unit_started, _now())
            diag_path.write_text(json.dumps(diagnosis, indent=1, default=str), encoding="utf-8")
            passed = _passed_ids(bench_path)
            print(f"  [{index + 1}/{len(units)}] " + ", ".join(
                f"{case['id']} {'PASS' if case['id'] in passed else 'FAIL'}" for case in unit),
                flush=True)
        results, diagnosis, logs = [], {}, []
        for index in range(len(units)):
            name = f"{index:02d}"
            if (unit_dir / f"{name}.json").exists():
                results += json.loads((unit_dir / f"{name}.json").read_text(encoding="utf-8"))["results"]
            diagnosis.update(json.loads((unit_dir / f"{name}.diag.json").read_text(encoding="utf-8")))
            logs.append((unit_dir / f"{name}.log").read_text(encoding="utf-8"))
        report.write_text(json.dumps({"total_cases": len(results),
                                      "passed_cases": sum(1 for row in results if row["passed"]),
                                      "results": results}, indent=2), encoding="utf-8")
        (out_dir / f"run{repeat}.log").write_text("".join(logs), encoding="utf-8")
        (out_dir / f"diag{repeat}.json").write_text(
            json.dumps(diagnosis, indent=1, default=str), encoding="utf-8")
        passed = _passed_ids(report)
        print(f"  {len(passed)}/{len(cases)} passed in {(_now() - started).total_seconds() / 60:.0f} min",
              flush=True)
    print(f"done; summarize with: python -m scripts.manual.artifact_suite report --dir {out_dir}")


def _units(cases) -> list[list[dict]]:
    """Cases that share one benchmark process: a revise case runs with the case it revises,
    because the benchmark attaches the earlier artifact only from its own run."""
    units: list[list[dict]] = []
    owner: dict[str, int] = {}
    for case in cases:
        base = case["expected"].get("revises_case")
        if base in owner:
            units[owner[base]].append(case)
            owner[case["id"]] = owner[base]
            continue
        owner[case["id"]] = len(units)
        units.append([case])
    return units


def _settle_open_jobs() -> None:
    """Close what a stopped run left behind, so K1's empty-queue check holds again.

    Queued benchmark jobs are cancelled at once. A running one belonged to the stopped
    process: it is asked to cancel, its lease is left to expire (at most the benchmark's
    600 s), and expired leases are recovered. Orphaned conversation requests are settled.
    """
    from app.config import configure_runtime_database
    from app.conversations.recovery import reconcile_pending_requests
    from app.db.connection import close_pools, connector, resolved_database_url
    from app.jobs.store import JobStore

    settings = load_settings()
    with open_connection(settings) as conn:
        open_jobs = conn.execute(queries.get("artifact_suite_open_jobs"),
                                 {"workspace_key": WORKSPACE}).fetchall()
        dsn = resolved_database_url(settings, conn)
    if not open_jobs:
        return
    configure_runtime_database(dsn)
    store = JobStore(connector(replace(settings, database_url=dsn)))
    try:
        for job in open_jobs:
            store.cancel(job["job_id"])
        latest = max((job["lease_expires_at"] for job in open_jobs
                      if job["lease_expires_at"] is not None), default=None)
        if latest is not None and latest > _now():
            wait = (latest - _now()).total_seconds() + 2
            print(f"waiting {wait:.0f} s for a stopped run's lease to expire", flush=True)
            time.sleep(wait)
        store.recover_expired()
        with open_connection(settings) as conn:
            reconcile_pending_requests(conn, older_than_seconds=1)
            conn.commit()
    finally:
        close_pools()
    print(f"settled {len(open_jobs)} job(s) left by a stopped run", flush=True)


def diagnose(cases, since, until) -> dict[str, dict]:
    """Read each case's conversation, research turn and artifact run back from the database."""
    settings = load_settings()
    titles = [f"Benchmark {case['id']}" for case in cases]
    expected = {case["id"]: case for case in cases}
    found: dict[str, dict] = {}
    with open_connection(settings) as conn:
        program_codes = {}
        collection = conn.execute(queries.get("get_collection_by_name"),
                                  {"name": settings.default_collection}).fetchone()
        rows = conn.execute(queries.get("artifact_suite_conversations"), {
            "workspace_key": WORKSPACE, "titles": titles, "since": since, "until": until,
        }).fetchall()
        for row in rows:
            case_id = row["title"].removeprefix("Benchmark ")
            case = expected[case_id]
            found[case_id] = _diagnose_case(conn, case, row["conversation_id"],
                                            collection["collection_id"], program_codes)
        conn.commit()
    for case_id in expected:
        found.setdefault(case_id, {"outcome": "not_run"})
    return found


def _diagnose_case(conn, case, conversation_id, collection_id, program_codes) -> dict:
    turns = conn.execute(queries.get("artifact_suite_turns"),
                         {"conversation_id": conversation_id}).fetchall()
    answers = [turn for turn in turns if turn["role"] == "assistant"]
    last = answers[-1] if answers else None
    evidence = (last["evidence"] or {}) if last else {}
    research = evidence.get("research") or {}
    trace_id = evidence.get("traceId")
    result: dict[str, Any] = {
        "turn_status": last["status"] if last else None,
        "research_outcome": research.get("outcome"),
        "artifact_requested": bool(evidence.get("artifactRun")),
        "artifact_notice": evidence.get("artifactNotice"),
        "research_calls": _calls(conn, trace_id, trace_id) if trace_id else None,
    }
    jobs = conn.execute(queries.get("artifact_suite_run_job"),
                        {"key_prefix": f"turn-artifact:{conversation_id}:%"}).fetchall()
    job = jobs[-1] if jobs else None
    if job is not None:
        stages = [row["stage"] for row in conn.execute(
            queries.get("artifact_suite_job_stages"), {"job_id": job["job_id"]})]
        refusals = [{"tool": row["tool_id"], "error": json.loads(row["result_json"]).get("error", "")}
                    for row in conn.execute(queries.get("artifact_suite_tool_refusals"),
                                            {"operation_id": job["job_id"]})]
        seconds = ((job["finished_at"] - job["created_at"]).total_seconds()
                   if job["finished_at"] else None)
        result.update({
            "run_status": job["status"],
            "run_error": (job["error"] or {}).get("message") if job["error"] else None,
            "run_error_type": (job["error"] or {}).get("type") if job["error"] else None,
            "last_stage": stages[-1] if stages else None,
            "stages": stages,
            "refusals": refusals,
            "run_calls": _calls(conn, job["job_id"], None),
            "run_seconds": seconds,
        })
        revisions = ((job["result"] or {}).get("artifactRevisions") or [])
        if revisions:
            spec_row = conn.execute(queries.get("artifact_suite_revision_spec"),
                                    {"revision_id": revisions[0]["revisionId"]}).fetchone()
            if spec_row is not None:
                result["quality"] = _quality(conn, case, spec_row["spec"], collection_id,
                                             program_codes)
    result["outcome"] = _classify(case, result)
    return result


def _calls(conn, operation_id, trace_id) -> dict:
    row = conn.execute(queries.get("artifact_suite_model_calls"),
                       {"operation_id": operation_id, "trace_id": trace_id}).fetchone()
    return {key: int(row[key]) for key in ("calls", "prompt_tokens", "completion_tokens",
                                           "truncated", "failed")}


def _quality(conn, case, spec, collection_id, program_codes) -> dict:
    """Measures the K-checks do not gate: coverage, term load, duplicates."""
    expected = case["expected"]
    text = extract_artifact_text(spec)
    codes = set(canonical_codes_in(text))
    quality: dict[str, Any] = {"courses_named": len(codes),
                               "degraded_sections": (spec.get("provenance") or {}).get(
                                   "degraded_sections", 0)}
    program = expected.get("program")
    if program and (expected.get("plan") or expected.get("checklist")):
        if program not in program_codes:
            row = conn.execute(queries.get("artifact_program_by_title"), {
                "collection_id": collection_id, "title": program,
                "title_pattern": f"%{program}%"}).fetchone()
            items = conn.execute(queries.get("list_program_items"),
                                 {"program_id": row["program_id"]}).fetchall() if row else []
            program_codes[program] = {code for item in items for code in (item["courses"] or [])}
        required = program_codes[program]
        if required:
            quality["program_coverage"] = round(len(codes & required) / len(required), 3)
    if expected.get("plan"):
        terms = extract_planned_terms(spec)
        loads = Counter(terms.values())
        quality["terms"] = len(loads)
        quality["max_term_load"] = max(loads.values()) if loads else 0
        quality["completed_scheduled"] = sorted(
            set(expected.get("completed_courses", [])) & set(terms))
        mentions = Counter(code for code in canonical_codes_in(text))
        quality["repeated_codes"] = sum(1 for count in mentions.values() if count > 2)
    return quality


def _classify(case, result) -> str:
    """Where a case stopped. `published` means the run published; the K-checks grade it."""
    wants = case["expected"].get("artifact")
    if result.get("turn_status") in {None, "failed", "abandoned"}:
        return "turn_failed"
    if wants is False:
        return "control_made_artifact" if result["artifact_requested"] else "control_ok"
    if not result["artifact_requested"]:
        return "no_handoff"
    status = result.get("run_status")
    if status == "succeeded":
        return "published"
    if status is None:
        return "run_missing"
    error_type = result.get("run_error_type") or ""
    stage = result.get("last_stage") or ""
    if error_type == "deadline_exceeded":
        return "deadline"
    if error_type == "provider_failed":
        return "provider"
    if error_type == "tool_failed" and not result.get("refusals"):
        # The controller gave up before any tool ran; the job's log line names the stage.
        return "tool_conversation"
    if stage == "outlining" or stage == "planning":
        return "outline_exhausted"
    if stage in {"writing", "repairing"}:
        return "section_exhausted"
    if stage == "checking":
        return "checks_failed"
    return f"failed_{stage or 'unknown'}"


# --- report -----------------------------------------------------------------


def report(args) -> None:
    out_dir = Path(args.dir)
    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    cases = {case["id"]: case for case in _read_cases(Path(meta["cases"]))}
    runs = []
    for path in sorted(out_dir.glob("run*.json")):
        repeat = path.stem.removeprefix("run")
        diag_path = out_dir / f"diag{repeat}.json"
        if not diag_path.exists():
            continue
        bench = {row["case_id"]: row for row in json.loads(path.read_text(encoding="utf-8"))["results"]}
        diag = json.loads(diag_path.read_text(encoding="utf-8"))
        runs.append((bench, diag))
    if not runs:
        raise SystemExit("no finished repeats in " + str(out_dir))

    rows = []
    for repeat, (bench, diag) in enumerate(runs):
        for case_id, case in cases.items():
            graded = bench.get(case_id)
            rows.append({
                "repeat": repeat, "case": case_id, "family": case["suite"]["family"],
                "kind": case["expected"].get("kind", "-"),
                "passed": bool(graded and graded["passed"]),
                "checks": {c["check"]: c["ok"] for c in (graded or {}).get("checks", [])},
                "turn_seconds": (graded or {}).get("turn_seconds"),
                "run_seconds": (graded or {}).get("run_seconds"),
                "diag": diag.get(case_id, {}),
            })
            base = case["expected"].get("revises_case")
            base_graded = bench.get(base) if base else None
            if base and not (base_graded and base_graded["passed"]
                             or any(c["check"] == "K3" and c["ok"]
                                    for c in (base_graded or {}).get("checks", []))):
                # Nothing to revise: this measures the base case, so it stays out of the rates.
                rows[-1]["blocked"] = True
                rows[-1]["diag"] = {**rows[-1]["diag"], "outcome": "revise_blocked"}

    print(f"{len(runs)} repeat(s) x {len(cases)} cases = {len(rows)} case runs\n")
    _family_table(rows)
    _check_table(rows)
    _outcome_table(rows)
    _flaky(rows, len(runs))
    _timing_table(rows)
    _quality_table(rows)
    _refusals(rows)
    if args.out:
        Path(args.out).write_text(json.dumps(_summary(rows, len(runs)), indent=2, default=str),
                                  encoding="utf-8")
        print(f"wrote {args.out}")


def _wilson(passed: int, total: int) -> tuple[float, float]:
    """95% Wilson interval: honest bounds for a small sample."""
    if total == 0:
        return (0.0, 0.0)
    z = 1.96
    p = passed / total
    centre = (p + z * z / (2 * total)) / (1 + z * z / total)
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return (max(0.0, centre - half), min(1.0, centre + half))


def _family_table(rows) -> None:
    print("PASS RATE BY FAMILY (case runs, 95% Wilson interval)")
    groups = defaultdict(list)
    rows = [row for row in rows if not row.get("blocked")]
    for row in rows:
        groups[row["family"]].append(row["passed"])
    total = [row["passed"] for row in rows]
    for family in [f for f in FAMILIES if f in groups] + ["ALL"]:
        values = total if family == "ALL" else groups[family]
        low, high = _wilson(sum(values), len(values))
        print(f"  {family:<22} {sum(values):>3}/{len(values):<3} {sum(values) / len(values):>5.0%}"
              f"   [{low:.0%}-{high:.0%}]")
    print()


def _check_table(rows) -> None:
    print("K-CHECKS (applicable case runs that failed)")
    counts = defaultdict(lambda: [0, 0])
    for row in rows:
        for check, ok in row["checks"].items():
            counts[check][0] += 0 if ok else 1
            counts[check][1] += 1
    print("  " + "  ".join(f"{check}:{bad}/{total}" for check, (bad, total) in
                           sorted(counts.items(), key=lambda item: int(item[0][1:]))))
    print()


def _outcome_table(rows) -> None:
    print("WHERE CASES STOPPED")
    outcomes = Counter(row["diag"].get("outcome", "not_run") for row in rows)
    for outcome, count in outcomes.most_common():
        families = Counter(row["family"] for row in rows if row["diag"].get("outcome") == outcome)
        top = ", ".join(f"{f} {n}" for f, n in families.most_common(4))
        print(f"  {outcome:<22} {count:>4}   {top}")
    print()


def _flaky(rows, repeats) -> None:
    if repeats < 2:
        return
    by_case = defaultdict(list)
    for row in rows:
        by_case[row["case"]].append(row["passed"])
    flaky = sorted(case for case, results in by_case.items() if any(results) and not all(results))
    never = sorted(case for case, results in by_case.items() if not any(results))
    print(f"FLAKY ({len(flaky)} pass some repeats): " + ", ".join(flaky[:30]))
    print(f"NEVER PASS ({len(never)}): " + ", ".join(never[:30]))
    print()


def _percentile(values, fraction) -> float | None:
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    index = min(len(values) - 1, max(0, math.ceil(fraction * len(values)) - 1))
    return values[index]


def _fmt(value, unit="") -> str:
    return "-" if value is None else f"{value:.0f}{unit}"


def _timing_table(rows) -> None:
    print("TIME AND COST BY KIND (p50 / p95)")
    print("  kind        turn s      run s      run calls   run out tokens   truncated calls")
    for kind in sorted({row["kind"] for row in rows}):
        group = [row for row in rows if row["kind"] == kind]
        calls = [(row["diag"].get("run_calls") or {}).get("calls") for row in group]
        tokens = [(row["diag"].get("run_calls") or {}).get("completion_tokens") for row in group]
        truncated = [(row["diag"].get("run_calls") or {}).get("truncated") for row in group]
        turn = [row["turn_seconds"] for row in group]
        run_s = [row["run_seconds"] or None for row in group]
        print(f"  {kind:<10} {_fmt(_percentile(turn, .5)):>4} / {_fmt(_percentile(turn, .95)):<4}"
              f" {_fmt(_percentile(run_s, .5)):>4} / {_fmt(_percentile(run_s, .95)):<4}"
              f" {_fmt(_percentile(calls, .5)):>4} / {_fmt(_percentile(calls, .95)):<5}"
              f" {_fmt(_percentile(tokens, .5)):>7} / {_fmt(_percentile(tokens, .95)):<8}"
              f" {sum(t or 0 for t in truncated):>5}")
    print()


def _quality_table(rows) -> None:
    published = [row for row in rows if row["diag"].get("quality")]
    if not published:
        return
    print("QUALITY OF PUBLISHED ARTIFACTS (not gated by the K-checks)")
    coverage = [row["diag"]["quality"]["program_coverage"] for row in published
                if "program_coverage" in row["diag"]["quality"]]
    if coverage:
        print(f"  program coverage (plans, checklists): mean {statistics.mean(coverage):.0%},"
              f" min {min(coverage):.0%}, below 50%: {sum(c < .5 for c in coverage)}")
    loads = [row["diag"]["quality"].get("max_term_load") for row in published
             if row["diag"]["quality"].get("max_term_load") is not None]
    if loads:
        print(f"  heaviest term: p50 {_percentile(loads, .5)} courses, max {max(loads)}")
    completed = sum(1 for row in published if row["diag"]["quality"].get("completed_scheduled"))
    degraded = sum(1 for row in published if row["diag"]["quality"].get("degraded_sections"))
    print(f"  plans scheduling a completed course: {completed};"
          f" artifacts with code-written fallback sections: {degraded}")
    print()


def _normalize(message: str) -> str:
    message = re.sub(r"\b[A-Z]{2,5}(?:_OX)? \d{3}[A-Z]{0,2}\b", "CODE", message)
    message = re.sub(r"'[^']*'", "'…'", message)
    message = re.sub(r"\d+", "N", message)
    return message[:140]


def _refusals(rows) -> None:
    refusals = Counter()
    for row in rows:
        for item in row["diag"].get("refusals") or []:
            refusals[(item["tool"], _normalize(item["error"]))] += 1
    if not refusals:
        return
    print("MOST COMMON STAGE REFUSALS (what the model keeps getting wrong)")
    for (tool, message), count in refusals.most_common(12):
        print(f"  {count:>4}  {tool.removeprefix('advisor_')}: {message}")
    print()


def _summary(rows, repeats) -> dict:
    families = defaultdict(list)
    for row in rows:
        families[row["family"]].append(row["passed"])
    return {
        "repeats": repeats, "case_runs": len(rows),
        "passed": sum(row["passed"] for row in rows),
        "families": {f: {"passed": sum(v), "runs": len(v), "interval": _wilson(sum(v), len(v))}
                     for f, v in families.items()},
        "outcomes": Counter(row["diag"].get("outcome", "not_run") for row in rows),
        "rows": rows,
    }


# --- helpers ----------------------------------------------------------------


def _read_cases(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _passed_ids(report_path: Path) -> set[str]:
    if not report_path.exists():
        return set()
    return {row["case_id"] for row in json.loads(report_path.read_text(encoding="utf-8"))["results"]
            if row["passed"]}


def _now() -> datetime:
    return datetime.now(UTC)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate", help="draw cases from the development database")
    gen.add_argument("--seed", type=int, required=True)
    gen.add_argument("--per-family", type=int, default=4)
    gen.add_argument("--all-programs", action="store_true",
                     help="include non-STEM programs")
    gen.add_argument("--out", required=True)
    gen.set_defaults(func=generate)
    live = sub.add_parser("run", help="run the cases through the benchmark, repeated")
    live.add_argument("--cases", required=True)
    live.add_argument("--repeat", type=int, default=3)
    live.add_argument("--out-dir", required=True)
    live.add_argument("--resume", action="store_true", help="skip repeats already diagnosed")
    live.set_defaults(func=run)
    rep = sub.add_parser("report", help="summarize finished repeats (no model calls)")
    rep.add_argument("--dir", required=True)
    rep.add_argument("--out")
    rep.set_defaults(func=report)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
