"""Tests for deterministic degree audit engine, service, and routes (Item 2)."""

from uuid import uuid4

import pytest
from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app.advising.audit_engine import DeterministicAuditEngine
from app.advising.audit_service import DegreeAuditService
from app.auth.models import Principal
from app.db import queries
from app.programs.rules import CoverageDisposition, RuleNode, RuleNodeType
from tests.helpers import api_client


def _create_user(db, *, role="student", status="active", workspace=None, email=None):
    user_id = uuid4()
    workspace_key = workspace or f"ws-{user_id}"
    email_addr = email or f"{user_id}@emory.edu"
    db.execute(
        queries.get("upsert_app_user"),
        {
            "user_id": user_id,
            "issuer": "https://auth.emory.edu",
            "subject": str(user_id),
            "workspace_key": workspace_key,
            "display_name": f"User {user_id}",
            "email": email_addr,
            "bootstrap": False,
        },
    ).fetchone()
    db.execute(
        queries.get("admin_update_user"),
        {"user_id": user_id, "role": role, "status": status},
    )
    db.commit()
    return Principal(
        workspace_key=workspace_key,
        issuer="https://auth.emory.edu",
        subject=str(user_id),
        display_name=f"User {user_id}",
        email=email_addr,
        role=role,
        user_id=user_id,
        status=status,
    )


@pytest.mark.parametrize("grade,status,verified,expected", [
    ("D", "completed", True, "outstanding"),
    ("B", "completed", True, "satisfied"),
    ("B", "completed", False, "conditional_satisfied"),
    ("IP", "in_progress", False, "in_progress_satisfied"),
    (None, "planned", False, "conditional_satisfied"),
    ("P", "completed", True, "unresolved"),
    (None, "completed", True, "unresolved"),
])
def test_engine_exact_course_and_grade_threshold(grade, status, verified, expected):
    rule = RuleNode("req1", RuleNodeType.EXACT_COURSE, "Intro to CS",
                    course_codes=("CS 170",), min_grade="C")
    attempt = {
        "attempt_id": "att1", "submitted_course_code": "CS 170", "grade": grade,
        "status": status, "attempted_credits": 4.0,
        "earned_credits": 4.0 if status == "completed" else 0.0, "is_verified": verified,
    }
    result = DeterministicAuditEngine(rule, [attempt]).run()
    assert result.overall_status == expected
    if expected in {"conditional_satisfied", "in_progress_satisfied"}:
        assert result.assumptions
    if expected in {"outstanding", "unresolved"}:
        assert result.blockers


def test_engine_non_greedy_allocation_and_no_double_count():
    # Tree: ALL_OF [ Req 1: EXACT CS 170, Req 2: CHOOSE 1 from (CS 170, CS 255) ]
    tree = RuleNode(
        node_id="root",
        node_type=RuleNodeType.ALL_OF,
        title="CS Core",
        children=(
            RuleNode(
                node_id="r1",
                node_type=RuleNodeType.EXACT_COURSE,
                title="CS 170 Req",
                course_codes=("CS 170",),
            ),
            RuleNode(
                node_id="r2",
                node_type=RuleNodeType.CHOOSE_COUNT,
                title="Elective Req",
                course_codes=("CS 170", "CS 255"),
                count=1,
            ),
        ),
    )

    # Case A: Student has CS 170 and CS 255.
    # Non-greedy search must allocate CS 170 to r1 and CS 255 to r2, satisfying both!
    attempts = [
        {"attempt_id": "a1", "submitted_course_code": "CS 170", "status": "completed", "grade": "A", "is_verified": True, "attempted_credits": 4.0, "earned_credits": 4.0},
        {"attempt_id": "a2", "submitted_course_code": "CS 255", "status": "completed", "grade": "A", "is_verified": True, "attempted_credits": 3.0, "earned_credits": 3.0},
    ]
    res = DeterministicAuditEngine(tree, attempts).run()
    assert res.overall_status == "satisfied"
    assert res.completed_credits == 7.0

    # Case B: Student has ONLY CS 170.
    # Cannot double count CS 170 for both r1 and r2!
    attempts_single = [
        {"attempt_id": "a1", "submitted_course_code": "CS 170", "status": "completed", "grade": "A", "is_verified": True, "attempted_credits": 4.0, "earned_credits": 4.0},
    ]
    res_single = DeterministicAuditEngine(tree, attempts_single).run()
    assert res_single.overall_status == "outstanding"
    assert res_single.completed_credits == 4.0


