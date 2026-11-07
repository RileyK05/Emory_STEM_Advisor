"""Grade live answers about random courses and programs against the database.

The clarity probe asks 19 fixed questions, and fixed questions get tuned against.
This draws fresh courses and programs with a seed, words the questions the way a
student would, and takes every right answer from the database, so no one reads the
answers to score them and no one wrote the answers down. Change the seed and it is
a new test.

Ground truth: stored prerequisite trees and the product's own evaluator of them,
program mentions, term offering rows, course lifecycle and campus equivalents. Policy
facts in ``POLICY`` are hand-written, so each one also names the sentence that has to
be in the corpus. A fact the corpus no longer states is reported as stale, not failed.

From backend/, with the database running and a real provider configured:
    python -m scripts.manual.advising_sweep --per-family 12 --out report.json
"""

from __future__ import annotations

import argparse
import json
import random
import re
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from pathlib import Path

from fastapi.testclient import TestClient

from app.advising.catalog import _restore_tree, load_campus_equivalents
from app.advising.sequence import _satisfied_by_completed
from app.api import create_app
from app.atlas.requisites import AllOf, AnyOf, Condition, CourseRef, Req, canonical_codes_in
from app.config import load_settings
from app.db.connection import close_pools, open_connection, resolved_database_url
from scripts.manual.advising_clarity_probe import (
    PROMISE,
    REFUTED,
    Probe,
    Result,
    _check_citations_resolve,
    _check_code_existence,
    _sentences,
)

STEM = ("ANT", "ASTR", "BIOL", "CHEM", "CS", "DATASCI", "ECON", "ENVS", "MATH", "NBB", "PHYS",
        "PSYC", "QTM")
UNDERGRAD = r" [1-4][0-9][0-9]"
YES = re.compile(r"(?i)\b(?:yes|you can|you may|you're eligible|you are eligible|is offered|"
                 r"was offered|is listed|is scheduled|does appear|appears|shows|you meet|"
                 r"you've met|you have met|same course|equivalent|counts?)\b")
NO = re.compile(r"(?i)\b(?:no\b|not\b|isn't|aren't|doesn't|don't|cannot|can't|unable|missing|"
                r"still need|need to|must first|retired|no longer|discontinued)\b")
DECLINE = re.compile(r"(?i)\b(?:not (?:listed|found|in|a current|an active|available|offered|"
                     r"appear|show|exist)|no (?:current|such|record|course|longer|match)|"
                     r"there(?: is|'s) no|no [A-Z_]{2,8} \d{3}|"
                     r"doesn't (?:exist|appear)|does not (?:exist|appear|show)|cannot find|"
                     r"can't find|couldn't find|retired|discontinued|isn't (?:a|an|listed|in))\b")
NO_PREREQ = re.compile(r"(?i)\b(?:no (?:listed |stated |formal |specific |course |enrollment )*"
                       r"(?:prerequisites?|prereqs?|requirements?|requisites?)|"
                       r"(?:does not|doesn't) (?:have|list|state|show|require|name)|"
                       r"without (?:any )?prerequisites?|none (?:are )?listed|"
                       r"not list|no .{0,30}listed|nothing .{0,60}lists?)\b")


@dataclass(frozen=True)
class Fact:
    """A policy fact written by hand and tied to a sentence the corpus must hold."""

    fact_id: str
    question: str
    expect: str
    source: str


