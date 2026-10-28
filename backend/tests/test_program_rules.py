import hashlib
import json
from uuid import uuid4

import pytest

from app.db import queries
from app.programs.rules import RuleNode, RuleValidationError, validate_rule_structure
from tests.helpers import api_client


@pytest.mark.parametrize("changes", [
    {"nodeId": ""},
    {"nodeType": "exact_course", "courseCodes": []},
    {"nodeType": "choose_count", "courseCodes": ["CS 170", "CS 170"], "count": 2},
    {"nodeType": "all_of", "courseCodes": []},
    {"nodeType": "credit_threshold", "minCredits": float("nan")},
    {"coverageDisposition": "waived"},
    {"minCredits": 6},
    {"count": 2},
])
def test_executable_rules_reject_ambiguous_shapes(changes):
    data = {"nodeId": "rule", "nodeType": "exact_course", "title": "Requirement",
            "courseCodes": ["CS 170"], **changes}
    with pytest.raises(RuleValidationError):
        validate_rule_structure(RuleNode.from_dict(data))


@pytest.mark.parametrize("code,expected", [
    ("ARCH16", "ARCH 16"),
    ("PAE8100", "PAE 8100"),
    ("BIOL_OX385RWE", "BIOL_OX 385RWE"),
    ("TATT605BSHES", "TATT 605BSHES"),
    ("VM999D1", "VM 999D1"),
    ("M2M 170", "M2M 170"),
])
def test_rule_course_codes_accept_atlas_shapes_without_equivalence(code, expected):
    node = RuleNode.from_dict({
        "nodeId": "one", "nodeType": "exact_course", "title": "Course",
        "courseCodes": [code],
    })
    assert node.course_codes == (expected,)
    assert RuleNode.from_dict({
        "nodeId": "ox", "nodeType": "exact_course", "title": "Course",
        "courseCodes": ["CS_OX170"],
    }).course_codes == ("CS_OX 170",)
    assert node.course_codes != ("CS 170",)


@pytest.fixture
def bound_program(db, collection, tmp_path):
    from app.programs.importer import import_programs, program_id
    from app.programs.requirements import parse_program, to_record
    from tests.test_programs import _ingest_catalog_file

    title = "Computer Science BA"
    source_text = (
        f"{title}\nDEGREE AWARDED BA\nHOURS TO COMPLETE 56\n"
        "COURSES REQUIRED 18\nDEPARTMENT CONTACT Computer Science\n"
        "Take CS 170 Intro to CS\n"
        "Choose 2 courses from CS 255, CS 326, CS 370\n"
        "A grade of C or better is required in all major courses\n"
    )
    identity = _ingest_catalog_file(db, collection, tmp_path, title, source_text,
                                    name="cs_catalog.md")
    record = to_record(parse_program(title, source_text), catalog="emory_college",
                       source_url="https://college.emory.edu/cs-ba", first_page=10,
                       last_page=12)
    record["source"] = identity
    path = tmp_path / "programs.jsonl"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    import_programs(db, collection_id=collection, path=path)
    source = db.execute(queries.get("resolve_program_source"), {
        "collection_id": collection, "origin_key": identity["origin_key"],
        "file_hash": identity["content_sha256"],
    }).fetchone()
    return program_id(collection, title), source["source_id"]


def test_get_program_inspection(db, collection, bound_program):
    client = api_client(db, collection)
    prog_id, _ = bound_program

    resp = client.get(f"/api/admin/programs/{prog_id}/inspection")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["program"]["title"] == "Computer Science BA"
    assert data["program"]["bindingStatus"] == "bound"
    assert len(data["items"]) == 3
    assert data["items"][0]["mentions"] == ["CS 170"]
    assert data["items"][1]["choose"] == 2
    assert len(data["items"][1]["mentions"]) == 3


