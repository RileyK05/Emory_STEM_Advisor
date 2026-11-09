"""The artifact benchmark runner and mechanical checks (K1 to K11).

Drives the real API and worker against the dev collection with the configured
model, evaluates 70 cases (30 artifact cases + 40 scope cases), and verifies
mechanical invariants K1 through K11.

Usage (from backend/):
    python -m scripts.manual.artifact_benchmark --cases scripts/manual/artifact_benchmark_cases.jsonl --out report.json
    python -m scripts.manual.artifact_benchmark --offline
    python -m scripts.manual.artifact_benchmark --case A01
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import docx
import openpyxl
from fastapi.testclient import TestClient

from app.advising.catalog import _restore_tree, load_campus_equivalents
from app.api import create_app
from app.artifacts.docx import export_docx
from app.artifacts.inert_html import render_inert_markup
from app.artifacts.models import DocumentSpec, HtmlArtifactSpec, WorkbookSpec
from app.artifacts.storage import ArtifactStore, validate_spec
from app.artifacts.xlsx import _FORMULA_BLOCKLIST, export_xlsx
from app.atlas.requisites import AllOf, CourseRef, canonical_codes_in
from app.config import configure_runtime_database, load_settings
from app.db import queries
from app.db.connection import close_pools, connector, open_connection, resolved_database_url
from app.generation.artifacts import build_worker_handlers
from app.generation.research_final import SCOPE_DECLINE_TEXT
from app.jobs.dispatch import KindDispatchingExecutor
from app.jobs.runtime import RunExecutor
from app.jobs.store import JobStore
from app.jobs.worker import run_once

logger = logging.getLogger(__name__)

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


@dataclass
class CheckResult:
    check: str
    ok: bool
    detail: str = ""


@dataclass
class BenchmarkResult:
    case_id: str
    category: str
    prompt: str
    turn_seconds: float = 0.0
    run_seconds: float = 0.0
    turn_response: dict[str, Any] = field(default_factory=dict)
    artifact_spec: dict[str, Any] | None = None
    artifact_record: dict[str, Any] | None = None
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.ok for c in self.checks)


def extract_artifact_text(spec: dict[str, Any]) -> str:
    """Extract all student-visible text from an artifact specification."""
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
    elif kind == "workbook":
        for sheet in spec.get("sheets", []):
            texts.append(sheet.get("name", ""))
            for cell in sheet.get("cells", []):
                if cell.get("value") is not None:
                    texts.append(str(cell["value"]))
    elif kind == "html":
        texts.append(spec.get("html", ""))
    return "\n".join(texts)


def extract_planned_terms(spec: dict[str, Any]) -> dict[str, int]:
    """Map scheduled course codes to a 1-based term/semester index from a plan."""
    mapping: dict[str, int] = {}
    kind = spec.get("kind")
    current_term = 1
    if kind == "document":
        for block in spec.get("blocks", []):
            text = block.get("text", "")
            for pattern in TERM_PATTERNS:
                m = pattern.search(text)
                if m:
                    try:
                        current_term = int(m.group(1))
                    except ValueError:
                        current_term += 1
                    break
            codes = canonical_codes_in(text)
            for code in codes:
                mapping.setdefault(code, current_term)
            if block.get("type") == "list":
                for item in block.get("items", []):
                    for code in canonical_codes_in(item):
                        mapping.setdefault(code, current_term)
            elif block.get("type") == "table":
                headers = block.get("headers", [])
                col_terms = {}
                for idx, h in enumerate(headers):
                    for pattern in TERM_PATTERNS:
                        m = pattern.search(h)
                        if m:
                            try:
                                col_terms[idx] = int(m.group(1))
                            except ValueError:
                                col_terms[idx] = idx + 1
                            break
                for row in block.get("rows", []):
                    for col_idx, cell_text in enumerate(row):
                        term_idx = col_terms.get(col_idx, current_term)
                        for code in canonical_codes_in(cell_text):
                            mapping.setdefault(code, term_idx)
    elif kind == "workbook":
        for s_idx, sheet in enumerate(spec.get("sheets", []), start=1):
            s_name = sheet.get("name", "")
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
                for code in canonical_codes_in(val):
                    mapping.setdefault(code, s_term)
    return mapping


def evaluate_checks(
    conn,
    case: dict[str, Any],
    turn: dict[str, Any],
    artifact_record: dict[str, Any] | None,
    turn_seconds: float,
    run_seconds: float,
    queue_empty: bool,
    known_courses: set[str],
    campus_equivalents: dict[str, frozenset[str]],
    collection_id: UUID,
    parent_sha256_before: str | None = None,
) -> list[CheckResult]:
    """Run mechanical checks K1 through K11 on a completed case."""
    results: list[CheckResult] = []
    expected = case.get("expected", {})
    spec = artifact_record.get("spec") if artifact_record else None
    artifact_run_info = turn.get("artifactRun")

    # K1: Terminal within 90s (turn) and 150s (run), queue empty
    turn_ok = turn_seconds <= 90.0
    run_ok = run_seconds <= 150.0
    k1_ok = turn_ok and run_ok and queue_empty
    k1_detail = f"turn: {turn_seconds:.1f}s (<=90), run: {run_seconds:.1f}s (<=150), queue_empty: {queue_empty}"
    results.append(CheckResult("K1", k1_ok, k1_detail))

    # K2: Intent matches expected artifact kind and purpose
    expect_artifact = expected.get("artifact", False)
    if expect_artifact:
        exp_kind = expected.get("kind")
        exp_purpose = expected.get("purpose") or "create"
        k2_ok = (
            artifact_run_info is not None
            and artifact_run_info.get("kind") == exp_kind
            and artifact_run_info.get("purpose") == exp_purpose
        )
        k2_detail = (
            f"expected {exp_kind}/{exp_purpose}, "
            f"got {artifact_run_info.get('kind') if artifact_run_info else None}/"
            f"{artifact_run_info.get('purpose') if artifact_run_info else None}"
        )
    else:
        if expected.get("conflict"):
            k2_ok = bool(turn.get("artifactNotice"))
            k2_detail = "conflict notice present" if k2_ok else "missing artifactNotice on conflict"
        else:
            k2_ok = artifact_run_info is None
            k2_detail = "no artifact run admitted" if k2_ok else "unexpected artifactRun admitted"
    results.append(CheckResult("K2", k2_ok, k2_detail))

    # K3: Publishes: every expected run ends published
    if expect_artifact:
        k3_ok = artifact_record is not None and spec is not None
        k3_detail = "artifact published" if k3_ok else "artifact run failed or not published"
    else:
        k3_ok = True
        k3_detail = "N/A (no artifact expected)"
    results.append(CheckResult("K3", k3_ok, k3_detail))

    # K4: Real courses: courses in artifact exist in collection or stated campus equivalents
    if spec is not None:
        art_text = extract_artifact_text(spec)
        codes = set(canonical_codes_in(art_text))
        invalid_codes = set()
        for code in codes:
            if code not in known_courses:
                twins = campus_equivalents.get(code, frozenset())
                if not (twins & known_courses):
                    invalid_codes.add(code)
        if expected.get("no_hallucinated_course"):
            bad = expected["no_hallucinated_course"]
            if bad in codes or bad in canonical_codes_in(turn.get("text") or ""):
                invalid_codes.add(bad)
        k4_ok = len(invalid_codes) == 0
        k4_detail = "all courses valid" if k4_ok else f"invalid courses: {sorted(invalid_codes)}"
    else:
        if expected.get("no_hallucinated_course"):
            bad = expected["no_hallucinated_course"]
            turn_codes = set(canonical_codes_in(turn.get("text") or ""))
            k4_ok = bad not in turn_codes
            k4_detail = f"{bad} absent as expected" if k4_ok else f"{bad} named in turn text"
        else:
            k4_ok = True
            k4_detail = "N/A"
    results.append(CheckResult("K4", k4_ok, k4_detail))

    # K5: Program truth: courses in plan/checklist appear in program requirements
    prog_title = expected.get("program")
    if spec is not None and prog_title and (expected.get("plan") or expected.get("checklist")):
        prog_row = conn.execute(
            """SELECT program_id FROM programs
               WHERE collection_id = %s AND (title ILIKE %s OR title ILIKE %s)
               ORDER BY length(title) ASC LIMIT 1""",
            (collection_id, prog_title, f"%{prog_title}%"),
        ).fetchone()
        if prog_row:
            items = conn.execute(queries.get("list_program_items"), {"program_id": prog_row["program_id"]}).fetchall()
            prog_allowed_codes: set[str] = set()
            for itm in items:
                itm_codes = canonical_codes_in(itm["text"])
                for ic in itm_codes:
                    prog_allowed_codes.add(ic)
                    prog_allowed_codes.update(campus_equivalents.get(ic, frozenset()))
            art_codes = set(canonical_codes_in(extract_artifact_text(spec)))
            unsupported = art_codes - prog_allowed_codes
            # Allow common general education/electives or completed courses
            completed = set(expected.get("completed_courses", []))
            unsupported -= completed
            k5_ok = len(unsupported) == 0 or len(unsupported) <= len(art_codes) * 0.4
            k5_detail = f"{len(art_codes) - len(unsupported)}/{len(art_codes)} courses in program requirements"
        else:
            k5_ok = True
            k5_detail = f"program '{prog_title}' not in dev database"
    else:
        k5_ok = True
        k5_detail = "N/A"
    results.append(CheckResult("K5", k5_ok, k5_detail))

    # K6: Order: in plans, no course sits in or before a term holding its required prerequisite
    if spec is not None and expected.get("plan"):
        plan_terms = extract_planned_terms(spec)
        completed_set = set(expected.get("completed_courses", []))
        for c in list(completed_set):
            completed_set.update(campus_equivalents.get(c, frozenset()))
        order_violations = []
        for code, term_idx in plan_terms.items():
            node_rows = conn.execute(
                """SELECT n.node_id, n.kind, n.parent_node_id, n.position, n.node_type,
                          n.target_code, n.condition_text
                   FROM prereq_nodes n JOIN courses co ON co.course_id = n.course_id
                   WHERE co.collection_id = %s AND co.code = %s AND co.lifecycle = 'current'
                     AND co.parse_status = 'parsed' AND n.kind = 'prerequisite'""",
                (collection_id, code),
            ).fetchall()
            if not node_rows:
                continue
            try:
                tree = _restore_tree([dict(r) for r in node_rows])
            except ValueError:
                continue
            # A required prerequisite is a course reachable from the root through
            # all-of groups only; an any-of alternative is not required on its own.
            required: set[str] = set()
            stack = [tree]
            while stack:
                node = stack.pop()
                if isinstance(node, CourseRef):
                    required.add(node.code)
                elif isinstance(node, AllOf):
                    stack.extend(node.items)
            earlier_courses = {c for c, t in plan_terms.items() if t < term_idx} | completed_set
            later_or_same = {c for c, t in plan_terms.items() if t >= term_idx and c != code}
            for prereq in required:
                names = {prereq} | set(campus_equivalents.get(prereq, frozenset()))
                if names & later_or_same and not names & earlier_courses:
                    order_violations.append(f"{code} (term {term_idx}) before/with {prereq}")
        k6_ok = len(order_violations) == 0
        k6_detail = "order verified" if k6_ok else f"violations: {order_violations[:3]}"
    else:
        k6_ok = True
        k6_detail = "N/A"
    results.append(CheckResult("K6", k6_ok, k6_detail))

    # K7: Evidence: non-empty and resolves to cited passages
    if spec is not None:
        art_evidence = spec.get("evidence", {})
        turn_citations = {c.get("chunkId") for c in turn.get("citations", [])}
        all_refs: list[str] = []
        for ref_list in art_evidence.values():
            for ref in ref_list:
                if isinstance(ref, dict):
                    all_refs.append(str(ref.get("chunk_id") or ref.get("chunkId") or ""))
        if not art_evidence:
            k7_ok = False
            k7_detail = "artifact evidence is empty"
        elif turn_citations and not any(r in turn_citations for r in all_refs if r):
            k7_ok = False
            k7_detail = "artifact evidence chunk IDs do not resolve to turn citations"
        else:
            k7_ok = True
            k7_detail = f"{len(all_refs)} evidence references resolve"
    else:
        k7_ok = True
        k7_detail = "N/A"
    results.append(CheckResult("K7", k7_ok, k7_detail))

    # K8: Export: docx/xlsx reopen, html inert sanitizer, formulas safe
    if spec is not None:
        kind = spec.get("kind")
        k8_ok = True
        k8_detail = ""
        try:
            typed_spec = validate_spec(kind, spec)
            if isinstance(typed_spec, DocumentSpec):
                docx_bytes = export_docx(typed_spec)
                doc = docx.Document(io.BytesIO(docx_bytes))
                k8_detail = f"docx valid ({len(doc.paragraphs)} paragraphs)"
            elif isinstance(typed_spec, WorkbookSpec):
                xlsx_bytes = export_xlsx(typed_spec)
                wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), data_only=False)
                blocked_found = []
                for sheet in wb.worksheets:
                    for row in sheet.iter_rows(values_only=False):
                        for c in row:
                            if (
                                c.value
                                and isinstance(c.value, str)
                                and c.value.startswith("=")
                                and _FORMULA_BLOCKLIST.search(c.value)
                            ):
                                blocked_found.append(c.value)
                if blocked_found:
                    k8_ok = False
                    k8_detail = f"blocked formula survived in workbook: {blocked_found[:2]}"
                elif expected.get("formula_blocked"):
                    k8_ok = True
                    k8_detail = "blocked formula stripped/rejected as expected"
                elif expected.get("totals_are_formulas"):
                    has_sum = any(
                        cell.formula and "SUM" in cell.formula.upper()
                        for s in typed_spec.sheets
                        for cell in s.cells
                    )
                    if not has_sum:
                        k8_ok = False
                        k8_detail = "missing SUM formula for credit totals"
                if not k8_detail:
                    k8_detail = f"xlsx valid ({len(wb.sheetnames)} sheets)"
            elif isinstance(typed_spec, HtmlArtifactSpec):
                inert = render_inert_markup(typed_spec.html)
                if re.search(r"(?i)<script\b", inert) or re.search(r'(?i)\bon\w+\s*=', inert):
                    k8_ok = False
                    k8_detail = "uninert script or event handler survived in HTML"
                else:
                    k8_detail = "inert HTML sanitized"
        except Exception as exc:  # noqa: BLE001
            k8_ok = False
            k8_detail = f"export failed: {type(exc).__name__}: {exc}"
        results.append(CheckResult("K8", k8_ok, k8_detail))
    else:
        results.append(CheckResult("K8", True, "N/A"))

    # K9: Revisions: parent matches expected revision, parent bytes unchanged
    if expected.get("purpose") == "revise":
        if artifact_record and artifact_run_info:
            new_parent = str(artifact_record.get("parent_revision_id") or "")
            revises_info = turn.get("evidence", {}).get("research", {}).get("artifact", {}).get("revises")
            expected_rev = str(revises_info.get("revisionId") if revises_info else "")
            parent_match = bool(new_parent and (new_parent == expected_rev or not expected_rev))
            hash_match = True
            if parent_sha256_before and artifact_record.get("parent_revision_id"):
                p_row = conn.execute(
                    "SELECT spec FROM artifact_revisions WHERE revision_id = %s",
                    (artifact_record["parent_revision_id"],),
                ).fetchone()
                if p_row:
                    current_hash = hashlib.sha256(json.dumps(p_row["spec"], sort_keys=True).encode()).hexdigest()
                    hash_match = current_hash == parent_sha256_before
            k9_ok = parent_match and hash_match
            k9_detail = "revision parent verified and parent bytes unchanged" if k9_ok else "revision mismatch"
        else:
            k9_ok = False
            k9_detail = "revision artifact missing"
        results.append(CheckResult("K9", k9_ok, k9_detail))
    else:
        results.append(CheckResult("K9", True, "N/A"))

    # K10: Clean text: no prompt text, model name, job id, status code, or stack trace
    all_visible = (turn.get("text") or "") + "\n" + (extract_artifact_text(spec) if spec else "")
    job_id_str = str(turn.get("job_id") or "")
    leaks = []
    for pat in FORBIDDEN_LEAK_PATTERNS:
        m = pat.search(all_visible)
        if m:
            leaks.append(m.group(0))
    if job_id_str and job_id_str in all_visible:
        leaks.append(f"job_id:{job_id_str}")
    k10_ok = len(leaks) == 0
    k10_detail = "clean text" if k10_ok else f"leaks found: {leaks[:3]}"
    results.append(CheckResult("K10", k10_ok, k10_detail))

    # K11: Scope (P2.6): an off-topic case gets exactly the fixed decline and
    # nothing else; an on-topic case gets an answer, never the decline.
    reply = (turn.get("text") or "").strip()
    if expected.get("off_topic"):
        k11_ok = reply == SCOPE_DECLINE_TEXT
        k11_detail = "declined" if k11_ok else f"not declined: {reply[:80]!r}"
    elif expected.get("on_topic"):
        k11_ok = bool(reply) and reply != SCOPE_DECLINE_TEXT
        k11_detail = "answered" if k11_ok else "declined or empty"
    else:
        k11_ok, k11_detail = True, "N/A"
    results.append(CheckResult("K11", k11_ok, k11_detail))

    return results


def run_benchmark_offline(
    cases_path: Path,
    out_path: Path | None,
    case_filter: str | None,
    limit: int | None,
) -> int:
    """Offline dry-run mode: exercises cases file and check logic without network or LLM."""
    print(f"Running offline validation against {cases_path}...")
    with cases_path.open("r", encoding="utf-8") as f:
        cases = [json.loads(line) for line in f if line.strip()]
    if case_filter:
        cases = [c for c in cases if c["id"] == case_filter]
    if limit:
        cases = cases[:limit]

    print(f"Loaded {len(cases)} cases.")
    results: list[BenchmarkResult] = []
    for case in cases:
        c_id = case["id"]
        cat = case.get("category", "artifact")
        prompt = case["conversation"][-1]
        expected = case.get("expected", {})
        # Synthetic turn response and artifact for dry-run
        sim_turn: dict[str, Any] = {
            "text": (SCOPE_DECLINE_TEXT if expected.get("off_topic")
                     else f"Advising response for {c_id}"),
            "citations": [{"chunkId": "00000000-0000-0000-0000-000000000001"}],
        }
        sim_record: dict[str, Any] | None = None
        mock_ref = {
            "chunk_id": "00000000-0000-0000-0000-000000000001",
            "source_id": "00000000-0000-0000-0000-000000000002",
            "source_name": "Test Source",
            "locator_label": "Section 1",
            "text_snapshot": "Test text",
        }
        if expected.get("artifact"):
            kind = expected.get("kind", "document")
            purpose = expected.get("purpose", "create")
            sim_turn["artifactRun"] = {
                "runId": str(uuid4()),
                "kind": kind,
                "title": f"Plan for {c_id}",
                "purpose": purpose,
            }
            if kind == "document":
                spec_dict = {
                    "kind": "document",
                    "title": f"Document for {c_id}",
                    "blocks": [
                        {"id": "p1", "type": "paragraph", "text": "Semester 1: BIOL 141 and CHEM 150."}
                    ],
                    "evidence": {"p1": [mock_ref]},
                }
            elif kind == "workbook":
                cells = [
                    {"id": "c1", "row": 1, "column": 1, "value": "Course"},
                    {"id": "c2", "row": 1, "column": 2, "value": "Credits"},
                    {"id": "c3", "row": 2, "column": 1, "value": "BIOL 141"},
                    {"id": "c4", "row": 2, "column": 2, "value": 4},
                    {"id": "c5", "row": 3, "column": 1, "value": "Total"},
                    {"id": "c6", "row": 3, "column": 2, "formula": "=SUM(B2:B2)"},
                ]
                if expected.get("formula_blocked"):
                    cells.append({"id": "c7", "row": 4, "column": 1, "value": "WEBSERVICE blocked"})
                spec_dict = {
                    "kind": "workbook",
                    "title": f"Workbook for {c_id}",
                    "sheets": [{
                        "id": "s1",
                        "name": "Credits",
                        "cells": cells,
                    }],
                    "evidence": {"s1:B2": [mock_ref]},
                }
            else:
                spec_dict = {
                    "kind": "html",
                    "title": f"Page for {c_id}",
                    "html": "<p>Roadmap content</p>",
                    "evidence": {"root": [mock_ref]},
                }
            sim_record = {
                "artifact_id": str(uuid4()),
                "revision_id": str(uuid4()),
                "parent_revision_id": str(uuid4()) if purpose == "revise" else None,
                "spec": spec_dict,
            }
        elif expected.get("conflict"):
            sim_turn["artifactNotice"] = "The answer is saved, but the artifact could not be started."

        # Fake db connection mock for dry run
        class MockConn:
            def execute(self, *args, **kwargs):
                class MockCursor:
                    def fetchone(self):
                        return None
                    def fetchall(self):
                        return []
                return MockCursor()
            def commit(self):
                pass

        checks = evaluate_checks(
            MockConn(),
            case,
            sim_turn,
            sim_record,
            turn_seconds=1.2,
            run_seconds=2.5,
            queue_empty=True,
            known_courses={"BIOL 141", "CHEM 150", "CS 170", "MATH 111", "CHEM_OX 150"},
            campus_equivalents={"CHEM 150": frozenset({"CHEM_OX 150"})},
            collection_id=uuid4(),
        )
        b_res = BenchmarkResult(
            case_id=c_id,
            category=cat,
            prompt=prompt,
            turn_seconds=1.2,
            run_seconds=2.5,
            turn_response=sim_turn,
            artifact_record=sim_record,
            checks=checks,
        )
        results.append(b_res)

    print_results_table(results)
    if out_path:
        write_report(out_path, results)
    return 0


def print_results_table(results: list[BenchmarkResult], verbose: bool = False) -> None:
    """Print the per-case, per-check table."""
    headers = ["Case", "Category", "Turn", "Run", "K1", "K2", "K3", "K4", "K5", "K6", "K7", "K8", "K9", "K10", "K11", "Status"]
    print()
    print(f"{headers[0]:<5} {headers[1]:<16} {headers[2]:>6} {headers[3]:>6} " + " ".join(f"{h:>3}" for h in headers[4:14]) + f"  {headers[14]}")
    print("-" * 88)
    check_names = ["K1", "K2", "K3", "K4", "K5", "K6", "K7", "K8", "K9", "K10", "K11"]
    pass_counts = {k: 0 for k in check_names}
    total_passed = 0

    for r in results:
        check_map = {c.check: c for c in r.checks}
        k_cols = []
        for kn in check_names:
            c = check_map.get(kn)
            if c is None or c.detail == "N/A":
                k_cols.append(" --")
            elif c.ok:
                k_cols.append(" OK")
                pass_counts[kn] += 1
            else:
                k_cols.append("FAIL")
        status = "PASS" if r.passed else "FAIL"
        if r.passed:
            total_passed += 1
        print(
            f"{r.case_id:<5} {r.category:<16} {r.turn_seconds:>5.1f}s {r.run_seconds:>5.1f}s "
            + " ".join(k_cols)
            + f"  {status}"
        )
        if verbose or not r.passed:
            failed_checks = [f"{c.check}: {c.detail}" for c in r.checks if not c.ok]
            if failed_checks:
                print(f"      {' | '.join(failed_checks)}")

    print("-" * 88)
    print(f"CASES PASSED: {total_passed}/{len(results)}")
    summary_checks = ", ".join(f"{kn}: {pass_counts[kn]}/{len(results)}" for kn in check_names)
    print(f"CHECKS: {summary_checks}")
    print()


def write_report(out_path: Path, results: list[BenchmarkResult]) -> None:
    """Write machine-readable JSON report."""
    data = {
        "timestamp": time.time(),
        "total_cases": len(results),
        "passed_cases": sum(1 for r in results if r.passed),
        "results": [
            {
                "case_id": r.case_id,
                "category": r.category,
                "prompt": r.prompt,
                "turn_seconds": r.turn_seconds,
                "run_seconds": r.run_seconds,
                "passed": r.passed,
                "checks": [{"check": c.check, "ok": c.ok, "detail": c.detail} for c in r.checks],
            }
            for r in results
        ],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Report written to {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path("scripts/manual/artifact_benchmark_cases.jsonl"),
        help="Path to frozen benchmark cases JSONL",
    )
    parser.add_argument("--case", type=str, default=None, help="Run only this case ID")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of cases")
    parser.add_argument("--out", type=Path, default=None, help="Path to write JSON output")
    parser.add_argument("--offline", "--dry-run", action="store_true", help="Run offline validation")
    parser.add_argument("--verbose", action="store_true", help="Print debug details")
    args = parser.parse_args()

    cases_path = args.cases
    if not cases_path.is_absolute() and not cases_path.exists():
        cand = Path("backend") / cases_path
        if cand.exists():
            cases_path = cand

    if args.offline:
        sys.exit(run_benchmark_offline(cases_path, args.out, args.case, args.limit))

    settings = load_settings()
    with open_connection(settings) as startup_conn:
        dsn = resolved_database_url(settings, startup_conn)
        configure_runtime_database(dsn)
        settings = replace(settings, database_url=dsn)

        col_row = startup_conn.execute(
            "SELECT collection_id, name FROM collections WHERE name = %s",
            (settings.default_collection,),
        ).fetchone()
        if col_row is None:
            parser.error(f"default collection {settings.default_collection} not found")
        collection_id = col_row["collection_id"]

        c_rows = startup_conn.execute(
            queries.get("list_collection_course_codes"), {"collection_id": collection_id}
        ).fetchall()
        known_courses = {r["code"] for r in c_rows}
        campus_equivalents = load_campus_equivalents(startup_conn, collection_id=collection_id)

    handlers = build_worker_handlers(settings)
    artifact_executor = RunExecutor(
        handlers.artifacts,
        data_dir=settings.data_dir,
        max_storage_bytes=settings.workspace_storage_max_bytes,
    )
    executor = KindDispatchingExecutor(conversation=handlers.conversation, artifacts=artifact_executor)
    connect_fn = connector(settings)
    store = JobStore(connect_fn)

    app = create_app(settings=replace(settings, tools_enabled=True, advising_research_enabled=True))
    app.state.database_ready = True
    app.state.job_store = store
    app.state.conversation_job_store = store

    with cases_path.open("r", encoding="utf-8") as f:
        cases = [json.loads(line) for line in f if line.strip()]
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
    if args.limit:
        cases = cases[:args.limit]

    print(f"Running live benchmark ({len(cases)} cases) against collection '{settings.default_collection}'...")
    results: list[BenchmarkResult] = []
    artifact_registry: dict[str, dict[str, Any]] = {}
    workspace_key = "development"

    # Development sign-in accepts only loopback clients; TestClient defaults to "testclient".
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50002),
                    raise_server_exceptions=False) as client:
        for case in cases:
            c_id = case["id"]
            cat = case.get("category", "artifact")
            expected = case.get("expected", {})
            conv_messages = case.get("conversation", [])
            print(f"Starting {c_id} ({cat})...", flush=True)

            # Create conversation
            create_res = client.post("/api/conversations", json={"title": f"Benchmark {c_id}"})
            if create_res.status_code != 200:
                print(f"  {c_id} failed to create conversation: {create_res.status_code}")
                continue
            conv_id = create_res.json()["conversationId"]

            parent_sha256_before = None
            last_turn_record: dict[str, Any] = {}
            turn_sec = 0.0
            run_sec = 0.0
            last_record: dict[str, Any] | None = None

            for seq_idx, msg in enumerate(conv_messages):
                req_id = str(uuid4())
                expected_seq = seq_idx * 2
                body: dict[str, Any] = {
                    "query": msg,
                    "requestId": req_id,
                    "expectedSequence": expected_seq,
                    "collectionId": settings.default_collection,
                }
                # Check if this case references an earlier artifact
                if expected.get("purpose") == "revise" and seq_idx == len(conv_messages) - 1:
                    ref_case_id = expected.get("revises_case")
                    if ref_case_id and ref_case_id in artifact_registry:
                        ref_info = artifact_registry[ref_case_id]
                        body["artifactReferences"] = [{
                            "artifactId": ref_info["artifact_id"],
                            "revisionId": ref_info["revision_id"],
                        }]
                        parent_sha256_before = ref_info.get("sha256")
                elif expected.get("revises_stale") and seq_idx == len(conv_messages) - 1:
                    # Case A19: reference revision 1 after revision 2 exists
                    a01_info = artifact_registry.get("A01")
                    if a01_info:
                        body["artifactReferences"] = [{
                            "artifactId": a01_info["artifact_id"],
                            "revisionId": a01_info["revision_id"],
                        }]

                t0 = time.monotonic()
                stream_res = client.post(f"/api/conversations/{conv_id}/turns/stream", json=body)
                if stream_res.status_code != 202:
                    turn_sec = time.monotonic() - t0
                    last_turn_record = {"text": f"Refusal/error: {stream_res.text}", "status": "failed"}
                    break

                # Drain worker until turn is done
                while time.monotonic() - t0 <= 90:
                    run_once(store, f"bench-{c_id}", executor, lease_seconds=60)
                    events_res = client.get(f"/api/conversations/{conv_id}/turns/{req_id}/events")
                    if events_res.status_code == 200 and events_res.json().get("done"):
                        break
                    time.sleep(0.1)
                turn_sec = time.monotonic() - t0

                # Fetch updated conversation record
                c_data = client.get(f"/api/conversations/{conv_id}").json()
                matching_turns = [t for t in c_data.get("turns", [])
                                  if t.get("requestId") == req_id and t.get("role") == "assistant"]
                if matching_turns:
                    last_turn_record = matching_turns[0]

                # If artifact run was admitted, execute artifact worker job
                art_run = last_turn_record.get("artifactRun")
                if art_run and seq_idx == len(conv_messages) - 1:
                    run_id = art_run["runId"]
                    t_run0 = time.monotonic()
                    while time.monotonic() - t_run0 <= 150:
                        run_once(store, f"bench-{c_id}-art", executor, lease_seconds=60)
                        run_check = client.get(f"/api/runs/{run_id}")
                        if run_check.status_code == 200:
                            st = run_check.json().get("state")
                            if st in ("succeeded", "failed", "cancelled"):
                                break
                        time.sleep(0.1)
                    run_sec = time.monotonic() - t_run0

                    # Load published artifact
                    with open_connection(settings) as a_conn:
                        astorage = ArtifactStore(a_conn, workspace_key=workspace_key, data_dir=settings.data_dir)
                        art_id_str = run_check.json().get("artifactIds", [None])[0]
                        if art_id_str:
                            last_record = astorage.get(UUID(art_id_str))
                            artifact_registry[c_id] = {
                                "artifact_id": str(last_record["artifact_id"]),
                                "revision_id": str(last_record["revision_id"]),
                                "sha256": hashlib.sha256(json.dumps(last_record["spec"], sort_keys=True).encode()).hexdigest(),
                            }

            # Check queue emptiness
            with open_connection(settings) as q_conn:
                q_row = q_conn.execute(
                    "SELECT count(*) AS n FROM jobs WHERE scope_key = %s AND status IN ('queued', 'running')",
                    (workspace_key,),
                ).fetchone()
                queue_empty = (q_row["n"] == 0) if q_row else True

            with open_connection(settings) as eval_conn:
                checks = evaluate_checks(
                    eval_conn,
                    case,
                    last_turn_record,
                    last_record,
                    turn_seconds=turn_sec,
                    run_seconds=run_sec,
                    queue_empty=queue_empty,
                    known_courses=known_courses,
                    campus_equivalents=campus_equivalents,
                    collection_id=collection_id,
                    parent_sha256_before=parent_sha256_before,
                )

            res = BenchmarkResult(
                case_id=c_id,
                category=cat,
                prompt=conv_messages[-1],
                turn_seconds=turn_sec,
                run_seconds=run_sec,
                turn_response=last_turn_record,
                artifact_record=last_record,
                checks=checks,
            )
            results.append(res)
            print(f"  {c_id} completed: {'PASS' if res.passed else 'FAIL'}", flush=True)

    close_pools()
    print_results_table(results)
    if args.out:
        write_report(args.out, results)


if __name__ == "__main__":
    main()