def test_engine_repeats_and_unresolved_zero_false_satisfaction():
    # 1. Repeats
    tree = RuleNode(
        node_id="root",
        node_type=RuleNodeType.EXACT_COURSE,
        title="Intro",
        course_codes=("CS 170",),
    )
    # Student failed with F in Fall 2023, then passed with A in Spring 2024
    attempts_repeat = [
        {"attempt_id": "a_f", "submitted_course_code": "CS 170", "term": "Fall 2023", "grade": "F", "status": "failed", "attempted_credits": 4.0, "earned_credits": 0.0},
        {"attempt_id": "a_pass", "submitted_course_code": "CS 170", "term": "Spring 2024", "grade": "A", "status": "completed", "attempted_credits": 4.0, "earned_credits": 4.0, "is_verified": True},
    ]
    res_rep = DeterministicAuditEngine(tree, attempts_repeat).run()
    assert res_rep.overall_status == "satisfied"
    assert res_rep.completed_credits == 4.0

    # 2. Unresolved rule node prevents overall satisfaction!
    tree_with_unresolved = RuleNode(
        node_id="root",
        node_type=RuleNodeType.ALL_OF,
        title="Major",
        children=(
            RuleNode(
                node_id="c1",
                node_type=RuleNodeType.EXACT_COURSE,
                title="Intro",
                course_codes=("CS 170",),
            ),
            RuleNode(
                node_id="c2",
                node_type=RuleNodeType.UNRESOLVED,
                title="Ambiguous Policy",
                coverage_disposition=CoverageDisposition.UNRESOLVED,
                unresolved_reason="Policy wording requires faculty review",
            ),
        ),
    )
    res_unres = DeterministicAuditEngine(tree_with_unresolved, attempts_repeat).run()
    # MUST be unresolved! Never satisfied when a required node is unresolved.
    assert res_unres.overall_status == "unresolved"
    assert any("Ambiguous Policy" in b for b in res_unres.blockers)


@pytest.mark.parametrize("case,expected,credits", [
    ("empty_all_of", "outstanding", 0),
    ("all_of_courses", "satisfied", 6),
    ("five_credit_courses", "satisfied", 15),
    ("zero_credit", "satisfied", 0),
    ("repeated_course", "outstanding", 3),
    ("nested_alternative", "satisfied", 6),
    ("unused_unknown_alternative", "satisfied", 3),
    ("search_limit", "unresolved", 0),
    ("many_required", "satisfied", 75),
    ("informational_only", "unresolved", 0),
    ("repeated_status", "unresolved", 3),
])
def test_engine_supported_allocation_boundaries(case, expected, credits):
    codes = ["CS 170", "CS 171", "CS 224", "CS 255", "CS 370"]
    if case == "many_required":
        codes = [f"CS {100 + i}" for i in range(25)]
    attempts = [{
        "attempt_id": f"attempt-{i}", "submitted_course_code": code,
        "status": "completed", "grade": "A", "attempted_credits": 3,
        "earned_credits": 3, "is_verified": True,
    } for i, code in enumerate(codes)]
    exact = RuleNode("exact", RuleNodeType.EXACT_COURSE, "CS 170", course_codes=(codes[0],))
    rule = RuleNode("all", RuleNodeType.ALL_OF, "Both courses", course_codes=tuple(codes[:2]))
    limit = 50_000
    if case == "informational_only":
        rule = RuleNode("root", RuleNodeType.ALL_OF, "Unreviewed program", children=(
            RuleNode("note", RuleNodeType.UNRESOLVED, "Header only",
                     coverage_disposition=CoverageDisposition.INFORMATIONAL),
        ))
    elif case == "repeated_status":
        rule, attempts = exact, [{**attempts[0], "status": "repeated"}]
    elif case == "many_required":
        rule = RuleNode("program", RuleNodeType.ALL_OF, "25 requirements", children=tuple(
            RuleNode(f"req-{i}", RuleNodeType.EXACT_COURSE, code, course_codes=(code,))
            for i, code in enumerate(codes)
        ))
    elif case == "empty_all_of":
        attempts = []
    elif case == "five_credit_courses":
        rule = RuleNode("credits", RuleNodeType.CREDIT_THRESHOLD, "15 credits", min_credits=15)
    elif case == "zero_credit":
        rule, attempts = exact, [{**attempts[0], "attempted_credits": 0, "earned_credits": 0}]
    elif case == "repeated_course":
        rule = RuleNode("choose", RuleNodeType.CHOOSE_COUNT, "Two courses",
                        count=2, course_codes=tuple(codes[:2]))
        attempts = [attempts[0], {**attempts[0], "attempt_id": "repeat", "grade": "B"}]
    elif case == "nested_alternative":
        alternative = RuleNode("alt", RuleNodeType.ANY_OF, "Choose one", children=(
            RuleNode("option-a", RuleNodeType.EXACT_COURSE, "A", course_codes=(codes[0],)),
            RuleNode("option-b", RuleNodeType.EXACT_COURSE, "B", course_codes=(codes[1],)),
        ))
        rule = RuleNode("root", RuleNodeType.ALL_OF, "Program", children=(alternative, exact))
    elif case == "unused_unknown_alternative":
        rule = RuleNode("root", RuleNodeType.ANY_OF, "Alternative", children=(
            RuleNode("unknown", RuleNodeType.UNRESOLVED, "Unknown option",
                     unresolved_reason="Approval policy unknown"), exact,
        ))
    elif case == "search_limit":
        rule, limit = exact, 1
    result = DeterministicAuditEngine(rule, attempts, max_search_states=limit).run()
    assert result.overall_status == expected
    assert result.completed_credits == credits
    # Input ordering cannot choose a different allocation or different conclusion.
    reversed_result = DeterministicAuditEngine(
        rule, list(reversed(attempts)), max_search_states=limit,
    ).run()
    assert result == reversed_result
    if case == "unused_unknown_alternative":
        assert not result.blockers
    if case == "search_limit":
        assert result.results_summary["searchLimitReached"]