def test_create_rule_draft_auto_and_validation(db, collection, bound_program):
    client = api_client(db, collection)
    prog_id, _ = bound_program

    # 1. Auto-generate draft
    resp = client.post(
        f"/api/admin/programs/{prog_id}/rules/draft",
        json={"notes": "Initial auto-generated draft"},
    )
    assert resp.status_code == 201, resp.text
    draft = resp.json()
    assert draft["releaseVersion"] == 1
    assert draft["status"] == "draft"
    assert len(draft["rulesDigest"]) == 64
    assert draft["coverageSummary"]["totalNodes"] >= 4

    # 2. Reject custom draft that omits a raw item position
    invalid_tree = {
        "nodeId": "root",
        "nodeType": "all_of",
        "title": "Incomplete Rules",
        "coverageDisposition": "supported",
        "children": [
            {
                "nodeId": "c1",
                "nodeType": "exact_course",
                "title": "CS 170",
                "rawPosition": 0,
                "coverageDisposition": "supported",
                "courseCodes": ["CS 170"],
            }
            # Missing positions 1 and 2!
        ],
    }
    resp_invalid = client.post(
        f"/api/admin/programs/{prog_id}/rules/draft",
        json={"rulesTree": invalid_tree},
    )
    assert resp_invalid.status_code == 422
    assert "Missing raw positions" in resp_invalid.text


def test_rule_identity_evidence_can_span_adjacent_chunks(db, collection, bound_program):
    from app.programs.review_service import ProgramReviewService

    prog_id, source_id = bound_program
    evidence = db.execute(queries.get("list_program_identity_evidence"), {
        "program_id": prog_id,
    }).fetchall()
    next_chunk_index = db.execute(
        "SELECT COALESCE(MAX(chunk_index), -1) + 1 AS next_index FROM chunks WHERE source_id = %s",
        (source_id,),
    ).fetchone()["next_index"]

    for field in ("title", "degree"):
        row = next(item for item in evidence if item["field"] == field)
        start, end = row["span_start"], row["span_end"]
        span = row["chunk_text"][start:end]
        split = span.index(" ") + 1
        prefix, suffix = span[:split], span[split:]
        chunk_id = uuid4()
        chunk_start = row["chunk_start_char"] + start + split
        db.execute(
            "UPDATE program_field_evidence SET span_end = %s, text_digest = %s "
            "WHERE program_id = %s AND field = %s AND ordinal = %s",
            (start + split, hashlib.sha256(prefix.encode()).hexdigest(),
             prog_id, field, row["ordinal"]),
        )
        db.execute(
            "INSERT INTO chunks (chunk_id, source_id, locator_id, chunk_index, start_char, "
            "end_char, token_count, text) VALUES (%s, %s, %s, %s, %s, %s, 1, %s)",
            (chunk_id, source_id, row["locator_id"], next_chunk_index,
             chunk_start, chunk_start + len(suffix), suffix),
        )
        db.execute(
            "INSERT INTO program_field_evidence (program_id, field, ordinal, chunk_id, "
            "locator_id, source_id, source_index_revision, span_start, span_end, text_digest) "
            "VALUES (%s, %s, 1, %s, %s, %s, %s, 0, %s, %s)",
            (prog_id, field, chunk_id, row["locator_id"], source_id,
             row["source_index_revision"], len(suffix),
             hashlib.sha256(suffix.encode()).hexdigest()),
        )
        next_chunk_index += 1

    release = ProgramReviewService.create_rule_draft(db, prog_id, None)
    assert release["status"] == "draft"


@pytest.mark.parametrize("mutation", ["superseded", "reindexed", "parsed", "metadata"])
def test_stale_evidence_prevents_release(db, collection, bound_program, mutation):
    client = api_client(db, collection)
    prog_id, source_id = bound_program

    # Create draft while source is current
    draft = client.post(
        f"/api/admin/programs/{prog_id}/rules/draft",
        json={"notes": "Draft 1"},
    ).json()
    release_id = draft["releaseId"]
    assert client.post(
        f"/api/admin/programs/{prog_id}/rules/{release_id}/approve", json={},
    ).status_code == 200

    # Mark source is_current = false
    if mutation == "superseded":
        db.execute("UPDATE sources SET is_current = false WHERE source_id = %s", (source_id,))
    elif mutation == "reindexed":
        db.execute("UPDATE sources SET index_revision = index_revision + 1 WHERE source_id = %s", (source_id,))
    elif mutation == "parsed":
        db.execute("UPDATE programs SET parsed_content_digest = %s WHERE program_id = %s", ("e" * 64, prog_id))
    else:
        db.execute("INSERT INTO source_provenance (source_id, source_category, classification_origin, content_hash) "
                   "VALUES (%s, 'institutional_catalog', 'verified_import', %s)", (source_id, "b" * 64))
    db.commit()

    # Attempting to publish must be rejected with 409
    pub_resp = client.post(
        f"/api/admin/programs/{prog_id}/rules/{release_id}/publish",
        json={"notes": "Publishing with stale evidence"},
    )
    assert pub_resp.status_code == 409
    assert "stale evidence prevents release" in pub_resp.text