POLICY: tuple[Fact, ...] = (
    Fact("full-time", "whats the minimum credits to be full time", r"\b12\b",
         "must enroll in at least 12 hours for full time"),
    Fact("regular-load", "how many credits can i take in a normal semester", r"\b19\b",
         "must enroll in 12 to 19 hours"),
    Fact("overload-gpa", "what gpa do i need to overload", r"3\.5",
         "cumulative GPA of 3.5 or higher may overload"),
    Fact("overload-cap", "what's the most credits i can overload to", r"\b22\b",
         "may overload up to 22 credit hours"),
    Fact("overload-incomplete", "can i overload if i have an incomplete from last semester",
         r"(?i)\b(?:no|not|cannot|can't)\b",
         "outstanding incomplete grades at the start of a semester may not overload"),
    Fact("maymester", "how many maymester classes can i take", r"(?i)\b(?:one|1|single)\b",
         "may enroll in only one three-week Maymester course"),
    Fact("ap-cap", "how many ap credits count toward graduation", r"\b12\b",
         "up to 12 credit hours towards hours required for graduation"),
    Fact("ap-each", "how many credits do i get for each ap test", r"(?i)\b(?:3|three)\b",
         "3 credit hours for each accepted test"),
    Fact("ap-score", "will emory accept a 3 on an ap exam",
         r"(?i)\b(?:no|not|below 4|at least (?:a )?4)\b", "AP scores below 4"),
    Fact("ap-ger", "can my ap scores fulfill my gers", r"(?i)\b(?:no|not|cannot|can't)\b",
         "may not fulfill general education requirements"),
    Fact("transfer-cap", "how many credits can i transfer in", r"\b62\b",
         "may transfer a maximum of 62 credit hours"),
    Fact("declare-by", "when do i have to declare my major", r"(?i)\bfourth\b|\b4th\b",
         "must declare a major by the end of their fourth regular"),
    Fact("declare-early", "can i declare a major during my first semester",
         r"(?i)\b(?:no|not|cannot|can't|after (?:your|their) first semester)\b",
         "may not declare a major or minor until after their first semester"),
    Fact("declare-transfer", "im a transfer student, when do i need to declare a major",
         r"(?i)\bsecond\b|\b2nd\b",
         "Transfer students must declare a major by the end of their second regular"),
    Fact("withdraw-class", "what's the last day to withdraw from one class",
         r"(?i)43rd|forty-third", "until the 43rd instructional day"),
    Fact("withdraw-semester", "if i withdraw from the whole semester how long do i have to "
         "still get Ws", r"(?i)twenty-third|23rd|\b23\b",
         "before the end of the twenty-third calendar day"),
    Fact("summa", "what percent of the class gets summa cum laude",
         r"\b5(?:\.00)?\s?%|5 percent",
         "Summa cum laude: top 5.00%"),
    Fact("latin-total", "what share of graduates get latin honors",
         r"\b30(?:\.00)?\s?%|30 percent",
         "who finish in the top 30% among Emory College graduates"),
)


@dataclass
class Case:
    family: str
    case_id: str
    question: str
    tree: Req | None = None
    expected: tuple[str, ...] = ()
    minimum: int = 0
    pattern: str | None = None
    forbid: str | None = None
    verdict: str | None = None
    notes: dict = field(default_factory=dict)


def _title(title: str) -> str:
    return re.sub(r"\s*\(.*$", "", title).strip()


def _named(code: str, twins: dict[str, frozenset[str]], codes: set[str]) -> bool:
    return code in codes or bool(twins.get(code, frozenset()) & codes)


def covers(tree: Req, codes: set[str], twins: dict[str, frozenset[str]]) -> bool:
    """Does an answer name enough of a tree that a student could act on it?"""
    if isinstance(tree, CourseRef):
        return _named(tree.code, twins, codes)
    if isinstance(tree, Condition):
        return True
    if isinstance(tree, AllOf):
        return all(covers(item, codes, twins) for item in tree.items)
    if isinstance(tree, AnyOf):
        return any(covers(item, codes, twins) for item in tree.items)
    raise TypeError(type(tree).__name__)


def one_way_through(tree: Req) -> list[str] | None:
    """The smallest course list that meets a tree, or None if it has a condition."""
    if isinstance(tree, CourseRef):
        return [tree.code]
    if isinstance(tree, Condition):
        return None
    if isinstance(tree, AnyOf):
        routes = [route for item in tree.items if (route := one_way_through(item))]
        return min(routes, key=len) if routes else None
    routes = [one_way_through(item) for item in tree.items]
    return None if None in routes else [code for route in routes for code in route]


