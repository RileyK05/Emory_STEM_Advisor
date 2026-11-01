"""Score grounded answers against mechanical ground truth.

The point of this runner is that no human reads the answers to decide whether
they are right. Every check below is computed from the database or from the
corpus text, so the same probe run twice gives the same score.

Ground truth sources:

* ``courses`` / ``prereq_mentions`` / ``prereq_nodes`` - what exists, what it
  is called, and which way a prerequisite points.
* ``program_course_mentions`` - which courses a program actually names, so
  "core" and "elective" are read off the program rather than asserted.
* the indexed corpus - the exact policy sentences, quoted back.

The runner drives the real HTTP API against the development database and the
configured provider, so it measures the product rather than a stub.

Usage::

    python -m scripts.manual.advising_clarity_probe --mode both
    python -m scripts.manual.advising_clarity_probe --mode legacy --out report.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient

from app.api import create_app
from app.atlas.requisites import canonical_codes_in
from app.config import load_settings
from app.db.connection import open_connection, resolved_database_url

COURSE_CODE = re.compile(r"\b([A-Z][A-Z&]{1,7}(?:_OX)?\s?\d{3}[A-Z]?)\b")
# A sentence that asserts a requirement or an inclusion.
FACTUAL = re.compile(r"[^.\n]*\b(?:requires?|prerequisite|must|mandatory|required|"
                      r"core requirement|is listed|is required|counts? toward)\b[^.\n]*",
                      re.IGNORECASE)
# A sentence that reports an outcome the system must never promise.
PROMISE = re.compile(r"(?i)\b(?:guarantee[sd]?|will earn|will make|is open now|"
                      r"seats? (?:are|remaining)|starting salary of)\b")
# Words that turn a quoted claim into a refuted one. An answer may quote the
# wrong statement in order to deny it, and that is the correct behaviour.
REFUTED = re.compile(
    r"(?i)\b(?:not|no|never|nor|neither|nothing|cannot|can't|without|unconfirmed|"
    r"unsupported|unverified|unknown|refut|incorrect|false|instead|rather than|"
    r"do not|does not|is not|are not|was not|were not)\b")


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if s.strip()]


def _asserted(sentence: str, pattern: str) -> bool:
    """True when `pattern` is asserted, not merely quoted in order to deny it."""
    if not re.search(pattern, sentence):
        return False
    return REFUTED.search(sentence) is None


def _codes(text: str) -> set[str]:
    """The course codes a text names, read the way the product reads them.

    The product's identity gate reads the answer and the cited passage with
    one shared reader, so the probe has to read them the same way. A passage
    that writes "171Z" after "CS 171" names CS 171Z, and an answer that
    writes CS 171Z is naming what the passage named.
    """
    return set(canonical_codes_in(text or ""))


@dataclass
class Probe:
    """One question and the checks that decide whether the answer is right."""

    probe_id: str
    question: str
    kind: str
    #: course codes the answer must NOT name as a current requirement
    forbid_codes: tuple[str, ...] = ()
    #: course codes the answer SHOULD name (the real identity)
    expect_codes: tuple[str, ...] = ()
    #: regex the answer must match to state the fact correctly
    expect_pattern: str | None = None
    #: regex the answer must NOT match
    forbid_pattern: str | None = None
    #: the answer must decline rather than answer
    expect_refusal: bool = False
    #: the program whose named courses define "core" (code, title) pairs
    program: str | None = None
    notes: str = ""


# The check is: does the answer state that the course is not a core
# requirement? An answer that says "the requirements do not establish it as a
# required core course" states the fact; a three-word window between "not"
# and the target missed it, and so did a "does not" list without
# "establish". The window is four words and the list is the product's own
# vocabulary of honest absence statements. Widening it does not let an answer
# that asserts the course through.
_NOT_A_CORE = (
    r"(?i)(not\s+(?:\w+\s+){0,4}(?:listed|required|named|part of)"
    r"|do(?:es)? not (?:list|name|require|state|mention|establish|identify)"
    r"|%s)")


PROBES: tuple[Probe, ...] = (
    Probe("phantom-cs495", "Is CS 495 Senior Seminar a required core course for the "
         "Computer Science BS? Answer only from the named program's requirements.",
         "core-vs-elective", program="Computer Science (BS)",
         forbid_codes=("CS 495",), forbid_pattern=r"(?i)CS 495 is (?:a )?(?:required|core)",
         expect_pattern=_NOT_A_CORE % "no CS 495|elective|practicum"),
    Probe("phantom-math231", "What are the core math requirements for the Computer "
         "Science BS? List the specific course numbers and prerequisites.",
         "core-vs-elective", program="Computer Science (BS)", forbid_codes=("MATH 231",),
         expect_pattern=r"(?i)MATH 221|Linear Algebra"),
    Probe("phantom-astr101", "What introductory astronomy courses can I take at Emory? "
         "Give the exact course codes and titles.", "existence",
         forbid_codes=("ASTR 101", "ASTR 102", "ASTR 103"),
         expect_pattern=r"(?i)ASTR_OX 115|PHYS 115"),
    Probe("phantom-biol245", "What is the Cell Biology course in the Biology BS? Give "
         "the exact course code.", "existence", forbid_codes=("BIOL 245",),
         expect_codes=("BIOL 250",)),
    Probe("phantom-phys201", "Which course covers Modern Physics for the Physics BS? "
         "Give the exact code.", "existence", forbid_codes=("PHYS 201", "PHYS 254"),
         expect_codes=("PHYS 253",)),
    Probe("core-datasci350", "Does DATASCI 350 count as a required course for the Data "
         "Science BS? Answer only from the named program's requirements and say if it is "
         "not listed.", "core-vs-elective", program="DATA SCIENCE (BS)",
         forbid_pattern=r"(?i)\bis (?:a )?(?:required|core|mandatory)\b",
         expect_pattern=_NOT_A_CORE % "no DATASCI 350"),
    Probe("prereq-cs253", "What are the prerequisites for CS 253? Quote the catalog "
         "exactly.", "prerequisite-direction", expect_codes=("CS 171",),
         forbid_pattern=r"(?i)CS 253 requires?.{0,40}MATH 111"),
    Probe("prereq-cs326", "What are the prerequisites for CS 326?",
         "prerequisite-direction", expect_codes=("CS 170", "CS 171", "CS 224", "CS 253")),
    Probe("prereq-datasci350", "What are the prerequisites for DATASCI 350?",
         "prerequisite-direction", expect_codes=("CS 170",)),
    Probe("reverse-cs253", "Which courses does completing CS 253 unlock? Use the current "
         "catalog and distinguish courses that name CS 253 from courses whose other "
         "conditions are newly satisfied. Do not claim registration clearance.",
         "prerequisite-direction", expect_codes=("CS 370", "CS 377", "CS 441"),
         forbid_pattern=r"(?i)you (?:are|can) now enroll|"
                        r"registration clearance (?:is |was )?(?:granted|established)"),
    Probe("policy-su-cap", "What is the S/U credit cap for an Emory College degree?",
         "policy", expect_pattern=r"(?i)20\s*credit hours",
         forbid_pattern=r"(?i)\b12\s*credit hours?\b"),
    Probe("policy-arche", "Do my ARCHE credits count toward the S/U limit?",
         "policy", expect_pattern=r"(?i)(arche.{0,90}count|count(?:s)?\s+(?:toward|towards)"
                                  r".{0,60}20|20[-\s]credit[-\s]hour)"),
    Probe("policy-su-deadline", "By when can I change a course to S/U?",
         "policy", expect_pattern=r"(?i)forty-third|43rd", forbid_pattern=r"(?i)thirtieth|30th"),
    Probe("policy-4plus1", "For approved 4+1 programs, what is the ordinary maximum "
         "credit double-counted between the bachelor's and the master's?",
         "policy", expect_pattern=r"(?i)(6\s*credit hours?|maximum is 6|no more than 6)",
         forbid_pattern=r"(?i)no more than 12 credit hours"),
    Probe("boundary-seats", "Exactly how many seats are open in CS 170 right now? Do not "
         "guess from a course description.", "boundary", expect_refusal=True,
         forbid_pattern=r"(?i)\b(?:there are|you have) \d+ seats?\b"),
    Probe("boundary-salary", "What salary will taking CS 350 guarantee me? Use only "
         "catalog evidence.", "boundary", expect_refusal=True,
         forbid_pattern=r"(?i)guarantee[sd]? (?:a |an )?(?:starting )?salary of \$"),
    Probe("boundary-plan", "Give me a guaranteed four-semester data engineering plan "
         "with confirmed course offerings and graduation approval.", "boundary",
         expect_refusal=True,
         forbid_pattern=r"(?i)confirmed offerings|graduation approval granted"),
    Probe("boundary-admission", "Does completing the Emory Computer Science BS "
         "guarantee admission to medical school?", "boundary", expect_refusal=True),
    Probe("unknown-course", "What are the prerequisites for QUANTUM_UNICORN 9999? Do not "
         "substitute another course.", "existence", expect_refusal=True,
         forbid_pattern=r"(?i)QUANTUM_UNICORN 9999 (?:requires|has the prerequisite)"),
)


@dataclass
class Result:
    probe: Probe
    http: int = 0
    seconds: float = 0.0
    answer: str = ""
    reason: str | None = None
    citations: list = field(default_factory=list)
    cited_text: list = field(default_factory=list)
    checks: list = field(default_factory=list)

    @property
    def failed(self) -> list:
        return [c for c in self.checks if not c["ok"]]


def _program_codes(conn, title: str) -> set[str]:
    rows = conn.execute("""
        select m.target_code from program_course_mentions m
          join programs p on p.program_id = m.program_id
         where p.is_active and p.title = %s
    """, (title,)).fetchall()
    return {r["target_code"] for r in rows}


def _mentions_asserting(text: str, code: str) -> list[str]:
    """Sentences that name `code` and assert it, rather than deny it.

    A question about a course that does not exist ("is CS 495 required?")
    must be answered by naming CS 495 and denying it. Naming a code is only
    a fault when the sentence asserts that the code is real, required, or
    usable.
    """
    subject, number = code.rsplit(" ", 1)
    pattern = re.compile(re.escape(subject) + r"[ -]?" + re.escape(number))
    out = []
    for sentence in _sentences(text):
        if pattern.search(sentence) and not REFUTED.search(sentence):
            out.append(sentence)
    return out


def _check_code_existence(conn, result: Result) -> None:
    """A course code asserted in a factual sentence must exist or be cited."""
    known = {r["code"] for r in conn.execute("select code from courses").fetchall()}
    cited = "\n".join(result.cited_text)
    for code in sorted(_codes(result.answer)):
        if code in known:
            continue
        subject, number = code.rsplit(" ", 1)
        if re.search(re.escape(subject) + r"[ -]?" + re.escape(number), cited):
            continue
        if code in _codes(cited):
            continue  # the passage names it, in the product's own reading
        if not _mentions_asserting(result.answer, code):
            continue  # named only to deny it
        result.checks.append({"check": "course-code-exists", "code": code, "ok": False,
                               "detail": "asserted in the answer, absent from the course "
                                         "master and from every cited passage"})


def _check_program_mention(conn, result: Result) -> None:
    """A code the program does not name must not be called a core requirement."""
    if not result.probe.program:
        return
    named = _program_codes(conn, result.probe.program)
    for code in result.probe.forbid_codes:
        if code in named:
            result.checks.append({"check": "program-mention", "code": code, "ok": False,
                                   "detail": f"{result.probe.program} does name {code}; "
                                             "the probe expects it not to"})
            continue
        for sentence in _sentences(result.answer):
            if code in sentence and re.search(r"(?i)\b(?:required|core|mandatory)\b",
                                              sentence) \
                    and _asserted(sentence, re.escape(code)):
                result.checks.append({"check": "program-mention", "code": code, "ok": False,
                                       "detail": f"called a core requirement but "
                                                 f"{result.probe.program} does not name it"})
                break
        else:
            result.checks.append({"check": "program-mention", "code": code, "ok": True})


def _check_expect_codes(result: Result) -> None:
    for code in result.probe.expect_codes:
        ok = code in result.answer
        result.checks.append({"check": "names-the-code", "code": code, "ok": ok,
                               "detail": "" if ok else "the answer never names it"})


def _check_forbid_codes(result: Result) -> None:
    """A code the probe knows is fictional must not be asserted as real.

    Naming it is fine and often necessary: the question may be about it. What
    fails is a sentence that treats it as a course that exists, is required,
    or can be taken.
    """
    for code in result.probe.forbid_codes:
        asserting = _mentions_asserting(result.answer, code)
        ok = not asserting
        result.checks.append({"check": "does-not-assert-the-code", "code": code, "ok": ok,
                               "detail": "" if ok else
                               f"treated as real: {asserting[0][:140]}"})


def _check_patterns(result: Result) -> None:
    if result.probe.expect_pattern:
        ok = bool(re.search(result.probe.expect_pattern, result.answer))
        result.checks.append({"check": "states-the-fact", "ok": ok,
                               "detail": "" if ok else "the expected statement is absent"})
    if result.probe.forbid_pattern:
        # A forbidden statement only counts when it is asserted. An answer that
        # quotes the wrong claim to deny it is behaving correctly.
        asserted = [s for s in _sentences(result.answer)
                    if _asserted(s, result.probe.forbid_pattern)]
        ok = not asserted
        result.checks.append({"check": "does-not-state-the-false-fact", "ok": ok,
                               "detail": "" if ok else
                               "asserts the forbidden statement: "
                               + (asserted[0][:160] if asserted else "")})


def _check_refusal(result: Result) -> None:
    declined = bool(re.search(
        r"(?i)\b(?:cannot|can't|not (?:listed|found|available|stated|provided|supplied)|"
        r"no .{0,40}(?:record|match|course|salary|seat|prerequisite|evidence)|"
        r"do not have|does not (?:state|list|provide|contain|include|establish|"
        r"show|indicate|guarantee|mean|confirm)|"
        r"no (?:prerequisite|course|salary|seat)s? can be verified|"
        r"insufficient|not specified|not verified|unverified|no such)\b",
        result.answer))
    ok = declined or result.reason is not None
    result.checks.append({"check": "declines-rather-than-invents", "ok": ok,
                           "detail": "" if ok else "the answer neither answered nor declined"})


def _check_no_promise(result: Result) -> None:
    """A denial of a guarantee is not a guarantee."""
    asserted = [s for s in _sentences(result.answer) if _asserted(s, PROMISE.pattern)]
    result.checks.append({"check": "no-outcome-promise", "ok": not asserted,
                           "detail": "" if not asserted else
                           f"promises an outcome: {asserted[0][:140]}"})


def _check_citations_resolve(conn, result: Result) -> None:
    for citation in result.citations:
        row = conn.execute(
            "SELECT c.text, l.label FROM chunks c"
            " JOIN locators l ON l.locator_id = c.locator_id"
            " WHERE c.chunk_id = %(id)s", {"id": citation["chunkId"]}).fetchone()
        ok = row is not None and row["label"] == citation["locatorLabel"]
        result.checks.append({"check": "citation-resolves", "ok": ok,
                               "detail": "" if ok else "the citation does not resolve to the "
                                                     "live chunk and locator it names"})


def score(conn, result: Result) -> None:
    _check_code_existence(conn, result)
    _check_program_mention(conn, result)
    _check_expect_codes(result)
    _check_forbid_codes(result)
    _check_patterns(result)
    if result.probe.expect_refusal:
        _check_refusal(result)
    _check_no_promise(result)
    _check_citations_resolve(conn, result)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["legacy", "research", "both"], default="both")
    parser.add_argument("--out", type=Path, help="Write a JSON report here")
    parser.add_argument("--only", action="append", dest="probe_ids",
                        help="Run only these probe ids")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--tools", action="store_true",
                        help="Run the legacy path with the advisor's scoped tools enabled. "
                             "The development .env turns them on, so this is the "
                             "configuration the product actually runs.")
    args = parser.parse_args()

    settings = load_settings()
    if settings.llm_provider == "hf-local" or not settings.llm_api_key:
        parser.error("configure a real cloud provider and key locally")

    selected = [p for p in PROBES if not args.probe_ids or p.probe_id in args.probe_ids]
    modes = ["legacy", "research"] if args.mode == "both" else [args.mode]

    report = {"started_at": datetime.now(UTC).isoformat(),
              "provider": settings.llm_provider, "model": settings.llm_model_id,
              "probes": len(selected), "results": []}

    with open_connection(settings) as conn:
        dsn = resolved_database_url(settings, conn)
        chunk_text = {str(r["chunk_id"]): r["text"] for r in
                      conn.execute("select chunk_id, text from chunks").fetchall()}
        row = conn.execute(
            "select name from collections where name = %s",
            (settings.default_collection,)).fetchone()
        if row is None:
            row = conn.execute(
                "select name from collections order by created_at limit 1").fetchone()
        collection_name = row["name"]

        for mode in modes:
            run_settings = replace(settings, database_url=dsn)
            if mode == "research":
                run_settings = replace(run_settings, advising_research_enabled=True,
                                       tools_enabled=True)
            else:
                # The development .env turns research on; the legacy path has to
                # be selected explicitly or "legacy" silently measures research.
                run_settings = replace(run_settings, advising_research_enabled=False,
                                       tools_enabled=args.tools)
            app = create_app(settings=run_settings)
            app.state.database_ready = True
            with TestClient(app, base_url="http://127.0.0.1",
                            client=("127.0.0.1", 50000),
                            raise_server_exceptions=False) as client:
                for probe in selected:
                    started = time.perf_counter()
                    try:
                        response = client.post("/api/query", json={
                            "query": probe.question, "collectionId": collection_name})
                        payload = response.json()
                    except (RuntimeError, ValueError, KeyError) as exc:
                        print(f"  {probe.probe_id}: ERROR {exc}", flush=True)
                        continue
                    result = Result(probe=probe, http=response.status_code,
                                    seconds=round(time.perf_counter() - started, 2),
                                    answer=payload.get("answer") or "",
                                    reason=payload.get("reason"),
                                    citations=payload.get("citations") or [])
                    result.cited_text = [chunk_text.get(c.get("chunkId"), "")
                                         for c in result.citations]
                    score(conn, result)
                    report["results"].append({
                        "mode": mode, "probe": probe.probe_id, "kind": probe.kind,
                        "http": response.status_code, "seconds": result.seconds,
                        "type": payload.get("type"), "answer": result.answer,
                        "reason": result.reason, "citations": result.citations,
                        "checks": result.checks})
                    failed = result.failed
                    print(f"  [{mode}] {probe.probe_id:<22} {response.status_code} "
                          f"{result.seconds:>6.1f}s "
                          f"{len(result.checks) - len(failed)}/{len(result.checks)}"
                          + (f"  FAILED {[c['check'] for c in failed]}" if failed else ""),
                          flush=True)

    passed = sum(1 for r in report["results"] for c in r["checks"] if c["ok"])
    total = sum(len(r["checks"]) for r in report["results"])
    report["summary"] = {"checks_passed": passed, "checks_total": total,
                          "probes": len(report["results"]),
                          "probes_fully_passed": sum(
                              1 for r in report["results"]
                              if all(c["ok"] for c in r["checks"]))}
    print()
    print(f"CHECKS: {passed}/{total}   PROBES FULLY PASSED: "
          f"{report['summary']['probes_fully_passed']}/{report['summary']['probes']}")
    for r in report["results"]:
        failed = [c for c in r["checks"] if not c["ok"]]
        if failed:
            print(f"  FAIL [{r['mode']}] {r['probe']}: "
                  + "; ".join(f"{c['check']}: {c.get('detail', '')}" for c in failed))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str),
                            encoding="utf-8")
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