@pytest.fixture
def audit_test_data(db, collection):
    source_id = uuid4()
    db.execute(
        """
        INSERT INTO sources (source_id, collection_id, filename, content_type, file_hash, raw_path, status, index_revision, is_current)
        VALUES (%s, %s, 'cs_catalog.md', 'text/markdown', %s, '/tmp/cs.md', 'indexed', 1, true)
        """,
        (source_id, collection, "c" * 64),
    )
    prog_id = uuid4()
    db.execute(
        """
        INSERT INTO programs (
            program_id, collection_id, title, kind, catalog, source_url,
            first_page, last_page, parser_version, source_id, source_index_revision,
            source_sha256, origin_key, binding_status
        ) VALUES (
            %s, %s, 'CS Minor', 'minor', 'emory_college', 'https://college.emory.edu/cs-minor',
            1, 2, '1', %s, 1, %s, 'origin-cs-minor', 'bound'
        )
        """,
        (prog_id, collection, source_id, "c" * 64),
    )
    # Insert published rule release
    release_id = uuid4()
    rules_tree = {
        "nodeId": "root",
        "nodeType": "all_of",
        "title": "CS Minor Requirements",
        "coverageDisposition": "supported",
        "children": [
            {
                "nodeId": "m1",
                "nodeType": "exact_course",
                "title": "CS 170",
                "coverageDisposition": "supported",
                "courseCodes": ["CS 170"],
            },
            {
                "nodeId": "m2",
                "nodeType": "exact_course",
                "title": "CS 171",
                "coverageDisposition": "supported",
                "courseCodes": ["CS 171"],
            },
        ],
    }
    db.execute(
        """
        INSERT INTO program_rule_releases (
            release_id, program_id, collection_id, source_id, source_index_revision,
            source_sha256, release_version, status, rules_tree, rules_digest,
            coverage_summary
        ) VALUES (
            %s, %s, %s, %s, 1, %s, 1, 'published', %s, %s, '{}'::jsonb
        )
        """,
        (release_id, prog_id, collection, source_id, "c" * 64, Jsonb(rules_tree), "d" * 64),
    )
    db.execute(
        "UPDATE programs SET current_rule_release_id = %s WHERE program_id = %s",
        (release_id, prog_id),
    )
    db.commit()
    return prog_id, release_id