def test_rule_release_lifecycle_and_diff(db, collection, bound_program):
    client = api_client(db, collection)
    prog_id, _ = bound_program

    # Create draft v1
    v1 = client.post(
        f"/api/admin/programs/{prog_id}/rules/draft",
        json={"notes": "Release v1"},
    ).json()
    rel_1_id = v1["releaseId"]
    assert v1["releaseVersion"] == 1

    # Approve v1
    appr_1 = client.post(
        f"/api/admin/programs/{prog_id}/rules/{rel_1_id}/approve",
        json={"notes": "Approved by dept chair"},
    ).json()
    assert appr_1["status"] == "approved"

    # Publish v1
    pub_1 = client.post(
        f"/api/admin/programs/{prog_id}/rules/{rel_1_id}/publish",
        json={"notes": "Published for 2026 cohort"},
    ).json()
    assert pub_1["status"] == "published"

    # Verify program current rule release is v1
    insp = client.get(f"/api/admin/programs/{prog_id}/inspection").json()
    assert insp["program"]["currentRuleReleaseId"] == rel_1_id

    # Create draft v2 with an edit to electives
    tree_v2 = dict(v1["rulesTree"])
    # Modify children
    tree_v2["children"][1]["count"] = 3
    tree_v2["children"][1]["title"] = "Choose 3 electives"

    v2 = client.post(
        f"/api/admin/programs/{prog_id}/rules/draft",
        json={"rulesTree": tree_v2, "notes": "Increased electives to 3"},
    ).json()
    rel_2_id = v2["releaseId"]
    assert v2["releaseVersion"] == 2

    # An unapproved draft cannot be published, even by the same administrator.
    unapproved = client.post(
        f"/api/admin/programs/{prog_id}/rules/{rel_2_id}/publish", json={"notes": "Too early"},
    )
    assert unapproved.status_code == 409
    assert client.post(
        f"/api/admin/programs/{prog_id}/rules/{rel_2_id}/approve", json={},
    ).status_code == 200
    # Publish v2
    pub_2 = client.post(
        f"/api/admin/programs/{prog_id}/rules/{rel_2_id}/publish",
        json={"notes": "Published v2"},
    ).json()
    assert pub_2["status"] == "published"

    # Verify v1 was archived and v2 is current
    insp2 = client.get(f"/api/admin/programs/{prog_id}/inspection").json()
    assert insp2["program"]["currentRuleReleaseId"] == rel_2_id
    releases = {r["releaseId"]: r["status"] for r in insp2["releases"]}
    assert releases[rel_1_id] == "archived"
    assert releases[rel_2_id] == "published"

    # Diff v1 and v2
    diff_resp = client.get(
        f"/api/admin/programs/{prog_id}/rules/diff?release_id_a={rel_1_id}&release_id_b={rel_2_id}"
    )
    assert diff_resp.status_code == 200, diff_resp.text
    diff = diff_resp.json()
    assert diff["versionA"] == 1
    assert diff["versionB"] == 2
    assert len(diff["modifiedNodes"]) == 1
    assert diff["modifiedNodes"][0]["nodeId"] == "pos-1"
    assert diff["digestMatch"] is False

    # Reject a draft
    v3 = client.post(
        f"/api/admin/programs/{prog_id}/rules/draft",
        json={"notes": "Draft v3 to reject"},
    ).json()
    rej_resp = client.post(
        f"/api/admin/programs/{prog_id}/rules/{v3['releaseId']}/reject",
        json={"reason": "Missing faculty review"},
    )
    assert rej_resp.status_code == 200
    assert rej_resp.json()["status"] == "rejected"
