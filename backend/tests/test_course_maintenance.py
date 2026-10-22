"""Phase B: reviewed course maintenance publishes and preserves identity.

These exercise the service through the database it writes, not through mocks.
"""

import uuid

import pytest
from fastapi import HTTPException

from app.admin.course_maintenance import CourseMaintenanceService
from app.atlas.course_writer import replace_course_requisites
from app.atlas.importer import _write_course
from app.atlas.records import AtlasCourse
from app.atlas.requisites import parse_requisites

_CODES = ["MATH 111", "MATH 112", "MATH 200", "PHYS 100"]


@pytest.fixture
def atlas(db, collection):
    source_id = uuid.uuid4()
    db.execute(
        """
        INSERT INTO sources (source_id, collection_id, filename, content_type,
                             file_hash, raw_path, status, index_revision, is_current)
        VALUES (%s, %s, 'atlas.jsonl', 'application/x-course-atlas+jsonl',
                %s, '/tmp/atlas.jsonl', 'indexed', 1, true)
        """,
        (source_id, collection, "a" * 64),
    )
    locators = {}
    for ordinal, code in enumerate(_CODES):
        locator_id = uuid.uuid4()
        db.execute(
            """
            INSERT INTO locators (locator_id, source_id, ordinal, locator_type,
                                  label, start_char, end_char)
            VALUES (%s, %s, %s, 'course', %s, 0, 10)
            """,
            (locator_id, source_id, ordinal, code),
        )
        locators[code] = locator_id
    db.commit()
    return source_id, locators


def seed_course(
    db, collection, source_id, locator_id, code, title,
    requisites=None, authority="catalog", lifecycle="current", admin_revision=0,
):
    course_id = uuid.uuid5(collection, f"course:{code}")
    parsed = parse_requisites(requisites, course_code=code)
    db.execute(
        """
        INSERT INTO courses (course_id, collection_id, source_id, locator_id, code,
                             subject, title, requisites_raw, parse_status,
                             parser_version, canonical_code, lifecycle, authority,
                             admin_revision)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'test', %s, %s, %s, %s)
        """,
        (course_id, collection, source_id, locator_id, code, code.split()[0],
         title, requisites, parsed.status, code, lifecycle, authority, admin_revision),
    )
    replace_course_requisites(db, course_id, parsed)
    db.commit()
    return course_id


def resolve_all(db, collection):
    db.execute(
        """
        UPDATE prereq_mentions m SET target_course_id = c.course_id
        FROM courses c
        WHERE c.collection_id = %s AND c.code = m.target_code
        """,
        (collection,),
    )
    db.commit()


def mentions_for(db, course_id):
    return {
        row["target_code"]: row
        for row in db.execute(
            "SELECT target_code, target_course_id FROM prereq_mentions "
            "WHERE course_id = %s ORDER BY position",
            (course_id,),
        ).fetchall()
    }


def test_create_preview_then_publish_a_new_course(db, collection, atlas):
    _source_id, _locators = atlas
    change = CourseMaintenanceService.create_change(
        db,
        collection_id=collection,
        payload={
            "code": "phys 100",
            "changeKind": "create",
            "title": "Physics for Poets",
            "requisitesRaw": "MATH 111",
            "reason": "Adding a missing elective identity.",
            "expectedRevision": 0,
        },
        actor_user_id=None,
    )
    assert change["code"] == "PHYS 100"
    assert change["status"] == "draft"

    preview = CourseMaintenanceService.preview_change(db, change_id=change["change_id"])
    assert preview["changedFields"] == ["title", "requisitesRaw", "lifecycle", "authority"]
    assert preview["parsed"]["status"] == "parsed"
    assert preview["parsed"]["mentions"] == [
        {"kind": "prerequisite", "targetCode": "MATH 111", "resolved": False}
    ]
    assert any("admin_created" in warning for warning in preview["warnings"])

    published = CourseMaintenanceService.publish_change(
        db, change_id=change["change_id"], actor_user_id=None
    )
    assert published["change"]["status"] == "published"
    assert published["change"]["published_revision"] == 1

    course = db.execute(
        "SELECT title, authority, lifecycle, admin_revision, parse_status "
        "FROM courses WHERE collection_id = %s AND code = 'PHYS 100'",
        (collection,),
    ).fetchone()
    assert dict(course) == {
        "title": "Physics for Poets",
        "authority": "admin_created",
        "lifecycle": "current",
        "admin_revision": 1,
        "parse_status": "parsed",
    }


