"""Comprehensive LLM Advisor System Audit Runner.

Queries the configured LLM across all Emory STEM majors, minors, schools,
electives, and policies, recording responses for factual verification against
official Emory sources.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Add backend to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config import load_settings
from app.llm import get_provider
from app.llm.base import GenerationRequest, TextMessage

TEST_CASES = [
    # ==========================================
    # GROUP 1: STEM MAJORS (Core & Requirements)
    # ==========================================
    {
        "id": "major-compsci-bs",
        "category": "major",
        "subject": "Computer Science BS",
        "query": "What are the exact core course requirements and required math courses for the Computer Science BS at Emory University? List the specific course numbers and prerequisites.",
    },
    {
        "id": "major-compsci-ba",
        "category": "major",
        "subject": "Computer Science BA",
        "query": "What are the core requirements and credit hours for the Computer Science BA at Emory? How does it differ from the BS in terms of math and CS electives?",
    },
    {
        "id": "major-math-bs",
        "category": "major",
        "subject": "Mathematics BS",
        "query": "What are the required courses for the Mathematics BS at Emory University? Detail the foundational sequences, breadth requirements, and credit totals.",
    },
    {
        "id": "major-math-ba",
        "category": "major",
        "subject": "Mathematics BA",
        "query": "What are the required courses for the Mathematics BA at Emory? How many total credit hours and electives are required?",
    },
    {
        "id": "major-applied-math-bs",
        "category": "major",
        "subject": "Applied Mathematics BS",
        "query": "What are the specific requirements for the Applied Mathematics BS at Emory? Which computing and applied tracks are required or accepted?",
    },
    {
        "id": "major-math-cs-bs",
        "category": "major",
        "subject": "Mathematics & Computer Science BS",
        "query": "What are the requirements for the joint Mathematics & Computer Science BS at Emory University? List all core CS and Math courses.",
    },
    {
        "id": "major-econ-cs",
        "category": "major",
        "subject": "Economics & Computer Science",
        "query": "What are the requirements for the joint Economics and Computer Science major at Emory? Which economics core courses and CS core courses are mandated?",
    },
    {
        "id": "major-econ-math-ba",
        "category": "major",
        "subject": "Economics & Mathematics BA",
        "query": "What are the requirements for the joint Economics and Mathematics BA at Emory University? List the required Econ and Math sequences.",
    },
    {
        "id": "major-math-pols-ba",
        "category": "major",
        "subject": "Mathematics & Political Science BA",
        "query": "What are the requirements for the joint Mathematics & Political Science BA at Emory? What math courses and political theory/methods courses are required?",
    },
    {
        "id": "major-applied-math-stats-bs",
        "category": "major",
        "subject": "Applied Math & Statistics BS",
        "query": "What are the requirements for the Applied Mathematics & Statistics BS (joint with QTM/Data Science) at Emory? List the required QTM and Math courses.",
    },
    {
        "id": "major-biology-bs",
        "category": "major",
        "subject": "Biology BS",
        "query": "What are the requirements for the Biology BS at Emory University? List the introductory core, secondary science requirements (chemistry, physics, math), and elective hours.",
    },
    {
        "id": "major-biology-ba",
        "category": "major",
        "subject": "Biology BA",
        "query": "What are the requirements for the Biology BA at Emory University? How does it differ from the Biology BS in secondary science requirements?",
    },
    {
        "id": "major-chem-bs",
        "category": "major",
        "subject": "Chemistry BS",
        "query": "What are the requirements for the Chemistry BS at Emory University under the Emory 360 Chemistry curriculum? List the core foundational courses (CHEM 150, 202, etc.) and required tracks.",
    },
    {
        "id": "major-chem-ba",
        "category": "major",
        "subject": "Chemistry BA",
        "query": "What are the requirements for the Chemistry BA at Emory University? How does it differ from the Chemistry BS in terms of course requirements and laboratory credits?",
    },
    {
        "id": "major-biophysics-bs",
        "category": "major",
        "subject": "Biophysics BS",
        "query": "What are the degree requirements for the Biophysics BS at Emory University? Which physics, chemistry, biology, and math courses are required?",
    },
    {
        "id": "major-physics-bs",
        "category": "major",
        "subject": "Physics BS",
        "query": "What are the requirements for the Physics BS at Emory University? List the credit hours, core physics courses, math prerequisites, and Quantum Information concentration options.",
    },
    {
        "id": "major-physics-ba",
        "category": "major",
        "subject": "Physics BA",
        "query": "What are the requirements for the Physics BA at Emory? How many total credit hours and which physics/math courses are required?",
    },
    {
        "id": "major-physics-astronomy-bs",
        "category": "major",
        "subject": "Physics & Astronomy BS",
        "query": "What are the requirements for the Physics & Astronomy BS at Emory University? Which astronomy and physics courses are required?",
    },
    {
        "id": "major-engsci-bs",
        "category": "major",
        "subject": "Engineering Science BS",
        "query": "What are the requirements for the Engineering Science BS at Emory University? Which engineering tracks and foundational science courses are mandated?",
    },
    {
        "id": "major-nbb-bs",
        "category": "major",
        "subject": "Neuroscience & Behavioral Biology BS",
        "query": "What are the requirements for the Neuroscience and Behavioral Biology (NBB) BS at Emory University? List the core courses, secondary science requirements, and elective groups.",
    },
    {
        "id": "major-envs-bs",
        "category": "major",
        "subject": "Environmental Sciences BS",
        "query": "What are the requirements for the Environmental Sciences (ENVS) BS at Emory University? List the core, quantitative/analytic requirements, and elective tracks.",
    },
    {
        "id": "major-envs-ba",
        "category": "major",
        "subject": "Environmental Sciences BA",
        "query": "What are the requirements for the Environmental Sciences (ENVS) BA at Emory? How does it differ from the BS in math and quantitative science requirements?",
    },
    {
        "id": "major-psychology-bs",
        "category": "major",
        "subject": "Psychology BS",
        "query": "What are the requirements for the Psychology BS at Emory University? Which quantitative and natural science courses are required for the BS versus BA?",
    },
    {
        "id": "major-qss-bs",
        "category": "major",
        "subject": "Quantitative Sciences BS",
        "query": "What are the requirements for the Quantitative Sciences (QSS) BS at Emory University? How does the QTM core combine with a substantive field track?",
    },
    {
        "id": "major-qppa-bs",
        "category": "major",
        "subject": "Public Policy Analysis BS",
        "query": "What are the requirements for the Public Policy Analysis BS at Emory University? List the QTM and political science core courses.",
    },
    {
        "id": "major-anthhbio-bs",
        "category": "major",
        "subject": "Anthropology & Human Biology BS",
        "query": "What are the requirements for the Anthropology & Human Biology BS at Emory University? Which biology, anthropology, and chemistry courses are required?",
    },

    # ==========================================
    # GROUP 2: MINORS
    # ==========================================
    {
        "id": "minor-ai",
        "category": "minor",
        "subject": "Artificial Intelligence Minor",
        "query": "What are the exact requirements for the Artificial Intelligence (AI) Minor at Emory University? List the required CS core courses and the approved elective categories.",
    },
    {
        "id": "minor-comp-inf",
        "category": "minor",
        "subject": "Computer Informatics Minor",
        "query": "What are the requirements for the Computer Informatics Minor at Emory University? Which CS and informatics courses count?",
    },
    {
        "id": "minor-cs",
        "category": "minor",
        "subject": "Computer Science Minor",
        "query": "What are the requirements for the Computer Science Minor at Emory University? What are the required courses, math prerequisites, and total credits?",
    },
    {
        "id": "minor-math",
        "category": "minor",
        "subject": "Mathematics Minor",
        "query": "What are the requirements for the Mathematics Minor at Emory University? How many courses and at what levels (200, 300, 400) are required?",
    },
    {
        "id": "minor-neuroethics",
        "category": "minor",
        "subject": "Neuroethics Minor",
        "query": "What are the requirements for the Neuroethics Minor at Emory? Which NBB and Philosophy courses are required or accepted?",
    },
    {
        "id": "minor-nutrition",
        "category": "minor",
        "subject": "Nutrition Science Minor",
        "query": "What are the requirements for the Nutrition Science Minor at Emory University? Which department offers it and what courses are required?",
    },
    {
        "id": "minor-physics",
        "category": "minor",
        "subject": "Physics Minor",
        "query": "What are the requirements for the Physics Minor at Emory University? List the required introductory courses and elective credits.",
    },
    {
        "id": "minor-astronomy",
        "category": "minor",
        "subject": "Astronomy Minor",
        "query": "What are the requirements for the Astronomy Minor at Emory University? Which astronomy and physics courses count?",
    },
    {
        "id": "minor-envs",
        "category": "minor",
        "subject": "Environmental Sciences Minor",
        "query": "What are the requirements for the Environmental Sciences Minor at Emory? List the required introductory course and elective credits.",
    },
    {
        "id": "minor-sustainability",
        "category": "minor",
        "subject": "Sustainability Sciences Minor",
        "query": "What are the requirements for the Sustainability Sciences Minor at Emory? Which foundational and elective courses are required?",
    },
    {
        "id": "minor-qss",
        "category": "minor",
        "subject": "Quantitative Sciences Minor",
        "query": "What are the requirements for the Quantitative Sciences (QSS) Minor at Emory University? List the required QTM core courses.",
    },

    # ==========================================
    # GROUP 3: SCHOOLS & DIVISIONS
    # ==========================================
    {
        "id": "school-oxford-cs",
        "category": "school",
        "subject": "Oxford College CS & Equivalency",
        "query": "Does taking CS_OX 170 and CS_OX 171 at Oxford College satisfy the prerequisites for Atlanta campus CS 253 at Emory University? Are Oxford courses equivalent to Atlanta courses?",
    },
    {
        "id": "school-oxford-transition",
        "category": "school",
        "subject": "Oxford College Continuation",
        "query": "How do students at Oxford College transition to Emory College of Arts and Sciences to complete a STEM major? What degree do they earn at Oxford and when do they declare their major?",
    },
    {
        "id": "school-goizueta-joint",
        "category": "school",
        "subject": "Goizueta Business School Joint STEM",
        "query": "Can an Emory student pursue a double major between a STEM major in Emory College (like Computer Science or Applied Math) and the BBA program in Goizueta Business School? What is the official policy on joint/secondary majors?",
    },
    {
        "id": "school-medicine-premed",
        "category": "school",
        "subject": "Emory School of Medicine Pre-Med Requirements",
        "query": "What coursework does the Emory University School of Medicine require for admission to its MD program? Detail the required biology, chemistry, physics, math, and English courses.",
    },
    {
        "id": "school-laney-4plus1",
        "category": "school",
        "subject": "Laney Graduate School 4+1 BS/MS",
        "query": "What are the rules for Emory's 4+1 BS/MS programs (such as in Computer Science or Cancer Biology)? How many credit hours can be double-counted between the undergraduate and master's degrees?",
    },

    # ==========================================
    # GROUP 4: ELECTIVES & SPECIAL TOPICS
    # ==========================================
    {
        "id": "elective-cs-300-400",
        "category": "elective",
        "subject": "CS 300/400 Electives",
        "query": "Which 300-level and 400-level courses count as electives for the Computer Science BS at Emory? Do independent study courses like CS 495 or research courses count toward the major elective requirement?",
    },
    {
        "id": "elective-ai-minor-econ",
        "category": "elective",
        "subject": "AI Minor Economics Electives",
        "query": "Do ECON 315 and ECON 320 count as electives for the Artificial Intelligence Minor at Emory University? Which other non-CS departments offer electives for the AI minor?",
    },
    {
        "id": "elective-ai-minor-nbb",
        "category": "elective",
        "subject": "AI Minor NBB Elective",
        "query": "Does NBB 480 count as an approved elective for the Artificial Intelligence Minor at Emory? Does being listed as an elective mean a student can enroll without taking NBB prerequisites?",
    },
    {
        "id": "elective-qtm-substantive",
        "category": "elective",
        "subject": "QSS Substantive Field Electives",
        "query": "How many courses are required for the substantive track in the Quantitative Sciences (QSS) BS, and can those courses double-count if the student double-majors in that substantive field?",
    },
    {
        "id": "elective-research-caps",
        "category": "elective",
        "subject": "Undergraduate Research Course Caps",
        "query": "What is the maximum number of credit hours of undergraduate research or directed study (e.g., 399R, 499R) that can count toward an Emory College major or toward graduation?",
    },

    # ==========================================
    # GROUP 5: ACADEMIC POLICIES & REGULATIONS
    # ==========================================
    {
        "id": "policy-su-credits",
        "category": "policy",
        "subject": "S/U Credit Limit",
        "query": "What is the maximum number of credit hours a student may take under the Satisfactory/Unsatisfactory (S/U) grading basis toward an Emory College undergraduate degree? Do ARCHE consortium courses count toward this limit?",
    },
    {
        "id": "policy-su-deadline",
        "category": "policy",
        "subject": "S/U Grading Basis Deadline",
        "query": "What is the exact deadline (in terms of instructional days or semester timeline) for an Emory College student to change the grading basis of a course from letter grade to S/U in a regular fall or spring semester?",
    },
    {
        "id": "policy-su-major-restriction",
        "category": "policy",
        "subject": "S/U Major Course Restriction",
        "query": "Can an Emory College student take required courses for their STEM major or minor on an S/U (Satisfactory/Unsatisfactory) basis?",
    },
    {
        "id": "policy-graduation-credits",
        "category": "policy",
        "subject": "Minimum Graduation Credits",
        "query": "How many total academic credit hours and semesters in residence are required to graduate with a Bachelor's degree from Emory College of Arts and Sciences?",
    },
    {
        "id": "policy-double-counting",
        "category": "policy",
        "subject": "Double Major / Minor Double-Counting Policy",
        "query": "What is Emory College's general policy on double-counting courses between two majors, or between a major and a minor?",
    },

    # ==========================================
    # GROUP 6: PREREQUISITES & ADVERSARIAL EDGE CASES
    # ==========================================
    {
        "id": "prereq-cs170-cs171-cs253",
        "category": "prerequisite",
        "subject": "CS Prerequisite Sequence",
        "query": "Can a student take CS 253 at Emory if they have completed CS 170 with an A, but have not taken CS 171? What are the exact prerequisites for CS 253?",
    },
    {
        "id": "prereq-math111-math112",
        "category": "prerequisite",
        "subject": "Math Calculus Sequence",
        "query": "What are the prerequisite options for MATH 112 (Calculus II) at Emory University? Does AP credit for Calculus AB or BC fulfill it?",
    },
    {
        "id": "prereq-chem-sequence",
        "category": "prerequisite",
        "subject": "Chemistry Core Sequence",
        "query": "What is the order of the chemistry core at Emory: CHEM 150, CHEM 202, CHEM 203, CHEM 204? What is CHEM 202Z and who takes it?",
    },
    {
        "id": "edge-invented-course",
        "category": "edge_case",
        "subject": "Invented Course DATASCI 350",
        "query": "Does DATASCI 350 count as a required core course for the Data Science BS at Emory University? What prerequisites does it have?",
    },
    {
        "id": "edge-live-seats",
        "category": "edge_case",
        "subject": "Live Seat Guarantees",
        "query": "How many seats are currently open in CS 170 for next semester at Emory, and can you guarantee my enrollment?",
    },
    {
        "id": "edge-salary-guarantee",
        "category": "edge_case",
        "subject": "Salary Guarantee",
        "query": "If I complete the Emory Computer Science BS, does Emory University guarantee that I will receive a starting salary of at least $120,000?",
    },
]


def run_audit(output_file: Path, start_idx: int = 0, count: int | None = None,
              timeout_seconds: float = 600.0, case_filter: str | None = None):
    settings = load_settings()
    provider = get_provider(settings, timeout_seconds=timeout_seconds)

    cases_to_run = TEST_CASES
    if case_filter:
        cases_to_run = [c for c in cases_to_run if case_filter.lower() in c["id"].lower() or case_filter.lower() in c["subject"].lower()]
        print(f"Filtered to {len(cases_to_run)} cases matching '{case_filter}'")

    cases_to_run = cases_to_run[start_idx:]
    if count is not None:
        cases_to_run = cases_to_run[:count]

    results = []
    if output_file.exists():
        try:
            with open(output_file, "r", encoding="utf-8") as f:
                results = json.load(f)
            print(f"Loaded {len(results)} existing results from {output_file}")
        except Exception:  # noqa: BLE001 - corrupt local cache is ignored
            results = []

    # Only skip cases that succeeded with non-empty text; retry any previous errors/timeouts/empty
    existing_ids = {r["id"] for r in results if r.get("status") == "success" and r.get("response_text")}
    results = [r for r in results if r.get("id") in existing_ids]

    print(f"Running audit against LLM: {settings.llm_model_id} ({settings.llm_provider})")
    print(f"Request timeout: {timeout_seconds}s (streaming)")
    print(f"Total cases to run: {len(cases_to_run)}")

    for i, case in enumerate(cases_to_run, 1):
        if case["id"] in existing_ids:
            print(f"[{i}/{len(cases_to_run)}] Skipping {case['id']} (already run successfully)")
            continue

        print(f"\n[{i}/{len(cases_to_run)}] Querying {case['id']} ({case['subject']})...", end="", flush=True)
        t0 = time.time()
        try:
            request = GenerationRequest(
                messages=[TextMessage(role="user", content=case["query"])],
                temperature=0.0,
                max_output_tokens=None,
                timeout_seconds=timeout_seconds,
            )
            chunks = []
            final_res = None
            first_token_time = None
            for event in provider.stream(request):
                if event.type == "text_delta":
                    if first_token_time is None:
                        first_token_time = time.time() - t0
                        print(f" [First token: {first_token_time:.1f}s]", end="", flush=True)
                    chunks.append(event.text)
                elif event.type == "completed":
                    final_res = event.result

            elapsed = time.time() - t0
            full_text = "".join(chunks)
            finish_reason = final_res.finish_reason if final_res else "unknown"

            record = {
                "id": case["id"],
                "category": case["category"],
                "subject": case["subject"],
                "query": case["query"],
                "provider": settings.llm_provider,
                "model": settings.llm_model_id,
                "status": "success" if full_text.strip() else "empty",
                "elapsed_seconds": round(elapsed, 2),
                "first_token_seconds": round(first_token_time, 2) if first_token_time else None,
                "response_text": full_text,
                "finish_reason": finish_reason,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            print(f" -> Done in {elapsed:.1f}s ({len(full_text)} chars, finish={finish_reason})")
        except Exception as exc:  # noqa: BLE001 - one probe failure must not stop the run
            elapsed = time.time() - t0
            print(f" -> FAILED in {elapsed:.2f}s: {exc}")
            record = {
                "id": case["id"],
                "category": case["category"],
                "subject": case["subject"],
                "query": case["query"],
                "status": "error",
                "elapsed_seconds": round(elapsed, 2),
                "error": str(exc),
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }

        results.append(record)
        # Save after each query
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\nAudit completed. Total records saved: {len(results)} to {output_file}")


def run_product(output_file: Path, case_filter: str | None = None) -> None:
    """Ask every case through the product's /api/query and grade it mechanically.

    The bare-model audit above measures the model alone. This asks the same
    prompts through the product's research path, and scores each
    answer with the clarity probe's checks for the spec's checkable rules:
    course codes that exist (which also catches wrong campus prefixes), no
    promised outcome (seats, admission, salary), and citations that resolve.
    """
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from app.api import create_app
    from app.db.connection import open_connection, resolved_database_url
    from scripts.manual.advising_clarity_probe import (
        Probe,
        Result,
        _check_citations_resolve,
        _check_code_existence,
        _check_no_promise,
    )

    settings = load_settings()
    if settings.llm_provider == "hf-local" or not settings.llm_api_key:
        raise SystemExit("configure a real cloud provider and key locally")
    cases = [c for c in TEST_CASES if not case_filter
             or case_filter.lower() in c["id"].lower()]
    report = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "provider": settings.llm_provider, "model": settings.llm_model_id,
              "cases": len(cases), "results": []}
    with open_connection(settings) as conn:
        dsn = resolved_database_url(settings, conn)
        chunk_text = {str(r["chunk_id"]): r["text"] for r in
                      conn.execute("select chunk_id, text from chunks").fetchall()}
        conn.commit()
        collection = settings.default_collection
        app = create_app(settings=replace(settings, database_url=dsn))
        app.state.database_ready = True
        with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50001),
                        raise_server_exceptions=False) as client:
            for case in cases:
                started = time.perf_counter()
                try:
                    response = client.post("/api/query", json={
                        "query": case["query"], "collectionId": collection})
                    payload = response.json()
                except (RuntimeError, ValueError, KeyError) as exc:
                    print(f"  {case['id']}: ERROR {exc}", flush=True)
                    continue
                result = Result(
                    probe=Probe(case["id"], case["query"], case["category"]),
                    http=response.status_code,
                    seconds=round(time.perf_counter() - started, 2),
                    answer=payload.get("answer") or "",
                    reason=payload.get("reason"),
                    citations=payload.get("citations") or [])
                result.cited_text = [chunk_text.get(c.get("chunkId"), "")
                                     for c in result.citations]
                if response.status_code == 200:
                    _check_code_existence(conn, result)
                    _check_no_promise(result)
                    _check_citations_resolve(conn, result)
                    conn.commit()  # end the read so no transaction spans the run
                else:
                    result.checks.append({"check": "answered", "ok": False,
                                          "detail": f"HTTP {response.status_code}"})
                failed = result.failed
                report["results"].append({
                    "id": case["id"], "http": response.status_code,
                    "seconds": result.seconds, "type": payload.get("type"),
                    "answer": result.answer, "reason": result.reason,
                    "citations": result.citations, "checks": result.checks})
                print(f"  {case['id']:<34} {response.status_code} "
                      f"{result.seconds:>6.1f}s"
                      + (f"  FAILED {[c.get('code') or c['check'] for c in failed]}"
                         if failed else ""), flush=True)
    rows = report["results"]
    clean = sum(1 for r in rows if all(c["ok"] for c in r["checks"]))
    print(f"{clean}/{len(rows)} cases fully passed")
    output_file.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str),
                           encoding="utf-8")
    print(f"wrote {output_file}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--product", action="store_true",
                        help="Ask through the product's /api/query and grade mechanically")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--count", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--filter", type=str, default=None)
    parser.add_argument("--output", type=str, default="data/manual-checks/llm_audit_responses_minimax.json")
    args = parser.parse_args()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    if args.product:
        run_product(out, case_filter=args.filter)
        raise SystemExit(0)
    run_audit(out, start_idx=args.start, count=args.count, timeout_seconds=args.timeout, case_filter=args.filter)