def _pick(rng: random.Random, rows: list, count: int, key=lambda row: row["code"]) -> list:
    """Half from the STEM subjects this product is for, half from the rest."""
    stem = [row for row in rows if key(row).split(" ")[0] in STEM]
    rest = [row for row in rows if key(row).split(" ")[0] not in STEM]
    rng.shuffle(stem)
    rng.shuffle(rest)
    take = stem[:count - count // 2] + rest[:count // 2]
    return take + stem[len(take):count] if len(take) < count else take


def _trees(conn, collection_id) -> dict[str, Req]:
    rows = conn.execute("""
        select c.code, n.node_id, n.kind, n.parent_node_id, n.position, n.node_type,
               n.target_code, n.condition_text
          from prereq_nodes n join courses c using (course_id)
         where c.collection_id = %s and c.lifecycle = 'current' and n.kind = 'prerequisite'
           and c.parse_status = 'parsed'
    """, (collection_id,)).fetchall()
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["code"]].append(row)
    return {code: _restore_tree(nodes) for code, nodes in grouped.items()}


def build_cases(conn, collection_id, rng: random.Random, per: int) -> list[Case]:
    courses = conn.execute(f"""
        select code, title, parse_status, requisites_raw, lifecycle from courses
         where collection_id = %s and code !~ '_OX' and code ~ '{UNDERGRAD}'
           and code !~ ' 999'
    """, (collection_id,)).fetchall()
    current = [row for row in courses if row["lifecycle"] == "current"]
    trees = _trees(conn, collection_id)
    titles = Counter(row["title"].lower() for row in current)
    cases: list[Case] = []

    wordings = ("what are the prereqs for {code}", "what do i need before i can take {low}",
                "do i need anything before {squeezed}", "requirements to take {code}?")
    with_tree = [row for row in current if row["code"] in trees]
    for row in _pick(rng, with_tree, per):
        code = row["code"]
        cases.append(Case("prereq", f"prereq-{code}", rng.choice(wordings).format(
            code=code, low=code.lower(), squeezed=code.replace(" ", "").lower()),
            tree=trees[code]))

    open_courses = [row for row in current if row["parse_status"] == "none"
                    and not (row["requisites_raw"] or "").strip()]
    for row in _pick(rng, open_courses, per // 2):
        cases.append(Case("no-prereq", f"none-{row['code']}",
                          f"does {row['code']} have any prerequisites", pattern=NO_PREREQ.pattern))

    unique = [row for row in current if titles[row["title"].lower()] == 1
              and len(row["title"]) > 12
              and not re.search(r"(?i)special topics|seminar|directed|research|honors|[:./&]",
                                row["title"])]
    for row in _pick(rng, unique, per // 2):
        cases.append(Case("title-to-code", f"title-{row['code']}",
                          f"what's the course code for {row['title'].lower()}",
                          expected=(row["code"],), minimum=1))
    for row in _pick(rng, unique, per // 2):
        words = [word for word in re.findall(r"[A-Za-z]{5,}", row["title"])][:4]
        if words:
            cases.append(Case("code-to-title", f"name-{row['code']}", f"what is {row['code']}",
                              pattern="(?i)" + "|".join(map(re.escape, words))))

    programs = conn.execute("""
        select p.program_id, p.title, p.kind,
               array_agg(distinct m.target_code) filter (where m.target_course_id is not null)
                   as codes,
               array_agg(distinct m.target_code) as named
          from programs p join program_course_mentions m using (program_id)
         where p.collection_id = %s and p.is_active
         group by 1, 2, 3 having count(distinct m.target_course_id) >= 5
    """, (collection_id,)).fetchall()
    by_subject: dict[tuple[str, str], list] = {}
    for row in programs:
        by_subject.setdefault((_title(row["title"]).lower(), row["kind"]), []).append(row)
    for (clean, kind), rows in sorted(by_subject.items(), key=lambda item: item[0]):
        if len(rows) > 1:  # Every subject taught under two degrees, not a sample.
            cases.append(Case("two-degrees", f"degrees-{clean}",
                              f"what classes do i need for the {clean} {kind}",
                              notes={"named": set().union(*(set(r["named"]) for r in rows)),
                                     "degrees": True}))
    rng.shuffle(programs)
    for row in programs[:per]:
        clean = _title(row["title"]).lower()
        cases.append(Case("program", f"program-{row['title']}", rng.choice(
            (f"what classes do i need for the {clean} {row['kind']}",
             f"requirements for {clean} {row['kind']}",
             f"what does a {clean} {row['kind']} take")),
            expected=tuple(row["codes"]), minimum=3, notes={"named": set().union(
                *(set(r["named"]) for r in by_subject[(clean, row["kind"])]))}))

    required = conn.execute("""
        select m.target_code as code, array_agg(distinct p.title) as titles
          from program_course_mentions m join programs p using (program_id)
         where p.collection_id = %s and p.is_active and p.kind = 'major'
           and m.target_code !~ '_OX' and m.target_course_id is not null
         group by 1 having count(distinct p.program_id) between 2 and 8
    """, (collection_id,)).fetchall()
    for row in _pick(rng, required, per // 2):
        cases.append(Case("which-majors", f"majors-{row['code']}",
                          f"which majors use {row['code']}",
                          pattern="(?i)" + "|".join(re.escape(_title(t)) for t in row["titles"])))

    dependents = conn.execute(f"""
        select t.code, array_agg(distinct c.code) as unlocks
          from prereq_mentions m
          join courses c on c.course_id = m.course_id and c.lifecycle = 'current'
          join courses t on t.course_id = m.target_course_id and t.lifecycle = 'current'
         where c.collection_id = %s and m.kind = 'prerequisite' and t.code ~ '{UNDERGRAD}'
           and t.code !~ '_OX' and c.code !~ '_OX'
         group by 1 having count(distinct c.code) between 2 and 12
    """, (collection_id,)).fetchall()
    for row in _pick(rng, dependents, per // 2):
        cases.append(Case("unlock", f"unlock-{row['code']}", rng.choice(
            (f"what can i take after {row['code']}",
             f"what classes open up once i finish {row['code']}")),
            expected=tuple(row["unlocks"]), minimum=max(1, len(row["unlocks"]) // 2)))

    offered = conn.execute("""
        select s.term, array_agg(distinct r.raw_course_code) as codes
          from atlas_offering_rows r
          join atlas_offering_snapshots s using (source_id, capture_set_hash)
          join sources so on so.source_id = r.source_id and so.is_current
         group by 1
    """).fetchall()
    by_term = {row["term"]: set(row["codes"]) for row in offered}
    known = {row["code"] for row in current}
    for term in ("Spring 2027", "Fall 2026"):
        if term not in by_term:
            continue
        have = [{"code": code} for code in sorted(by_term[term] & known)
                if re.search(UNDERGRAD, code)]
        for row in _pick(rng, have, per // 4):
            cases.append(Case("offered", f"offered-{row['code']}-{term}",
                              f"is {row['code']} offered in {term.lower()}", verdict="yes"))
        others = set().union(*(codes for name, codes in by_term.items() if name != term))
        missing = [{"code": code} for code in sorted((others & known) - by_term[term])
                   if re.search(UNDERGRAD, code) and "_OX" not in code]
        for row in _pick(rng, missing, per // 4):
            cases.append(Case("offered", f"unoffered-{row['code']}-{term}",
                              f"is {row['code']} offered in {term.lower()}", verdict="no"))

    subjects = sorted({row["code"].split(" ")[0] for row in current} & set(STEM))
    every = {row["code"] for row in courses}
    stems = {re.sub(r"[A-Z]+$", "", code) for code in every}
    for subject in rng.sample(subjects, min(per // 2, len(subjects))):
        number = next(n for n in rng.sample(range(100, 500), 400)
                      if f"{subject} {n}" not in stems)
        fake = f"{subject} {number}"
        cases.append(Case("phantom", f"phantom-{fake}", rng.choice(
            (f"what are the prereqs for {fake}", f"is {fake} a good class to take",
             f"how many credits is {fake}")),
            pattern=DECLINE.pattern, notes={"phantom": fake}))

    retired = [row for row in courses if row["lifecycle"] == "retired"
               and row["code"] not in known]
    for row in _pick(rng, retired, per // 2):
        cases.append(Case("retired", f"retired-{row['code']}",
                          f"can i take {row['code']} next semester", pattern=DECLINE.pattern))

    # Student context: what they took decides the answer, and the product's own
    # evaluator says what the right answer is.
    routes = [(row, one_way_through(trees[row["code"]])) for row in with_tree]
    routes = [(row, route) for row, route in routes
              if route and len(set(route)) <= 4 and all(code in known for code in route)]
    rng.shuffle(routes)
    for row, route in routes[:per // 2]:
        taken = sorted(set(route))
        assert _satisfied_by_completed(trees[row["code"]], set(taken))
        cases.append(Case("eligible", f"eligible-{row['code']}",
                          f"i've taken {', '.join(taken)}. can i take {row['code']}?",
                          verdict="yes"))
    # A course can only be refused usefully by saying what is still missing, so the
    # answer has to name the whole requirement and must not open with a yes.
    blocked = [(row, sorted(set(route))) for row, route in routes if len(set(route)) >= 2]
    for row, taken in blocked[per // 2:per]:
        partial = taken[1:]
        if _satisfied_by_completed(trees[row["code"]], set(partial)):
            continue
        cases.append(Case("eligible", f"blocked-{row['code']}",
                          f"i've taken {', '.join(partial)}. can i take {row['code']}?",
                          tree=trees[row["code"]], verdict="blocked"))

    for row in programs[per:per + per // 2]:
        if row["kind"] != "major":
            continue
        clean = _title(row["title"]).lower()
        done = sorted(rng.sample(sorted(row["codes"]), 3))
        left = tuple(sorted(set(row["codes"]) - set(done)))
        cases.append(Case("audit", f"audit-{row['title']}",
                          f"i've done {', '.join(done)}. what's left for the {clean} major?",
                          expected=left, minimum=1,
                          forbid=r"(?i)\byou (?:have|'ve) (?:satisfied|completed|fulfilled) the "
                                 r"(?:major|program|degree)\b"))

    twins = conn.execute("""
        select oxford_code, atlanta_code from course_campus_equivalents
         where collection_id = %s order by oxford_code
    """, (collection_id,)).fetchall()
    for row in rng.sample(list(twins), min(per // 3, len(twins))):
        cases.append(Case("campus", f"campus-{row['oxford_code']}",
                          f"i took {row['oxford_code']} at oxford, is that the same as "
                          f"{row['atlanta_code']}?", verdict="yes"))

    corpus = " ".join(conn.execute(
        "select string_agg(c.text, ' ') as text from chunks c join sources s using (source_id) "
        "where s.is_current and s.filename ~ '^emory-(policy|college-dates|course-reg)'"
    ).fetchone()["text"].split())
    for fact in POLICY:
        if fact.source not in corpus:
            print(f"STALE policy fact: {fact.fact_id}: the corpus no longer states it")
            continue
        cases.append(Case("policy", fact.fact_id, fact.question, pattern=fact.expect))
    return cases


def grade(conn, case: Case, result: Result, twins: dict[str, frozenset[str]]) -> None:
    def check(name: str, ok: bool, detail: str = "") -> None:
        result.checks.append({"check": name, "ok": ok, "detail": "" if ok else detail})

    answer = result.answer
    codes = set(canonical_codes_in(answer))
    check("answered", result.http == 200 and bool(answer.strip()),
          f"http {result.http}, reason {result.reason}")
    check("cited", bool(result.citations) or case.family in {"phantom", "retired"},
          "an answer with no citation")
    _check_code_existence(conn, result)
    # "Where did you see ANT 139?" names the made-up code without asserting it.
    result.checks = [c for c in result.checks if c.get("code") != case.notes.get("phantom")]
    promises = [sentence for sentence in _sentences(answer) if PROMISE.search(sentence)
                and not REFUTED.search(sentence) and not re.search(r"(?i)\bif\b", sentence)]
    check("no-outcome-promise", not promises, promises[0][:140] if promises else "")
    _check_citations_resolve(conn, result)
    if case.tree is not None:
        check("covers-the-tree", covers(case.tree, codes, twins),
              f"tree {case.tree} not covered by {sorted(codes)}")
    if case.expected and case.minimum:
        hits = sum(_named(code, twins, codes) for code in case.expected)
        check("names-expected", hits >= case.minimum,
              f"{hits} of {len(case.expected)}, wanted {case.minimum}")
    if "named" in case.notes and codes:
        named = case.notes["named"]
        share = sum(code in named or _named(code, twins, named) for code in codes) / len(codes)
        # Half, not all: a good answer may add examples or point to a related program.
        check("stays-on-the-program", share >= 0.5,
              f"{share:.0%} of {sorted(codes)[:12]} are in the program")
    if case.notes.get("degrees"):
        check("names-both-degrees", bool(re.search(r"\bB\.?A\b", answer)
                                         and re.search(r"\bB\.?S\b", answer)),
              "the student named no degree and the answer gave one")
    if case.pattern:
        check("states-the-fact", bool(re.search(case.pattern, answer)),
              "expected statement absent")
    if case.forbid:
        check("avoids-the-claim", not re.search(case.forbid, answer), "made the forbidden claim")
    if case.verdict:
        head = " ".join(re.split(r"(?<=[.!?])\s+", answer)[:2])
        ok = {"yes": YES.search(head), "no": NO.search(head),
              "blocked": not re.match(r"(?i)\W*yes\b", head)}[case.verdict]
        check(f"says-{case.verdict}", bool(ok), f"opening: {head[:160]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-family", type=int, default=12)
    parser.add_argument("--seed", type=int, default=20261106)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--only", action="append", help="Run only these families")
    parser.add_argument("--list", action="store_true", help="Print the questions and stop")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    settings = load_settings()
    report = {"seed": args.seed, "results": []}

    with open_connection(settings) as conn:
        dsn = resolved_database_url(settings, conn)
        collection = conn.execute("select collection_id, name from collections where name = %s",
                                  (settings.default_collection,)).fetchone()
        cases = build_cases(conn, collection["collection_id"], random.Random(args.seed),
                            args.per_family)
        cases = [case for case in cases if not args.only or case.family in args.only]
        twins = load_campus_equivalents(conn, collection_id=collection["collection_id"])
        print(Counter(case.family for case in cases))
        if args.list:
            for case in cases:
                print(f"  {case.family:<14} {case.question}")
            return

        app = create_app(settings=replace(settings, database_url=dsn, tools_enabled=True,
                                          advising_research_enabled=True))
        app.state.database_ready = True

        def ask(case: Case) -> tuple[Case, Result, dict]:
            started = time.perf_counter()
            with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 52000),
                            raise_server_exceptions=False) as client:
                response = client.post("/api/query", json={"query": case.question,
                                                           "collectionId": collection["name"]})
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            result = Result(probe=Probe(case.case_id, case.question, case.family),
                            http=response.status_code, answer=payload.get("answer") or "",
                            seconds=round(time.perf_counter() - started, 1),
                            reason=payload.get("reason"),
                            citations=payload.get("citations") or [])
            return case, result, payload

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for future in as_completed([pool.submit(ask, case) for case in cases]):
                case, result, payload = future.result()
                rows = conn.execute("select text from chunks where chunk_id = any(%s::uuid[])",
                                    ([c["chunkId"] for c in result.citations],)).fetchall()
                result.cited_text = [row["text"] for row in rows]
                grade(conn, case, result, twins)
                conn.commit()
                failed = result.failed
                print(f"  {case.family:<14} {result.http} {result.seconds:>6.1f}s "
                      f"{len(result.checks) - len(failed)}/{len(result.checks)} "
                      f"{case.case_id}"
                      + (f"  FAILED {[c['check'] for c in failed]}" if failed else ""), flush=True)
                report["results"].append({
                    "family": case.family, "id": case.case_id, "question": case.question,
                    "http": result.http, "seconds": result.seconds, "answer": result.answer,
                    "reason": result.reason, "type": payload.get("type"),
                    "citations": len(result.citations), "checks": result.checks})

    close_pools()
    families = defaultdict(lambda: [0, 0, []])
    for row in report["results"]:
        family = families[row["family"]]
        family[1] += 1
        family[0] += all(c["ok"] for c in row["checks"])
        family[2].append(row["seconds"])
    print()
    for name, (passed, total, seconds) in sorted(families.items()):
        print(f"{name:<14} {passed}/{total}  median {sorted(seconds)[len(seconds) // 2]:.0f}s")
    print(f"CASES FULLY PASSED: {sum(f[0] for f in families.values())}/"
          f"{sum(f[1] for f in families.values())}")
    print(Counter(c["check"] for row in report["results"] for c in row["checks"] if not c["ok"]))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str),
                            encoding="utf-8")


if __name__ == "__main__":
    main()