def test_update_replaces_edges_and_marks_admin_authority(db, collection, atlas):
    source_id, locators = atlas
    seed_course(db, collection, source_id, locators["MATH 111"], "MATH 111", "Calculus I")
    seed_course(db, collection, source_id, locators["MATH 112"], "MATH 112", "Calculus II")
    course_id = seed_course(
        db, collection, source_id, locators["MATH 200"], "MATH 200",
        "Calculus III", "MATH 111",
    )
    resolve_all(db, collection)

    change = CourseMaintenanceService.create_change(
        db,
        collection_id=collection,
        payload={
            "code": "MATH 200",
            "changeKind": "update",
            "title": "Multivariable Calculus",
            "requisitesRaw": "MATH 112",
            "reason": "Correcting the requirement per the department.",
            "expectedRevision": 0,
        },
        actor_user_id=None,
    )
    CourseMaintenanceService.publish_change(db, change_id=change["change_id"], actor_user_id=None)

    row = db.execute(
        "SELECT title, authority, admin_revision FROM courses WHERE course_id = %s",
        (course_id,),
    ).fetchone()
    assert row["title"] == "Multivariable Calculus"
    assert row["authority"] == "admin_correction"
    assert row["admin_revision"] == 1

    mentions = mentions_for(db, course_id)
    assert set(mentions) == {"MATH 112"}
    assert mentions["MATH 112"]["target_course_id"] is not None


def test_stale_revision_is_rejected_at_creation_and_publication(db, collection, atlas):
    source_id, locators = atlas
    seed_course(db, collection, source_id, locators["MATH 200"], "MATH 200", "Calculus III")
    change = CourseMaintenanceService.create_change(
        db,
        collection_id=collection,
        payload={
            "code": "MATH 200",
            "changeKind": "update",
            "title": "Corrected",
            "requisitesRaw": "",
            "reason": "First correction.",
            "expectedRevision": 0,
        },
        actor_user_id=None,
    )
    # A revision that is no longer current is refused before a new draft exists.
    with pytest.raises(HTTPException) as stale:
        CourseMaintenanceService.create_change(
            db,
            collection_id=collection,
            payload={
                "code": "MATH 200",
                "changeKind": "update",
                "title": "Second",
                "requisitesRaw": "",
                "reason": "Competing edit.",
                "expectedRevision": 0,
            },
            actor_user_id=None,
        )
    assert stale.value.status_code == 409

    # Publication re-checks the revision: an out-of-band advance makes the draft
    # stale rather than merging it.
    db.execute(
        "UPDATE courses SET admin_revision = 5 WHERE collection_id = %s AND code = 'MATH 200'",
        (collection,),
    )
    db.commit()
    with pytest.raises(HTTPException) as conflict:
        CourseMaintenanceService.publish_change(
            db, change_id=change["change_id"], actor_user_id=None
        )
    assert conflict.value.status_code == 409


def test_retire_unresolves_dependents_and_restore_resolves_them(db, collection, atlas):
    source_id, locators = atlas
    math111 = seed_course(db, collection, source_id, locators["MATH 111"], "MATH 111", "Calculus I")
    math112 = seed_course(
        db, collection, source_id, locators["MATH 112"], "MATH 112", "Calculus II", "MATH 111"
    )
    resolve_all(db, collection)
    assert mentions_for(db, math112)["MATH 111"]["target_course_id"] == math111

    retire = CourseMaintenanceService.create_change(
        db,
        collection_id=collection,
        payload={
            "code": "MATH 111",
            "changeKind": "retire",
            "reason": "Retiring the identity; historical citations must survive.",
            "expectedRevision": 0,
        },
        actor_user_id=None,
    )
    preview = CourseMaintenanceService.preview_change(db, change_id=retire["change_id"])
    assert any("unresolved" in warning for warning in preview["warnings"])
    CourseMaintenanceService.publish_change(db, change_id=retire["change_id"], actor_user_id=None)

    lifecycle = db.execute(
        "SELECT lifecycle FROM courses WHERE course_id = %s", (math111,)
    ).fetchone()["lifecycle"]
    assert lifecycle == "retired"
    # Retirement never erases the dependent edge; it becomes explicitly unresolved.
    assert mentions_for(db, math112)["MATH 111"]["target_course_id"] is None

    restore = CourseMaintenanceService.create_change(
        db,
        collection_id=collection,
        payload={
            "code": "MATH 111",
            "changeKind": "restore",
            "reason": "The course returns.",
            "expectedRevision": 1,
        },
        actor_user_id=None,
    )
    CourseMaintenanceService.publish_change(db, change_id=restore["change_id"], actor_user_id=None)
    assert mentions_for(db, math112)["MATH 111"]["target_course_id"] == math111


