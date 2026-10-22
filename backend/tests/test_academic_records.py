"""Tests for academic record import, normalization, verification, and service operations (Item 1)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.admin.store import AdminStore
from app.auth.models import Principal
from app.db import queries
from app.records.importer import (
    infer_status_and_credits,
    normalize_course_code,
    normalize_term,
    parse_csv_content,
)
from app.records.service import AcademicRecordService
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


def test_normalizer_unit_rules():
    # Course codes
    assert normalize_course_code("cs170") == "CS 170"
    assert normalize_course_code("MATH-111") == "MATH 111"
    assert normalize_course_code("  biol_141  ") == "BIOL 141"
    assert normalize_course_code("CHEM 150L") == "CHEM 150L"
    assert normalize_course_code("CS_OX170") == "CS_OX 170"

    # Terms
    assert normalize_term("FA24") == "Fall 2024"
    assert normalize_term("Fall 2024") == "Fall 2024"
    assert normalize_term("sp25") == "Spring 2025"
    assert normalize_term("Summer 2023") == "Summer 2023"

    # Status and credits inference
    status, credits = infer_status_and_credits("A", 4.0)
    assert status == "completed" and credits == 4.0

    status, credits = infer_status_and_credits("B-", 3.0)
    assert status == "completed" and credits == 3.0

    status, credits = infer_status_and_credits("F", 3.0)
    assert status == "failed" and credits == 0.0

    status, credits = infer_status_and_credits("W", 3.0)
    assert status == "withdrawn" and credits == 0.0

    status, credits = infer_status_and_credits("IP", 3.0)
    assert status == "in_progress" and credits == 0.0

    status, credits = infer_status_and_credits("TR", 3.0)
    assert status == "transfer" and credits == 3.0

    status, credits = infer_status_and_credits("AP", 4.0)
    assert status == "test_credit" and credits == 4.0

    # An explicitly reported earned value, including zero, is not attempted credit.
    for grade in ("A", "TR", "AP", None):
        status, credits = infer_status_and_credits(grade, 4.0, explicit_earned_credits=0.0)
        assert credits == 0.0


@pytest.mark.parametrize(("raw", "expected"), [
    ("ARCH16", "ARCH 16"),
    ("PAE8100", "PAE 8100"),
    ("BIOL_OX385RWE", "BIOL_OX 385RWE"),
    ("TATT605BSHES", "TATT 605BSHES"),
    ("VM999D1", "VM 999D1"),
    ("M2M 170", "M2M 170"),
    ("CS-101", "CS 101"),
    ("CS_101", "CS 101"),
])
def test_transcript_codes_follow_atlas_shapes_without_collapsing_oxford(raw, expected):
    assert normalize_course_code(raw) == expected
    assert normalize_course_code("CS_OX170") != normalize_course_code("CS170")


@pytest.mark.parametrize("field,value", [
    ("Credits", "NaN"), ("Credits", "inf"),
    ("Earned", "NaN"), ("Earned", "inf"), ("Earned", "-1"),
])
def test_csv_rejects_invalid_credit_values(field, value):
    csv_text = f"Course,Term,{field}\nCS 170,Fall 2024,{value}\n"
    parsed, errors, _ = parse_csv_content(csv_text)
    assert parsed == []
    assert len(errors) == 1


def test_csv_parser_and_duplicate_detection():
    csv_text = """Course,Term,Credits,Grade,Title
