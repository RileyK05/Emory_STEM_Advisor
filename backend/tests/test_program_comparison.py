"""Program comparisons keep overlays and mention overlap off the academic record."""

from uuid import uuid4

import pytest
from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app.advising.comparison import MENTION_OVERLAP_ONLY
from app.advising.comparison_service import ProgramComparisonService
from app.db import queries
from tests.helpers import api_client
from tests.test_degree_audit import _create_user


def _program(db, collection, source_id, title, rules_tree):
    program_id = uuid4()
    release_id = uuid4()
    db.execute(
        """
        INSERT INTO programs (
            program_id, collection_id, title, kind, catalog, source_url,
            first_page, last_page, parser_version, source_id, source_index_revision,
            source_sha256, origin_key, binding_status
        ) VALUES (
            %s, %s, %s, 'minor', 'emory_college', 'https://college.emory.edu/program',
            1, 2, '1', %s, 1, %s, %s, 'bound'
        )
        """,
        (program_id, collection, title, source_id, "c" * 64, f"origin-{program_id}"),
    )
    db.execute(
        queries.get("insert_program_item"),
        {"program_id": program_id, "position": 1, "section": "Core", "text": "CS 170", "choose": None},
    )
    db.execute(
        queries.get("insert_program_course_mention"),
        {"program_id": program_id, "position": 1, "target_code": "CS 170"},
    )
    db.execute(
        """
        INSERT INTO program_rule_releases (
            release_id, program_id, collection_id, source_id, source_index_revision,
            source_sha256, release_version, status, rules_tree, rules_digest, coverage_summary
        ) VALUES (%s, %s, %s, %s, 1, %s, 1, 'published', %s, %s, '{}'::jsonb)
        """,
        (release_id, program_id, collection, source_id, "c" * 64, Jsonb(rules_tree), "d" * 64),
    )
    db.execute(
        "UPDATE programs SET current_rule_release_id = %s WHERE program_id = %s",
        (release_id, program_id),
    )
    return program_id


@pytest.fixture
def comparison_programs(db, collection):
    source_id = uuid4()
    db.execute(
        """
        INSERT INTO sources (
            source_id, collection_id, filename, content_type, file_hash, raw_path,
            status, index_revision, is_current
        ) VALUES (%s, %s, 'compare.md', 'text/markdown', %s, '/tmp/compare.md', 'indexed', 1, true)
        """,
        (source_id, collection, "c" * 64),
    )
    exact = {
        "nodeId": "root",
        "nodeType": "exact_course",
        "title": "CS 170",
        "coverageDisposition": "supported",
        "courseCodes": ["CS 170"],
    }
    unresolved = {
        "nodeId": "root",
        "nodeType": "all_of",
        "title": "Requirements",
        "coverageDisposition": "supported",
        "children": [
            exact | {"nodeId": "course"},
            {
                "nodeId": "policy",
                "nodeType": "unresolved",
                "title": "Ambiguous elective",
                "coverageDisposition": "unresolved",
                "unresolvedReason": "Sharing policy is not evidenced",
            },
        ],
    }
    return (
        _program(db, collection, source_id, "Exact Minor", exact),
        _program(db, collection, source_id, "Open Minor", unresolved),
    )


def test_comparison_overlay_is_not_saved_credit(db, collection, comparison_programs):
    exact_id, open_id = comparison_programs
    student = _create_user(db, role="student")
    other = _create_user(db, role="student")
    record_id = uuid4()
    db.execute(
        """
        INSERT INTO student_academic_records (record_id, workspace_key, record_revision)
        VALUES (%s, %s, 1)
        """,
        (record_id, student.workspace_key),
    )
    db.execute(
        """
        INSERT INTO academic_record_attempts (
            attempt_id, record_id, workspace_key, institution, submitted_course_code,
            term, attempted_credits, earned_credits, grade, status, provenance, is_verified
        ) VALUES (
            %s, %s, %s, 'Emory University', 'CS 170', 'Fall 2024', 3, 3, 'A',
            'completed', 'student_manual', true
        )
        """,
        (uuid4(), record_id, student.workspace_key),
    )
    db.commit()

    created = ProgramComparisonService.create(
        db, student.workspace_key, [exact_id, open_id], ["CHEM 150"], [], student,
    )
    repeated = ProgramComparisonService.create(
        db, student.workspace_key, [open_id, exact_id], ["chem 150"], [], student,
    )
    assert repeated["comparison_id"] == created["comparison_id"]
    assert created["sharing_policy"] == "unknown"
    assert created["mention_kind"] == MENTION_OVERLAP_ONLY
    assert created["shared_mentions"] == ["CS 170"]
    assert created["credit_savings"] is None
    assert created["coverage_comparable"] is False
    assert created["ranking_blocked_reason"] == "unequal unresolved coverage"
    assert "easier" not in created

    attempts = db.execute(
        """
        SELECT submitted_course_code, provenance FROM academic_record_attempts
        WHERE workspace_key = %s ORDER BY submitted_course_code
        """,
        (student.workspace_key,),
    ).fetchall()
    assert [row["submitted_course_code"] for row in attempts] == ["CS 170"]
    assert all(row["provenance"] == "student_manual" for row in attempts)
    audits_after_repeat = db.execute(
        "SELECT count(*) AS n FROM degree_audits WHERE workspace_key = %s",
        (student.workspace_key,),
    ).fetchone()["n"]
    assert audits_after_repeat == 2

    with pytest.raises(HTTPException) as denied:
        ProgramComparisonService.get(db, student.workspace_key, created["comparison_id"], other)
    assert denied.value.status_code == 403

    client = api_client(db, collection)
    response = client.post(
        "/api/comparisons",
        json={"programIds": [str(exact_id), str(open_id)], "addCourseCodes": ["CHEM 150"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["creditSavings"] is None
    assert body["sharingPolicy"] == "unknown"
    assert body["mentionKind"] == MENTION_OVERLAP_ONLY
    assert body["sharedMentions"] == ["CS 170"]
    assert body["rankingBlockedReason"] == "unequal unresolved coverage"
    assert "easierProgramId" not in body
    listed = client.get("/api/comparisons")
    assert listed.status_code == 200, listed.text
    assert listed.json()[0]["creditSavings"] is None