def test_baseline_import_preserves_correction_and_queues_conflict(db, collection, atlas):
    source_id, locators = atlas
    course_id = seed_course(
        db, collection, source_id, locators["MATH 200"], "MATH 200", "Calculus III", "MATH 111"
    )
    change = CourseMaintenanceService.create_change(
        db,
        collection_id=collection,
        payload={
            "code": "MATH 200",
            "changeKind": "update",
            "title": "Corrected Title",
            "requisitesRaw": "MATH 111",
            "reason": "Reviewed correction.",
            "expectedRevision": 0,
        },
        actor_user_id=None,
    )
    CourseMaintenanceService.publish_change(db, change_id=change["change_id"], actor_user_id=None)

    wrote = _write_course(
        db, collection, source_id, locators["MATH 200"],
        AtlasCourse(code="MATH 200", title="New Baseline Title", requisites="MATH 112"),
        "MATH 200",
    )
    db.commit()
    assert wrote is False
    assert db.execute(
        "SELECT title, authority FROM courses WHERE course_id = %s", (course_id,)
    ).fetchone()["title"] == "Corrected Title"
    conflicts = CourseMaintenanceService.list_conflicts(db, collection_id=collection)
    assert [row["code"] for row in conflicts] == ["MATH 200"]

    # A baseline identical to the correction's base is not a conflict.
    wrote_again = _write_course(
        db, collection, source_id, locators["MATH 200"],
        AtlasCourse(code="MATH 200", title="Calculus III", requisites="MATH 111"),
        "MATH 200",
    )
    db.commit()
    assert wrote_again is False


def test_unparsed_requisites_never_invent_edges(db, collection, atlas):
    source_id, locators = atlas
    course_id = seed_course(
        db, collection, source_id, locators["MATH 200"], "MATH 200", "Calculus III", "MATH 111"
    )
    change = CourseMaintenanceService.create_change(
        db,
        collection_id=collection,
        payload={
            "code": "MATH 200",
            "changeKind": "update",
            "title": "Calculus III",
            "requisitesRaw": "Prerequisite: approval of adviser.",
            "reason": "Catalog wording is prose only.",
            "expectedRevision": 0,
        },
        actor_user_id=None,
    )
    preview = CourseMaintenanceService.preview_change(db, change_id=change["change_id"])
    assert preview["parsed"]["status"] == "unparsed"
    assert preview["parsed"]["mentions"] == []
    assert any("without invented edges" in warning for warning in preview["warnings"])

    CourseMaintenanceService.publish_change(db, change_id=change["change_id"], actor_user_id=None)
    assert mentions_for(db, course_id) == {}
    assert db.execute(
        "SELECT count(*) AS n FROM prereq_nodes WHERE course_id = %s", (course_id,)
    ).fetchone()["n"] == 0


def test_course_maintenance_publishes_through_the_admin_api(db, collection, atlas):
    from tests.helpers import api_client

    client = api_client(db, collection)
    created = client.post(
        f"/api/admin/course-changes?collection_id={collection}",
        json={
            "code": "PHYS 100",
            "changeKind": "create",
            "title": "Physics for Poets",
            "requisitesRaw": "MATH 111",
            "reason": "Adding a reviewed elective identity.",
            "expectedRevision": 0,
        },
    )
    assert created.status_code == 201, created.text
    change_id = created.json()["changeId"]

    preview = client.get(f"/api/admin/course-changes/{change_id}/preview")
    assert preview.status_code == 200, preview.text
    assert preview.json()["change"]["status"] == "draft"
    assert preview.json()["parsed"]["unresolved"] == ["MATH 111"]

    published = client.post(f"/api/admin/course-changes/{change_id}/publish")
    assert published.status_code == 200, published.text
    assert published.json()["change"]["status"] == "published"

    detail = client.get(f"/api/admin/courses/PHYS%20100?collection_id={collection}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["course"]["authority"] == "admin_created"

    listed = client.get(f"/api/admin/courses?collection_id={collection}&query=physics&campus=atlanta")
    assert listed.status_code == 200, listed.text
    assert [row["code"] for row in listed.json()["courses"]] == ["PHYS 100"]