CS 170,Fall 2023,4.0,A,Intro to Computer Science
MATH 111,Fall 2023,3.0,B+,Calculus I
CS 170,Spring 2024,4.0,A-,Intro to CS Repeat
INVALID_ROW_WITHOUT_TERM,
CS 224,Fall 2023,-1.0,A,Bad Credits
"""
    existing_attempts = [
        {"submitted_course_code": "CS 170", "term": "Fall 2023"}
    ]

    parsed, errors, duplicates = parse_csv_content(csv_text, existing_attempts)

    # CS 170 in Fall 2023 is duplicate of existing attempt
    assert len(duplicates) == 1
    assert duplicates[0]["courseCode"] == "CS 170"
    assert duplicates[0]["reason"] == "existing_attempt_in_record"

    # Errors caught: missing term on row 4, negative credits on row 5
    assert len(errors) == 2
    assert "Missing term" in errors[0]["error"]
    assert "Invalid credits" in errors[1]["error"]

    # Valid rows parsed: CS 170 (Spring 2024), MATH 111 (Fall 2023), and the duplicate CS 170
    assert len(parsed) == 3
    assert parsed[0]["submittedCourseCode"] == "CS 170"
    assert parsed[1]["submittedCourseCode"] == "MATH 111"
    assert parsed[2]["submittedCourseCode"] == "CS 170"


def test_import_preview_defaults_duplicate_rows_to_reject(db):
    student = _create_user(db, role="student")
    record = AcademicRecordService.get_or_create_record(db, student.workspace_key)
    csv_data = """Course,Term,Credits,Grade,Title
CS 170,Fall 2024,4.0,A,Intro to CS
CS 170,Fall 2024,4.0,A,Duplicate row
MATH 111,Fall 2024,3.0,B,Calculus I
"""
    preview = AcademicRecordService.create_import_preview(
        db, student.workspace_key, "duplicates.csv", csv_data,
        record["record_revision"], student,
    )
    assert len(preview["parsed_rows"]) == 3
    assert preview["row_decisions"][0] == "select"
    assert preview["row_decisions"][1] == "reject"
    assert len(preview["duplicate_candidates"]) == 1


def test_academic_record_service_lifecycle(db):
    student = _create_user(db, role="student")
    advisor = _create_user(db, role="advisor")
    other_student = _create_user(db, role="student")

    # 1. Initial record creation
    rec = AcademicRecordService.get_or_create_record(db, student.workspace_key)
    assert rec["record_revision"] == 1

    # 2. Insert attempt
    att1 = AcademicRecordService.upsert_attempt(
        db,
        student.workspace_key,
        {
            "submittedCourseCode": "CS 170",
            "resolvedCourseCode": "cs171",
            "term": "Fall 2023",
            "attemptedCredits": 4.0,
            "grade": "A",
            "courseTitle": "Intro to CS",
            "provenance": "institutional_sync",
        },
        student,
    )
    assert att1["submitted_course_code"] == "CS 170"
    assert att1["provenance"] == "student_manual"
    assert att1["status"] == "completed"
    assert float(att1["earned_credits"]) == 4.0
    assert not att1["is_verified"]

    # 3. Staff verification
    with pytest.raises(HTTPException) as exc_info:
        # Student cannot verify
        AcademicRecordService.verify_attempt(
            db,
            student.workspace_key,
            att1["attempt_id"],
            "manual_staff",
            "Checked unofficial transcript",
            student,
        )
    assert exc_info.value.status_code == 403

    # Verification authority does not grant access to an arbitrary student's record.
    with pytest.raises(HTTPException) as no_grant:
        AcademicRecordService.verify_attempt(
            db, student.workspace_key, att1["attempt_id"], "manual_staff", "Checked", advisor,
        )
    assert no_grant.value.status_code == 403
    admin = _create_user(db, role="admin")
    grant = AdminStore(db, admin).create_grant(
        grant_type="advisor_access", actor_user_id=advisor.user_id,
        target_workspace_key=student.workspace_key, resource_type="all_records",
        allowed_actions=["read", "verify"], reason="Review official academic attempts",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db.commit()
    verified_att = AcademicRecordService.verify_attempt(
        db,
        student.workspace_key,
        att1["attempt_id"],
        "manual_staff",
        "Official transcript checked",
        advisor,
    )
    assert verified_att["is_verified"]
    assert verified_att["verification_method"] == "manual_staff"
    assert verified_att["verified_by_user_id"] == advisor.user_id
    verified_revision = AcademicRecordService.get_revision(db, student.workspace_key, 3, student)
    assert verified_revision["attempts"][0]["resolvedCourseCode"] == "CS 171"
    assert verified_revision["attempts"][0]["verificationMethod"] == "manual_staff"
    AdminStore(db, admin).revoke_grant(grant["grant_id"])
    db.commit()
    with pytest.raises(HTTPException) as revoked:
        AcademicRecordService.verify_attempt(
            db, student.workspace_key, att1["attempt_id"], "manual_staff", "Checked", advisor,
        )
    assert revoked.value.status_code == 403

    # 4. Editing a verified attempt invalidates verification!
    edited_att = AcademicRecordService.upsert_attempt(
        db,
        student.workspace_key,
        {
            "attemptId": str(att1["attempt_id"]),
            "submittedCourseCode": "CS 170",
            "term": "Fall 2023",
            "attemptedCredits": 4.0,
            "grade": "A-",
        },
        student,
    )
    assert not edited_att["is_verified"]
    assert edited_att["verification_method"] is None
    assert edited_att["grade"] == "A-"
    prior = AcademicRecordService.get_revision(db, student.workspace_key, 2, student)
    assert prior["attempts"][0]["grade"] == "A"
    assert prior["attempts"][0]["provenance"] == "student_manual"

    # Check verification event history
    full_data = AcademicRecordService.get_record(db, student.workspace_key, student)
    event_types = [e["event_type"] for e in full_data["recentEvents"]]
    assert "verification_invalidated" in event_types
    assert "attempt_verified" in event_types
    assert "attempt_updated" in event_types

    # 5. Cross-workspace isolation
    with pytest.raises(HTTPException) as exc_iso:
        AcademicRecordService.get_record(db, student.workspace_key, other_student)
    assert exc_iso.value.status_code == 403

    # 6. Delete attempt
    AcademicRecordService.delete_attempt(db, student.workspace_key, att1["attempt_id"], student)
    after_del = AcademicRecordService.get_record(db, student.workspace_key, student)
    assert len(after_del["attempts"]) == 0


def test_import_preview_and_commit_concurrency(db):
    student = _create_user(db, role="student")
    rec = AcademicRecordService.get_or_create_record(db, student.workspace_key)
    initial_rev = rec["record_revision"]

    csv_data = """Course,Term,Credits,Grade,Title