def test_degree_audit_service_lifecycle_and_staleness(db, collection, audit_test_data):
    prog_id, _release_id = audit_test_data
    student = _create_user(db, role="student")
    other_student = _create_user(db, role="student")

    # 1. Add attempt CS 170 to student record
    db.execute(
        """
        INSERT INTO student_academic_records (record_id, workspace_key, record_revision)
        VALUES (%s, %s, 1)
        """,
        (uuid4(), student.workspace_key),
    )
    rec = db.execute(
        "SELECT record_id FROM student_academic_records WHERE workspace_key = %s",
        (student.workspace_key,),
    ).fetchone()
    record_id = rec["record_id"]

    db.execute(
        """
        INSERT INTO academic_record_attempts (
            attempt_id, record_id, workspace_key, institution, submitted_course_code,
            term, attempted_credits, earned_credits, grade, status, provenance, is_verified
        ) VALUES (
            %s, %s, %s, 'Emory University', 'CS 170', 'Fall 2024', 4.0, 4.0, 'A', 'completed', 'student_manual', true
        )
        """,
        (uuid4(), record_id, student.workspace_key),
    )
    db.commit()

    # 2. Run audit: CS 170 satisfied, CS 171 outstanding -> overall status is outstanding
    audit_res = DegreeAuditService.run_audit(db, student.workspace_key, prog_id, student)
    audit = audit_res["audit"]
    audit_id = audit["audit_id"]
    assert audit["overall_status"] == "outstanding"
    assert audit["completed_credits"] == 4.0
    assert not audit["is_stale"]

    evals = audit_res["evaluations"]
    assert len(evals) >= 3

    # 3. Read audit
    read_res = DegreeAuditService.get_audit(db, student.workspace_key, audit_id, student)
    assert not read_res["audit"]["is_stale"]

    # 4. Cross-workspace isolation
    with pytest.raises(HTTPException) as exc_iso:
        DegreeAuditService.get_audit(db, student.workspace_key, audit_id, other_student)
    assert exc_iso.value.status_code == 403

    # 5. Staleness detection: Student adds new course attempt (bumps record revision)
    db.execute(
        "UPDATE student_academic_records SET record_revision = record_revision + 1 WHERE record_id = %s",
        (record_id,),
    )
    db.commit()

    # Re-reading marks audit stale!
    listed = DegreeAuditService.list_audits(db, student.workspace_key, student)
    assert next(a for a in listed if a["audit_id"] == audit_id)["is_stale"]
    stale_check = DegreeAuditService.get_audit(db, student.workspace_key, audit_id, student)
    assert stale_check["audit"]["is_stale"]


def test_degree_audit_http_routes(db, collection, audit_test_data):
    prog_id, _ = audit_test_data
    client = api_client(db, collection)

    # 1. Run audit via HTTP
    run_resp = client.post(
        "/api/audits/run",
        json={"programId": str(prog_id)},
    )
    assert run_resp.status_code == 200, run_resp.text
    data = run_resp.json()
    audit_id = data["audit"]["auditId"]
    assert data["programTitle"] == "CS Minor"
    assert data["audit"]["overallStatus"] in ("satisfied", "outstanding", "unresolved")

    # 2. Get audit via HTTP
    get_resp = client.get(f"/api/audits/{audit_id}")
    assert get_resp.status_code == 200, get_resp.text
    assert get_resp.json()["audit"]["auditId"] == audit_id

    # 3. List audits via HTTP
    list_resp = client.get("/api/audits")
    assert list_resp.status_code == 200, list_resp.text
    audits = list_resp.json()
    assert len(audits) >= 1
    saved = next(a for a in audits if a["auditId"] == audit_id)
    assert saved["isStale"] is False
    assert data["coverageScope"] == "program_audit"
    assert get_resp.json()["audit"]["isStale"] is False

    programs = client.get("/api/audits/programs")
    assert programs.status_code == 200, programs.text
    assert any(row["programId"] == str(prog_id) for row in programs.json())

    exported = client.get(f"/api/audits/{audit_id}/export")
    assert exported.status_code == 200, exported.text
    assert exported.json()["audit"]["isStale"] is False
    assert exported.json()["audit"]["overallStatus"] == data["audit"]["overallStatus"]
    assert exported.json()["coverageScope"] == "program_audit"

    # A newer source index leaves the saved release in place and marks the audit stale.
    db.execute(
        "UPDATE sources SET index_revision = index_revision + 1 WHERE filename = 'cs_catalog.md'",
    )
    db.commit()
    stale_list = next(
        a for a in client.get("/api/audits").json() if a["auditId"] == audit_id
    )
    stale_detail = client.get(f"/api/audits/{audit_id}").json()
    stale_export = client.get(f"/api/audits/{audit_id}/export").json()
    assert stale_list["isStale"] is True
    assert stale_detail["audit"]["isStale"] is True
    assert stale_export["audit"]["isStale"] is True
    assert stale_detail["audit"]["overallStatus"] == stale_export["audit"]["overallStatus"]
    assert stale_export["coverageScope"] == "program_audit"
    assert all(row["programId"] != str(prog_id) for row in client.get("/api/audits/programs").json())