CS 170,Fall 2023,4.0,A,Intro to CS
MATH 111,Fall 2023,3.0,B+,Calculus I
"""

    preview = AcademicRecordService.create_import_preview(
        db,
        student.workspace_key,
        "transcript.csv",
        csv_data,
        initial_rev,
        student,
    )
    assert preview["status"] == "preview"
    assert len(preview["parsed_rows"]) == 2
    assert len(preview["preview_digest"]) == 64

    kept = AcademicRecordService.update_import_preview(
        db, student.workspace_key, preview["batch_id"], initial_rev,
        ["select", "reject"], None, student,
    )
    with pytest.raises(HTTPException) as stale_edit:
        AcademicRecordService.update_import_preview(
            db, student.workspace_key, preview["batch_id"], initial_rev + 9,
            None, [{"rowIndex": 0, "grade": "C"}], student,
        )
    assert stale_edit.value.status_code == 409
    unchanged = db.execute(
        "SELECT parsed_rows FROM academic_record_import_batches WHERE batch_id = %s",
        (preview["batch_id"],),
    ).fetchone()
    assert unchanged["parsed_rows"][0]["grade"] == "A"

    # Committing with wrong revision fails with 409 Conflict
    with pytest.raises(HTTPException) as exc_conflict:
        AcademicRecordService.commit_import_batch(
            db,
            student.workspace_key,
            preview["batch_id"],
            initial_rev + 999,
            kept["preview_digest"],
            student,
        )
    assert exc_conflict.value.status_code == 409

    # Committing with matching revision succeeds
    res = AcademicRecordService.commit_import_batch(
        db,
        student.workspace_key,
        preview["batch_id"],
        initial_rev,
        kept["preview_digest"],
        student,
    )
    assert res["status"] == "committed"
    assert res["attemptsImported"] == 1
    assert res["newRecordRevision"] == initial_rev + 1

    # Retrying the same reviewed digest does not insert another attempt.
    again = AcademicRecordService.commit_import_batch(
        db,
        student.workspace_key,
        preview["batch_id"],
        res["newRecordRevision"],
        kept["preview_digest"],
        student,
    )
    assert again["attemptsImported"] == 1

    record_data = AcademicRecordService.get_record(db, student.workspace_key, student)
    assert len(record_data["attempts"]) == 1
    assert record_data["attempts"][0]["submitted_course_code"] == "CS 170"
    assert record_data["attempts"][0]["provenance"] == "csv_import"


@pytest.mark.parametrize("correction,expected", [
    ({"submittedCourseCode": "math111"}, ("MATH 111", "completed", 4.0)),
    ({"grade": "W"}, ("CS 170", "withdrawn", 0.0)),
    ({"attemptedCredits": -1.0}, None),
    ({"earnedCredits": float("inf")}, None),
])
def test_corrected_import_rows_are_validated_before_commit(db, correction, expected):
    student = _create_user(db)
    record = AcademicRecordService.get_or_create_record(db, student.workspace_key)
    revision = record["record_revision"]
    preview = AcademicRecordService.create_import_preview(
        db, student.workspace_key, "courses.csv",
        "Course,Term,Credits,Grade\nCS 170,Fall 2024,4,A\n", revision, student,
    )
    if expected is None:
        with pytest.raises(HTTPException) as error:
            AcademicRecordService.update_import_preview(
                db, student.workspace_key, preview["batch_id"], revision,
                None, [{"rowIndex": 0, **correction}], student,
            )
        assert error.value.status_code == 422
        return
    reviewed = AcademicRecordService.update_import_preview(
        db, student.workspace_key, preview["batch_id"], revision,
        None, [{"rowIndex": 0, **correction}], student,
    )
    AcademicRecordService.commit_import_batch(
        db, student.workspace_key, preview["batch_id"], revision,
        reviewed["preview_digest"], student,
    )
    saved = AcademicRecordService.get_record(db, student.workspace_key, student)["attempts"][0]
    assert saved["resolved_course_code"] == expected[0]
    assert saved["submitted_course_code"] == expected[0]
    assert saved["status"] == expected[1]
    assert float(saved["earned_credits"]) == expected[2]


def test_academic_record_http_routes(db, collection, migrated_dsn):
    from app.api import deps
    from app.db.connection import connect

    client = api_client(db, collection)

    def fresh_connection():
        # A request closes its connection just like production, exposing missing commits.
        conn = connect(migrated_dsn)
        try:
            yield conn
        finally:
            conn.close()

    client.app.dependency_overrides[deps.get_conn] = fresh_connection

    # 1. Get record
    get_resp = client.get("/api/academic-record")
    assert get_resp.status_code == 200, get_resp.text
    initial = get_resp.json()
    assert initial["record"]["recordRevision"] >= 1
    rev = initial["record"]["recordRevision"]

    # 2. Upsert attempt via HTTP
    upsert_resp = client.post(
        "/api/academic-record/attempts",
        json={
            "submittedCourseCode": "PHYS 151",
            "term": "Fall 2024",
            "attemptedCredits": 4.0,
            "grade": "A",
            "courseTitle": "Physics I",
        },
    )
    assert upsert_resp.status_code == 200, upsert_resp.text
    att = upsert_resp.json()
    assert att["submittedCourseCode"] == "PHYS 151"
    att_id = att["attemptId"]

    # 3. Import preview via HTTP
    preview_resp = client.post(
        "/api/academic-record/imports/preview",
        json={
            "filename": "courses.csv",
            "rawContent": "Course,Term,Credits,Grade\nCHEM 150,Spring 2024,3.0,B\n",
            "expectedRecordRevision": rev + 1,
        },
    )
    assert preview_resp.status_code == 200, preview_resp.text
    batch = preview_resp.json()
    batch_id = batch["batchId"]

    # 4. Import commit via HTTP
    commit_resp = client.post(
        f"/api/academic-record/imports/{batch_id}/commit",
        json={"expectedRecordRevision": rev + 1, "previewDigest": batch["previewDigest"]},
    )
    assert commit_resp.status_code == 200, commit_resp.text
    assert commit_resp.json()["attemptsImported"] == 1

    # 5. Delete attempt via HTTP
    del_resp = client.delete(f"/api/academic-record/attempts/{att_id}")
    assert del_resp.status_code == 204
