"""Atlas import + prerequisite seam tests against a real Postgres. No models."""

import hashlib
import html
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

import app.atlas.importer as atlas_importer
from app.advising.requirement_holds import apply_requirement_hold
from app.atlas.authority import prepare_authoritative_courses
from app.atlas.importer import (
    ATLAS_CONTENT_TYPE,
    import_atlas,
    import_authoritative_atlas,
    refresh_atlas_requisites,
)
from app.atlas.offerings import (
    _section_scope,
    publish_offerings,
    read_offering_snapshot,
    render_offerings_markdown,
    select_latest_offering_captures,
    validate_offering_captures,
)
from app.atlas.records import AtlasCourse, canonical_code_map, load_atlas
from app.db import queries
from app.retrieval.config import PrereqSeamConfig, load_retrieval_config
from app.retrieval.retrieve import retrieve
from app.retrieval.seams.prereq import prereq_seam
from app.retrieval.types import Retrieved
from app.util.digest import canonical_json_bytes
from tests.fakes import FakeEmbedder, whitespace_tokens

_MODEL = "fake-model"

_SAMPLE = """\
{"code": "ECON 101", "title": "Principles of Macroeconomics", "description": "Intro macro.", "requisites": "Prerequisite: MATH 111 or 112."}
{"code": "ECON 201", "title": "Intermediate Macroeconomics", "description": "Macro theory.", "requisites": "ECON 101"}
{"code": "MATH 111", "title": "Calculus I", "description": "Limits.", "requisites": null}
{"code": "MATH 112", "title": "Calculus II", "description": "Series.", "requisites": "MATH 111"}
{"code": "ECON 301", "title": "Econometrics", "description": "Stats.", "requisites": "ECON 201 and MATH 112"}
{"code": "ECON 350", "title": "Topics", "description": "Varies.", "requisites": "ECON 101 and a love of graphs"}
{"code": "ECON 401", "title": "Advanced", "description": "Hard.", "requisites": "Prerequisite: ECON 101. Corequisite: MATH 111."}
{"code": "BUSA 210", "title": "Business Stats", "description": "Stats.", "requisites": "MATH 111 or equivalent"}"""


def _offering_captures(*, description="Captured live description.",
                       restriction="Graduate students only",
                       career="Graduate Allied Health"):
    term = "Spring 2027"
    results = [
        {"code": "BIOL_OX 142WE", "title": "Biology grouped card", "key": "",
         "group": "code:BIOL_OX 142WE", "matched": "crn:100,101", "srcdb": "5271"},
        {"code": "GEOL_OX 385W", "title": "Geology seminar", "key": "crn:202",
         "group": "code:GEOL_OX 385W", "matched": "crn:202", "srcdb": "5271"},
    ]
    rows = "".join(
        '<a class="result__link" '
        f'data-group="{html.escape(row["group"], quote=True)}" '
        f'data-key="{html.escape(row["key"], quote=True)}" '
        f'data-matched="{html.escape(row["matched"], quote=True)}" '
        f'data-srcdb="{row["srcdb"]}">'
        f'<span class="result__code">{html.escape(row["code"])}</span>'
        f'<span class="result__title">{html.escape(row["title"])}</span></a>'
        for row in results
    )
    index = {
        "kind": "index", "source_url": "https://atlas.emory.edu/", "term": term,
        "career": career, "observed_at": "2026-10-20T12:00:00Z",
        "html": (
            f'<div class="SearchCriteria">Term: {term} Career: {career}</div>'
            '<div class="Found">Found 2 courses</div>' + rows
        ),
    }
    details = []
    for cursor, row in enumerate(results):
        crns = [value.removeprefix("crn:") for value in row["matched"].split(",")]
        section_rows = "".join(
            '<a class="course-section" '
            f'data-key="crn:{crn}" data-group="{row["group"]}" '
            f'data-srcdb="{row["srcdb"]}" data-matched="{row["matched"]}"></a>'
            for crn in crns
        )
        class_html = ("<div class=\"dtl-section\">Course group</div>" if cursor == 0 else
                      '<div class="dtl-section">Section 1, Class Nbr 202</div>')
        details.append({
            "kind": "detail", "source_url": "https://atlas.emory.edu/", "term": term,
            "career": career, "observed_at": f"2026-10-20T12:00:0{cursor}Z",
            "index_cursor": cursor,
            "result_identity": {
                "group": row["group"], "key": row["key"], "matched": row["matched"],
                "srcdb": row["srcdb"], "label": f"{row['code']} result card",
            },
            "html": (
                f'<div class="dtl-course-code">{row["code"]}</div>{class_html}'
                f'<div class="detail-title">{row["title"]}</div>'
                f'<section class="section section--description"><div class="section__content">'
                f'{html.escape(description)}</div></section>'
                '<section class="section section--registration_restrictions">'
                f'<div class="section__content">{html.escape(restriction)}</div></section>'
                '<section class="section section--classnotes">'
                '<div class="section__content">Instructor permission required</div></section>'
                '<div class="detail-consent">Consent required</div>'
                f'{section_rows}'
            ),
        })
    return index, details


def _import(db, collection, tmp_path, text=_SAMPLE, *, allow_course_removal=False):
    path = tmp_path / "atlas.jsonl"
    path.write_text(text, encoding="utf-8")
    return import_atlas(
        db,
        collection_id=collection,
        path=path,
        embedder=FakeEmbedder(dim=8),
        embed_model=_MODEL,
        count_tokens=whitespace_tokens,
        config=load_retrieval_config(),
        allow_course_removal=allow_course_removal,
    )


def _stage_offerings(snapshot, path):
    path.write_bytes(render_offerings_markdown(snapshot))
    return path


def _publish_offerings(db, collection, snapshot, path, embedder=None):
    return publish_offerings(
        db,
        collection_id=collection,
        snapshot=snapshot,
        path=_stage_offerings(snapshot, path),
        data_dir=path.parent / "data",
        embedder=embedder or FakeEmbedder(dim=8),
        embed_model=_MODEL,
        count_tokens=whitespace_tokens,
        config=load_retrieval_config(),
    )


def test_offerings_preflight_keeps_raw_group_and_course_identities():
    index, details = _offering_captures()
    snapshot = validate_offering_captures(index, details)
    assert [row.raw_course_code for row in snapshot.records] == [
        "BIOL_OX 142WE", "GEOL_OX 385W",
    ]
    assert snapshot.records[0].class_number is None
    assert snapshot.records[0].is_grouped is True
    assert len(snapshot.records[0].raw_metadata["sections"]) == 2
    markdown = render_offerings_markdown(snapshot).decode("utf-8")
    assert "BIOL_OX 142WE" in markdown and "GEOL_OX 385W" in markdown
    assert "## BIOL_OX 142WE — Biology grouped card (grouped result)" in markdown
    assert "## GEOL_OX 385W — Geology seminar (class 202)" in markdown
    entries = markdown.split("\n## ")[1:]
    assert len(entries) == 2
    assert all("- Term: Spring 2027" in entry for entry in entries)
    assert all("- Career: Graduate Allied Health" in entry for entry in entries)
    assert "grouped result card; no single class number was shown" in markdown
    assert "Consent required" in markdown
    assert "Graduate students only" in markdown
    assert "Instructor permission required" in markdown
    assert "Captured result metadata" not in markdown
    assert "2026-10-20T12:00" not in markdown
    later = deepcopy(index)
    later["observed_at"] = "2026-10-21T12:00:00Z"
    later_details = deepcopy(details)
    for cursor, detail in enumerate(later_details):
        detail["observed_at"] = f"2026-10-21T12:00:0{cursor + 1}Z"
    assert render_offerings_markdown(validate_offering_captures(later, later_details)) == render_offerings_markdown(snapshot)


@pytest.mark.parametrize(
    ("problem", "message"),
    [
        ("missing_detail", "no valid detail capture for current index result row"),
        ("mismatched_identity", "no valid detail capture for current index result row"),
        ("empty_law", "empty offerings indexes are held"),
    ],
)
def test_offerings_preflight_rejects_incomplete_or_placeholder_sets(problem, message):
    index, details = _offering_captures()
    if problem == "missing_detail":
        details = details[:-1]
    elif problem == "mismatched_identity":
        details[0]["result_identity"]["matched"] = "crn:unknown"
    elif problem == "empty_law":
        index = {
            "kind": "index", "source_url": "https://atlas.emory.edu/",
            "term": "Spring 2027", "career": "Law", "observed_at": "2026-10-20T12:00:00Z",
            "html": "<div class='SearchCriteria'>Term: Spring 2027 Career: Law</div>"
                    "<div class='Found'>Found 0 courses</div>",
        }
        details = []
    with pytest.raises(ValueError, match=message):
        validate_offering_captures(index, details)


@pytest.mark.parametrize(("held_code", "held_title"), [
    ("AAAA 999T", "Course To Be Scheduled Later"),
    ("AAAA 998UC", "Course To Be Scheduled Later"),
    ("AAAA 997UC", "TEST Course"),
])
def test_offerings_preflight_counts_all_links_and_quarantines_placeholder(held_code, held_title):
    index, details = _offering_captures(career="Theology")
    index["html"] = index["html"].replace("Found 2 courses", "Found 1 course")
    index["html"] = index["html"].replace("BIOL_OX 142WE", held_code)
    index["html"] = index["html"].replace("Biology grouped card", held_title)
    details[0]["html"] = details[0]["html"].replace("BIOL_OX 142WE", held_code)
    details[0]["html"] = details[0]["html"].replace(
        "Biology grouped card", held_title)
    details[0]["result_identity"]["group"] = f"code:{held_code}"
    details[0]["result_identity"]["label"] = "AAAA placeholder"
    snapshot = validate_offering_captures(index, details)
    assert snapshot.announced_count == 1 and len(snapshot.records) == 2
    assert snapshot.eligible_count == 1 and snapshot.held_count == 1
    assert snapshot.records[0].review_status == "held_placeholder"
    markdown = render_offerings_markdown(snapshot).decode("utf-8")
    assert held_code not in markdown
    assert held_title not in markdown
    assert "Held placeholder rows: 1" in markdown


def test_capture_selection_uses_latest_index_and_latest_valid_exact_detail():
    index, details = _offering_captures()
    truncated = deepcopy(index)
    truncated["observed_at"] = "2026-10-20T11:00:00Z"
    truncated["html"] = truncated["html"].replace("Found 2 courses", "Found 2 courses").replace(
        "data-key=\"crn:202\"", "data-key=\"crn:old\"")
    stale_detail = deepcopy(details[1])
    stale_detail["observed_at"] = "2026-10-20T11:30:00Z"
    stale_detail["result_identity"]["key"] = "crn:old"

    newer_valid = deepcopy(details[1])
    newer_valid["observed_at"] = "2026-10-20T12:00:03Z"
    newer_valid["html"] = newer_valid["html"].replace(
        "Captured live description.", "Latest exact detail description.")
    latest_invalid = deepcopy(details[1])
    latest_invalid["observed_at"] = "2026-10-20T12:00:04Z"
    latest_invalid["html"] = latest_invalid["html"].replace(
        "Class Nbr 202", "Class Nbr 999")

    selected_index, selected_details = select_latest_offering_captures(
        [truncated, index], [stale_detail, details[0], details[1], newer_valid, latest_invalid],
        career="Graduate Allied Health", term="Spring 2027",
    )
    assert selected_index["html"] == index["html"]
    snapshot = validate_offering_captures(selected_index, selected_details)
    assert snapshot.records[1].description == "Latest exact detail description."

    later_index = deepcopy(index)
    later_index["observed_at"] = "2026-10-21T12:00:00Z"
    with pytest.raises(ValueError, match="no valid detail capture"):
        select_latest_offering_captures(
            [later_index], details, career="Graduate Allied Health", term="Spring 2027",
        )

    nested_identity = deepcopy(details[0])
    nested_identity["result_identity"] = {
        "attrs": {
            "data-group": details[0]["result_identity"]["group"],
            "data-key": details[0]["result_identity"]["key"],
            "data-matched": details[0]["result_identity"]["matched"],
            "data-srcdb": details[0]["result_identity"]["srcdb"],
        },
        "text": "Raw clicked result link text",
    }
    selected_index, selected_details = select_latest_offering_captures(
        [index], [nested_identity, details[1]],
        career="Graduate Allied Health", term="Spring 2027",
    )
    assert validate_offering_captures(selected_index, selected_details).records[0].raw_course_code == (
        "BIOL_OX 142WE"
    )

    same_time_different_index = deepcopy(index)
    same_time_different_index["html"] = same_time_different_index["html"].replace(
        "Geology seminar", "Changed at same time")
    with pytest.raises(ValueError, match="same timestamp but different envelopes"):
        select_latest_offering_captures(
            [index, same_time_different_index], details,
            career="Graduate Allied Health", term="Spring 2027",
        )

    invalid_current = deepcopy(index)
    invalid_current["observed_at"] = "2026-10-20T13:00:00Z"
    invalid_current["html"] = invalid_current["html"].replace(
        "Found 2 courses", "Found 3 courses")
    with pytest.raises(ValueError, match="fewer than its announced result count"):
        select_latest_offering_captures(
            [index, invalid_current], details,
            career="Graduate Allied Health", term="Spring 2027",
        )


def test_offerings_publish_separately_and_keep_historical_source_rows(
    db, collection, tmp_path,
):
    master_source = _import(db, collection, tmp_path)
    master_courses = db.execute(
        "SELECT code, title, source_id, parse_status FROM courses "
        "WHERE collection_id = %(c)s ORDER BY code",
        {"c": collection},
    ).fetchall()
    prerequisite_nodes = db.execute(
        "SELECT count(*) AS n FROM prereq_nodes n JOIN courses c USING (course_id) "
        "WHERE c.collection_id = %(c)s", {"c": collection},
    ).fetchone()["n"]

    index, details = _offering_captures()
    index["html"] = index["html"].replace("Found 2 courses", "Found 1 course")
    index["html"] = index["html"].replace("BIOL_OX 142WE", "AAAA 999T").replace(
        "Biology grouped card", "Course To Be Scheduled Later")
    details[0]["html"] = details[0]["html"].replace("BIOL_OX 142WE", "AAAA 999T").replace(
        "Biology grouped card", "Course To Be Scheduled Later")
    details[0]["result_identity"]["group"] = "code:AAAA 999T"
    first_snapshot = validate_offering_captures(index, details)
    first_path = tmp_path / "offerings.md"
    first_source = _publish_offerings(db, collection, first_snapshot, first_path)
    first_source_row = db.execute(queries.get("get_source_by_hash"), {
        "collection_id": collection,
        "file_hash": hashlib.sha256(first_path.read_bytes()).hexdigest(),
        "workspace_key": None,
    }).fetchone()
    first_read = read_offering_snapshot(
        db, source_id=first_source, index_revision=first_source_row["index_revision"],
        collection_id=collection, capture_set_hash=first_snapshot.capture_set_hash,
    )
    assert first_read is not None and first_read["is_current"] is True
    assert first_read["announced_count"] == 1 and first_read["result_row_count"] == 2
    assert first_read["eligible_row_count"] == 1 and first_read["held_row_count"] == 1
    assert len(first_read["rows"]) == 1
    assert first_read["observed_from"].isoformat().startswith("2026-10-20")
    all_first_rows = read_offering_snapshot(
        db, source_id=first_source, index_revision=first_source_row["index_revision"],
        collection_id=collection, include_held=True,
    )["rows"]
    assert len(all_first_rows) == 2
    assert all_first_rows[0]["class_number"] is None
    assert all_first_rows[0]["is_grouped"] is True
    assert all_first_rows[0]["review_status"] == "held_placeholder"
    assert all_first_rows[0]["capture_envelope_hash"] == hashlib.sha256(
        canonical_json_bytes(details[0])
    ).hexdigest()
    assert all_first_rows[0]["observed_at"].isoformat().startswith("2026-10-20")
    assert len(all_first_rows[0]["raw_metadata"]["sections"]) == 2
    first_capture_files = db.execute(queries.get("list_atlas_offering_capture_files"), {
        "source_id": first_source, "capture_set_hash": first_snapshot.capture_set_hash,
    }).fetchall()
    assert len(first_capture_files) == 3
    for row in first_capture_files:
        capture_path = Path(row["raw_path"])
        assert capture_path.parent == tmp_path / "data" / "raw" / "atlas-captures"
        assert capture_path.name == f"{row['capture_hash']}.json"
        assert hashlib.sha256(capture_path.read_bytes()).hexdigest() == row["capture_hash"]

    recaptured_index = deepcopy(index)
    recaptured_details = deepcopy(details)
    recaptured_index["observed_at"] = "2026-10-21T12:00:00Z"
    for cursor, detail in enumerate(recaptured_details):
        detail["observed_at"] = f"2026-10-21T12:00:0{cursor + 1}Z"
    recaptured = validate_offering_captures(recaptured_index, recaptured_details)
    assert render_offerings_markdown(recaptured) == first_path.read_bytes()
    same_source = _publish_offerings(db, collection, recaptured, first_path)
    assert same_source == first_source
    latest_capture = read_offering_snapshot(
        db, source_id=first_source, index_revision=first_source_row["index_revision"],
        collection_id=collection,
    )
    assert latest_capture["capture_set_hash"] == recaptured.capture_set_hash
    assert latest_capture["observed_from"].isoformat().startswith("2026-10-21")
    recaptured_files = db.execute(queries.get("list_atlas_offering_capture_files"), {
        "source_id": first_source, "capture_set_hash": recaptured.capture_set_hash,
    }).fetchall()
    assert len(recaptured_files) == 3
    assert {row["capture_hash"] for row in recaptured_files} != {
        row["capture_hash"] for row in first_capture_files
    }
    original_capture = read_offering_snapshot(
        db, source_id=first_source, index_revision=first_source_row["index_revision"],
        collection_id=collection, capture_set_hash=first_snapshot.capture_set_hash,
        include_held=True,
    )
    assert original_capture["observed_from"].isoformat().startswith("2026-10-20")
    assert original_capture["rows"][0]["capture_envelope_hash"] == all_first_rows[0]["capture_envelope_hash"]

    changed_index, changed_details = _offering_captures(description="Changed term description.")
    changed_snapshot = validate_offering_captures(changed_index, changed_details)

    class BrokenEmbedder:
        def embed_documents(self, _texts):
            raise RuntimeError("offering staging failed")

    with pytest.raises(RuntimeError, match="offering staging failed"):
        _publish_offerings(db, collection, changed_snapshot, first_path, BrokenEmbedder())
    assert db.execute("SELECT is_current FROM sources WHERE source_id = %s", (first_source,)).fetchone()[
        "is_current"
    ] is True

    second_source = _publish_offerings(db, collection, changed_snapshot, first_path)
    assert second_source != first_source
    historical = read_offering_snapshot(
        db, source_id=first_source, index_revision=first_source_row["index_revision"],
        collection_id=collection, capture_set_hash=first_snapshot.capture_set_hash,
    )
    assert historical is not None and historical["is_current"] is False
    assert historical["rows"] == first_read["rows"]
    second_source_row = db.execute(queries.get("get_source_by_hash"), {
        "collection_id": collection,
        "file_hash": hashlib.sha256(first_path.read_bytes()).hexdigest(),
        "workspace_key": None,
    }).fetchone()
    current = read_offering_snapshot(
        db, source_id=second_source, index_revision=second_source_row["index_revision"],
        collection_id=collection,
    )
    assert current is not None and current["is_current"] is True
    assert current["rows"][0]["description"] == "Changed term description."

    assert db.execute(
        "SELECT code, title, source_id, parse_status FROM courses "
        "WHERE collection_id = %(c)s ORDER BY code", {"c": collection},
    ).fetchall() == master_courses
    assert db.execute(
        "SELECT count(*) AS n FROM prereq_nodes n JOIN courses c USING (course_id) "
        "WHERE c.collection_id = %(c)s", {"c": collection},
    ).fetchone()["n"] == prerequisite_nodes
    assert db.execute("SELECT is_current FROM sources WHERE source_id = %s", (master_source,)).fetchone()[
        "is_current"
    ] is True


def test_class_section_scope_excludes_other_class_but_group_scope_keeps_children():
    sections = [
        {"crn": "100", "section": "001", "campus": "Atlanta", "section_type": "Lecture"},
        {"crn": "101", "section": "002", "campus": "Oxford", "section_type": "Lab"},
    ]
    assert _section_scope(sections, "100") == "sections 001; campuses Atlanta; types Lecture"
    assert _section_scope(sections) == (
        "sections 001, 002; campuses Atlanta, Oxford; types Lab, Lecture"
    )


def test_only_placeholder_offerings_cannot_publish(db, collection, tmp_path):
    placeholder_index, placeholder_details = _offering_captures(career="Theology")
    placeholder_index["html"] = placeholder_index["html"].replace(
        "BIOL_OX 142WE", "AAAA 999T").replace(
        "Biology grouped card", "Course To Be Scheduled Later")
    placeholder_details[0]["html"] = placeholder_details[0]["html"].replace(
        "BIOL_OX 142WE", "AAAA 999T").replace(
        "Biology grouped card", "Course To Be Scheduled Later")
    placeholder_details[0]["result_identity"]["group"] = "code:AAAA 999T"
    held_snapshot = validate_offering_captures(placeholder_index, placeholder_details)
    assert held_snapshot.eligible_count == 1
    # Turn the remaining result into a held placeholder too; an all-held source
    # is never indexed as an ordinary advising document.
    only_held_index = placeholder_index.copy()
    only_held_details = [dict(item) for item in placeholder_details]
    only_held_index["html"] = only_held_index["html"].replace(
        "GEOL_OX 385W", "AAAA 999T").replace(
        "Geology seminar", "Course To Be Scheduled Later")
    only_held_details[1]["html"] = only_held_details[1]["html"].replace(
        "GEOL_OX 385W", "AAAA 999T").replace(
        "Geology seminar", "Course To Be Scheduled Later")
    only_held_details[1]["result_identity"]["group"] = "code:AAAA 999T"
    only_held_details[1]["result_identity"]["label"] = "AAAA placeholder"
    only_held = validate_offering_captures(only_held_index, only_held_details)
    assert only_held.eligible_count == 0 and only_held.held_count == 2
    with pytest.raises(ValueError, match="all results are held"):
        _publish_offerings(db, collection, only_held, tmp_path / "theology.md")


def _seam_config(**overrides):
    base = {
        "enabled": True,
        "decay": 0.8,
        "directions": ("self", "upstream"),
        "limit": 40,
    }
    base.update(overrides)
    return PrereqSeamConfig(**base)


def test_description_requirements_import_exact_text_without_partial_graphs(db, collection, tmp_path):
    records = [
        {"code": "CS 120", "title": "Foundation"},
        {"code": "CS 220", "title": "Algorithms",
         "description": "Algorithm analysis. Prerequisite: CS 120."},
        {"code": "CS 240", "title": "Systems", "requisites": "CS 120",
         "description": "Prerequisite: CS 220."},
        {"code": "CS 250", "title": "Seminar",
         "description": "Prerequisite: CS 120. Corequisite: MATH 111 and MATH 112 or MATH 119."},
    ]
    source = _import(db, collection, tmp_path, "\n".join(map(json.dumps, records)))
    rows = db.execute(queries.get("list_courses_for_source"), {"source_id": source}).fetchall()
    by_code = {row["code"]: row for row in rows}
    assert by_code["CS 220"]["requisites_raw"] == "Prerequisite: CS 120."
    assert by_code["CS 220"]["parse_status"] == "parsed"
    assert by_code["CS 240"]["requisites_raw"] == "CS 120"
    assert by_code["CS 250"]["parse_status"] == "ambiguous"
    assert not db.execute(queries.get("list_nodes_for_course"), {
        "course_id": by_code["CS 250"]["course_id"],
    }).fetchall()


def test_unchanged_atlas_refreshes_old_parser_without_reembedding(db, collection, tmp_path):
    text = json.dumps({"code": "CS 220", "title": "Algorithms",
                       "description": "Algorithm analysis. Prerequisite: CS 120."})
    source = _import(db, collection, tmp_path, text)
    source_params = {"collection_id": collection,
                     "file_hash": hashlib.sha256(text.encode()).hexdigest(), "workspace_key": None}
    before_revision = db.execute(queries.get("get_source_by_hash"), source_params).fetchone()[
        "index_revision"]
    before_chunks = db.execute(queries.get("list_chunks_for_source"), {
        "source_id": source,
    }).fetchall()
    course = db.execute(queries.get("get_course_by_code"), {
        "collection_id": collection, "code": "CS 220",
    }).fetchone()
    db.execute("UPDATE courses SET requisites_raw = NULL, parse_status = 'none', "
               "parser_version = '3', authority = 'atlas', lifecycle = 'retired' "
               "WHERE course_id = %(c)s", {"c": course["course_id"]})
    db.execute(queries.get("delete_prereq_nodes_for_course"), {"course_id": course["course_id"]})
    db.execute(queries.get("delete_prereq_mentions_for_course"), {"course_id": course["course_id"]})
    db.commit()

    class BrokenEmbedder:
        def embed_documents(self, texts):
            raise AssertionError("unchanged atlas must not be re-embedded")

    repeated = import_atlas(db, collection_id=collection, path=tmp_path / "atlas.jsonl",
        embedder=BrokenEmbedder(), embed_model=_MODEL, count_tokens=whitespace_tokens,
        config=load_retrieval_config())
    assert repeated == source
    assert db.execute(queries.get("list_chunks_for_source"), {
        "source_id": source,
    }).fetchall() == before_chunks
    repaired = db.execute(queries.get("get_course_by_code"), {
        "collection_id": collection, "code": "CS 220",
    }).fetchone()
    assert repaired["parse_status"] == "parsed"
    assert repaired["requisites_raw"] == "Prerequisite: CS 120."
    published = db.execute(queries.get("list_courses_for_source"), {
        "source_id": source,
    }).fetchone()
    assert published["authority"] == "atlas"
    assert published["lifecycle"] == "retired"
    after_revision = db.execute(queries.get("get_source_by_hash"), source_params).fetchone()[
        "index_revision"]
    assert after_revision == before_revision + 1
    assert refresh_atlas_requisites(db, collection_id=collection,
                                   path=tmp_path / "atlas.jsonl") == 0
    assert db.execute(queries.get("get_source_by_hash"), source_params).fetchone()[
        "index_revision"] == after_revision


def test_metadata_repair_rejects_a_different_atlas_file(db, collection, tmp_path):
    _import(db, collection, tmp_path)
    changed = tmp_path / "different.jsonl"
    changed.write_text(_SAMPLE + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="exact current indexed"):
        refresh_atlas_requisites(db, collection_id=collection, path=changed)


def test_metadata_repair_preserves_manual_course_corrections(db, collection, tmp_path):
    _import(db, collection, tmp_path)
    db.execute("UPDATE courses SET requisites_raw = 'MATH 119', parser_version = 'manual' "
               "WHERE collection_id = %(c)s AND code = 'MATH 112'", {"c": collection})
    db.commit()
    with pytest.raises(ValueError, match="manual changes"):
        refresh_atlas_requisites(db, collection_id=collection, path=tmp_path / "atlas.jsonl")
    row = db.execute(queries.get("get_course_by_code"), {
        "collection_id": collection, "code": "MATH 112",
    }).fetchone()
    assert row["requisites_raw"] == "MATH 119"


def test_import_creates_courses_and_parse_status(db, collection, tmp_path):
    source_id = _import(db, collection, tmp_path)
    rows = db.execute(
        queries.get("list_courses_for_source"), {"source_id": source_id}
    ).fetchall()
    by_code = {row["code"]: row for row in rows}
    assert by_code["ECON 101"]["parse_status"] == "parsed"
    assert by_code["ECON 201"]["parse_status"] == "parsed"
    assert by_code["ECON 350"]["parse_status"] == "unparsed"
    assert by_code["MATH 111"]["parse_status"] == "none"
    assert by_code["ECON 401"]["parse_status"] == "parsed"
    # A parsed course writes its tree: "MATH 111 or equivalent" is an any-node with a condition.
    nodes = db.execute(
        queries.get("list_nodes_for_course"), {"course_id": by_code["BUSA 210"]["course_id"]}
    ).fetchall()
    assert "any" in [node["node_type"] for node in nodes]
    assert any(node["condition_text"] == "equivalent" for node in nodes)


def test_labs_pair_with_their_lecture_only_when_the_text_names_a_corequisite(
    db, collection, tmp_path,
):
    text = "\n".join(json.dumps(row) for row in [
        {"code": "CHEM 150", "title": "Structure"},
        {"code": "CHEM 150L", "title": "Structure Lab", "requisites": "CHEM 150 as a corequisite."},
        {"code": "CHEM 202", "title": "Reactivity"},
        {"code": "CHEM 202L", "title": "Reactivity Lab", "requisites": "CHEM 202 as prerequisite."},
        {"code": "BIOL 141", "title": "Biology", "requisites": "BIOL 141L as a co-requisite."},
        {"code": "BIOL 141L", "title": "Biology Lab"},
    ])
    _import(db, collection, tmp_path, text)
    pairs = db.execute(
        "SELECT lab_code, lecture_code FROM course_lab_pairings "
        "WHERE collection_id = %(collection_id)s ORDER BY lab_code",
        {"collection_id": collection},
    ).fetchall()
    assert [(row["lab_code"], row["lecture_code"]) for row in pairs] == [
        ("BIOL 141L", "BIOL 141"), ("CHEM 150L", "CHEM 150"),
    ]
    unpaired = db.execute(queries.get("list_unpaired_labs"), {
        "collection_id": collection,
    }).fetchall()
    assert [row["lab_code"] for row in unpaired] == ["CHEM 202L"]


def test_catalog_importer_cannot_claim_atlas_authority(tmp_path):
    path = tmp_path / "atlas-primary.jsonl"
    path.write_text(json.dumps({
        "code": "BIOL_OX 142WE", "title": "Biology", "authority": "atlas",
        "term": "Spring 2027", "careers": ["Graduate Allied Health"],
        "evidence": [],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="verified authoritative importer"):
        import_atlas(
            None, collection_id=None, path=path,
            embedder=FakeEmbedder(dim=8), embed_model=_MODEL,
            count_tokens=whitespace_tokens, config=load_retrieval_config(),
        )


def test_authoritative_publication_pins_capture_rows_and_releases_matching_hold(
    db, collection, tmp_path,
):
    base_path = tmp_path / "base.jsonl"
    base_records = [
        {"code": "BIOL_OX 142WE", "title": "Catalog Biology", "description": "Catalog copy.",
         "requisites": "Prerequisite: MUS 221"},
        {"code": "GEOL_OX 385W", "title": "Catalog Geology", "description": "Catalog copy."},
        {"code": "CS 171", "title": "Catalog Algorithms", "description": "Catalog copy."},
        {"code": "MUS 221", "title": "Catalog Theory", "description": "Catalog copy."},
    ]
    base_path.write_text("\n".join(json.dumps(row) for row in base_records), encoding="utf-8")
    old_source = _import(db, collection, tmp_path, base_path.read_text(encoding="utf-8"))

    index, details = _offering_captures(restriction="Prerequisite: CS 171")
    snapshot = validate_offering_captures(index, details)
    offering_source = _publish_offerings(db, collection, snapshot, tmp_path / "offerings.md")
    offering_revision = db.execute(
        "SELECT index_revision FROM sources WHERE source_id = %(source_id)s",
        {"source_id": offering_source},
    ).fetchone()["index_revision"]
    evidence_record = snapshot.records[0]
    evidence_path = (tmp_path / "data" / "raw" / "atlas-captures" /
                     f"{evidence_record.capture_envelope_hash}.json")
    master_hash = db.execute(
        "SELECT file_hash FROM sources WHERE source_id = %(source_id)s",
        {"source_id": old_source},
    ).fetchone()["file_hash"]
    stable_course_id = db.execute(queries.get("get_course_by_code"), {
        "collection_id": collection, "code": "BIOL_OX 142WE",
    }).fetchone()["course_id"]
    hold = apply_requirement_hold(
        db, source_id=old_source, course_code="BIOL_OX 142WE",
        master_file_hash=master_hash,
        conflict_reason="Catalog course requirement conflicts with captured Atlas authority.",
        evidence_capture_sha256=evidence_record.capture_envelope_hash,
        evidence_capture_path=str(evidence_path), curator="test curator",
        evidence_source_id=offering_source, evidence_index_revision=offering_revision,
        evidence_capture_set_hash=snapshot.capture_set_hash,
    )

    def write_primary(selected_snapshot, name):
        courses = prepare_authoritative_courses(
            load_atlas(base_path), [selected_snapshot], term="Spring 2027",
        )
        path = tmp_path / f"primary-{name}.jsonl"
        path.write_bytes(b"".join(
            canonical_json_bytes(course.model_dump(mode="json")) + b"\n"
            for course in courses
        ))
        return path

    changed_row = replace(evidence_record, restrictions="Forged section restriction")
    tampered_snapshot = replace(snapshot, records=(changed_row, *snapshot.records[1:]))
    with pytest.raises(ValueError, match="differs from its immutable indexed evidence"):
        import_authoritative_atlas(
            db, collection_id=collection, path=write_primary(tampered_snapshot, "tampered"),
            snapshots=[tampered_snapshot], embedder=FakeEmbedder(dim=8),
            embed_model=_MODEL, count_tokens=whitespace_tokens,
            config=load_retrieval_config(),
        )
    assert db.execute(
        "SELECT status FROM atlas_requirement_holds WHERE hold_id = %(hold_id)s",
        {"hold_id": hold["hold_id"]},
    ).fetchone()["status"] == "active"
    assert db.execute(
        "SELECT is_current FROM sources WHERE source_id = %(source_id)s",
        {"source_id": old_source},
    ).fetchone()["is_current"] is True

    source_id = import_authoritative_atlas(
        db, collection_id=collection, path=write_primary(snapshot, "valid"), snapshots=[snapshot],
        catalog=load_atlas(base_path), embedder=FakeEmbedder(dim=8), embed_model=_MODEL,
        count_tokens=whitespace_tokens, config=load_retrieval_config(),
    )
    row = db.execute(queries.get("get_course_by_code"), {
        "collection_id": collection, "code": "BIOL_OX 142WE",
    }).fetchone()
    assert row["course_id"] == stable_course_id
    assert row["title"] == "Biology grouped card"
    schools = {
        item["code"]: (item["authority"], item["campus"], item["school"], item["source"])
        for item in db.execute(
            "SELECT c.code, c.authority, c.campus, s.school, s.source FROM courses c "
            "JOIN course_schools s USING (course_id) WHERE c.collection_id = %(collection_id)s",
            {"collection_id": collection},
        ).fetchall()
    }
    assert schools["BIOL_OX 142WE"] == ("atlas", "oxford", snapshot.career, "atlas_offering")
    assert schools["CS 171"] == ("catalog", "atlanta", "Emory College", "college_catalog")
    assert row["parse_status"] == "parsed"
    mentions = db.execute(queries.get("list_mentions_for_course"), {
        "course_id": row["course_id"],
    }).fetchall()
    assert [(item["kind"], item["target_code"]) for item in mentions] == [
        ("prerequisite", "CS 171"),
    ]
    assert db.execute(queries.get("list_chunks_for_source"), {
        "source_id": old_source,
    }).fetchall()
    assert db.execute(
        "SELECT is_current FROM sources WHERE source_id = %(source_id)s",
        {"source_id": old_source},
    ).fetchone()["is_current"] is False
    assert db.execute(
        "SELECT is_current FROM sources WHERE source_id = %(source_id)s",
        {"source_id": source_id},
    ).fetchone()["is_current"] is True
    assert db.execute(
        "SELECT status FROM atlas_requirement_holds WHERE hold_id = %(hold_id)s",
        {"hold_id": hold["hold_id"]},
    ).fetchone()["status"] == "released"
    with pytest.raises(ValueError, match="Atlas-primary"):
        import_atlas(
            db, collection_id=collection, path=base_path,
            embedder=FakeEmbedder(dim=8), embed_model=_MODEL,
            count_tokens=whitespace_tokens, config=load_retrieval_config(),
        )


def test_mentions_resolve_and_missing_target_is_null(db, collection, tmp_path):
    _import(db, collection, tmp_path)
    econs = db.execute(
        queries.get("get_course_by_code"),
        {"collection_id": collection, "code": "ECON 101"},
    ).fetchone()
    mentions = db.execute(
        queries.get("list_mentions_for_course"), {"course_id": econs["course_id"]}
    ).fetchall()
    targets = {m["target_code"]: m["target_course_id"] for m in mentions}
    assert targets["MATH 111"] is not None
    assert targets["MATH 112"] is not None

    external = '{"code": "ECON 500", "title": "Seminar", "description": "x", ' \
        '"requisites": "PHIL 999"}'
    path = tmp_path / "atlas2.jsonl"
    path.write_text(external, encoding="utf-8")
    import_atlas(
        db,
        collection_id=collection,
        path=path,
        embedder=FakeEmbedder(dim=8),
        embed_model=_MODEL,
        count_tokens=whitespace_tokens,
        config=load_retrieval_config(),
        allow_course_removal=True,
    )
    course = db.execute(
        queries.get("get_course_by_code"),
        {"collection_id": collection, "code": "ECON 500"},
    ).fetchone()
    mention = db.execute(
        queries.get("list_mentions_for_course"), {"course_id": course["course_id"]}
    ).fetchone()
    assert mention["target_code"] == "PHIL 999"
    assert mention["target_course_id"] is None


def test_reimport_replaces_old_atlas(db, collection, tmp_path):
    first_source = _import(db, collection, tmp_path)
    first_chunks = db.execute(
        queries.get("list_chunks_for_source"), {"source_id": first_source}
    ).fetchall()
    first_count = db.execute(
        "SELECT COUNT(*) AS n FROM courses WHERE collection_id = %(c)s",
        {"c": collection},
    ).fetchone()["n"]
    smaller = '{"code": "ECON 101", "title": "Macro", "description": "x", ' \
        '"requisites": null}'
    with pytest.raises(ValueError, match="explicitly allow course removal"):
        _import(db, collection, tmp_path, text=smaller)
    assert db.execute(queries.get("list_atlas_sources"), {
        "collection_id": collection, "content_type": ATLAS_CONTENT_TYPE,
    }).fetchall()[0]["source_id"] == first_source
    assert db.execute(queries.get("list_chunks_for_source"), {
        "source_id": first_source,
    }).fetchall() == first_chunks
    assert db.execute("SELECT COUNT(*) AS n FROM courses WHERE collection_id = %(c)s",
                      {"c": collection}).fetchone()["n"] == first_count

    _import(db, collection, tmp_path, text=smaller, allow_course_removal=True)
    rows = db.execute(
        queries.get("list_atlas_sources"),
        {"collection_id": collection, "content_type": ATLAS_CONTENT_TYPE},
    ).fetchall()
    assert len(rows) == 1
    counts = db.execute(
        "SELECT lifecycle, COUNT(*) AS n FROM courses WHERE collection_id = %(c)s "
        "GROUP BY lifecycle ORDER BY lifecycle",
        {"c": collection},
    ).fetchall()
    assert {(row["lifecycle"], row["n"]) for row in counts} == {
        ("current", 1), ("retired", first_count - 1),
    }
    old_source = db.execute(
        "SELECT is_current FROM sources WHERE source_id=%s", (first_source,)
    ).fetchone()
    assert old_source["is_current"] is False
    assert db.execute("SELECT count(*) AS n FROM sources WHERE collection_id=%s AND content_type=%s",
                      (collection, ATLAS_CONTENT_TYPE)).fetchone()["n"] == 2
    assert db.execute(
        queries.get("list_chunks_for_source"), {"source_id": first_source}
    ).fetchall() == first_chunks

    # A retired identity that returns to the published catalog becomes current
    # again without changing its content-addressed course_id.
    _import(db, collection, tmp_path, text=_SAMPLE, allow_course_removal=True)
    restored = db.execute(
        "SELECT lifecycle, COUNT(*) AS n FROM courses WHERE collection_id = %(c)s "
        "GROUP BY lifecycle",
        {"c": collection},
    ).fetchall()
    assert {(row["lifecycle"], row["n"]) for row in restored} == {("current", first_count)}


def test_retired_courses_are_listed_separately_from_the_current_catalog(db, collection, tmp_path):
    from tests.helpers import api_client, collection_name

    _import(db, collection, tmp_path)
    smaller = '{"code": "ECON 101", "title": "Macro", "description": "x", ' \
        '"requisites": null}'
    _import(db, collection, tmp_path, text=smaller, allow_course_removal=True)
    client = api_client(db, collection)
    response = client.get("/api/retired-courses", params={
        "query": "econ", "collection": collection_name(db, collection),
    })
    assert response.status_code == 200
    rows = response.json()
    assert {row["code"] for row in rows} == {
        "ECON 201", "ECON 301", "ECON 350", "ECON 401",
    }
    assert {row["lifecycle"] for row in rows} == {"retired"}


@pytest.mark.parametrize("failure", ["embedding", "input_changed"])
def test_failed_atlas_replacement_preserves_active_graph(
    db, collection, tmp_path, monkeypatch, failure,
):
    first_source = _import(db, collection, tmp_path)
    first_chunks = db.execute(queries.get("list_chunks_for_source"), {
        "source_id": first_source,
    }).fetchall()
    first_courses = db.execute(
        "SELECT code, title, source_id FROM courses WHERE collection_id = %(c)s ORDER BY code",
        {"c": collection},
    ).fetchall()

    class BrokenEmbedder:
        def embed_documents(self, _texts):
            raise RuntimeError("atlas staging failed")

    path = tmp_path / "new-atlas.jsonl"
    path.write_text('{"code":"ECON 101","title":"Changed","requisites":null}',
                    encoding="utf-8")
    embedder = BrokenEmbedder() if failure == "embedding" else FakeEmbedder(dim=8)
    if failure == "input_changed":
        real_ingest_file = atlas_importer.ingest_file

        def change_input_before_pipeline(conn, **kwargs):
            kwargs["path"].write_text(
                '{"code":"ECON 101","title":"Substituted input",'
                '"description":"different bytes","requisites":null}',
                encoding="utf-8",
            )
            return real_ingest_file(conn, **kwargs)

        monkeypatch.setattr(atlas_importer, "ingest_file", change_input_before_pipeline)

    expected_exception = RuntimeError if failure == "embedding" else ValueError
    expected_message = "atlas staging failed" if failure == "embedding" else "file changed during import"
    with pytest.raises(expected_exception, match=expected_message):
        import_atlas(db, collection_id=collection, path=path,
                     embedder=embedder, embed_model=_MODEL,
                     count_tokens=whitespace_tokens, config=load_retrieval_config(),
                     allow_course_removal=True)
    assert db.execute("SELECT is_current FROM sources WHERE source_id=%s", (first_source,)).fetchone()[
        "is_current"
    ] is True
    assert db.execute(queries.get("list_chunks_for_source"), {
        "source_id": first_source,
    }).fetchall() == first_chunks
    assert db.execute(
        "SELECT code, title, source_id FROM courses WHERE collection_id = %(c)s ORDER BY code",
        {"c": collection},
    ).fetchall() == first_courses


def test_prereq_seam_directions_codes_and_dormancy(db, collection, tmp_path):
    _import(db, collection, tmp_path)
    result = prereq_seam(db, collection, "ECON 301", _seam_config())
    assert result.state == "active"
    assert [c for c in result.candidates if c.raw_score == 1.0]  # the asked course itself
    assert [c for c in result.candidates if abs(c.raw_score - 0.8) < 1e-9]  # upstream

    lowercase = prereq_seam(db, collection, "what do I need for econ301?", _seam_config())
    assert lowercase.state == "active"
    assert prereq_seam(db, collection, "what is a matrix?", _seam_config()).state == "dormant"

    downstream = prereq_seam(db, collection, "Which courses does MATH 111 unlock?",
                             _seam_config(directions=("self", "downstream")))
    assert downstream.state == "active"
    downstream_neighbors = [c for c in downstream.candidates
                            if abs(c.raw_score - 0.8) < 1e-9]
    assert downstream_neighbors
    details = [db.execute(queries.get("cited_chunk_details"), {
        "chunk_id": candidate.chunk_id, "workspace_key": None,
    }).fetchone() for candidate in downstream_neighbors]
    assert any("MATH 111 or 112" in detail["text"] for detail in details)

    forward = prereq_seam(db, collection, "What are prerequisites for MATH 111?",
                          _seam_config(directions=("self", "downstream")))
    assert forward.state == "active"
    assert not [c for c in forward.candidates if abs(c.raw_score - 0.8) < 1e-9]


def test_prereq_seam_uses_only_parsed_tree_edges_and_keeps_own_prose(db, collection, tmp_path):
    records = [
        {"code": "ECON 101", "title": "Principles"},
        {"code": "MATH 111", "title": "Calculus I"},
        {"code": "MATH 112", "title": "Calculus II"},
        {"code": "ECON 350", "title": "Topics",
         "requisites": "ECON 101 and a love of graphs"},
        {"code": "ECON 302", "title": "Methods",
         "requisites": "ECON 101 and MATH 111 or MATH 112"},
    ]
    source_id = _import(db, collection, tmp_path,
                        "\n".join(json.dumps(record) for record in records))
    rows = db.execute(queries.get("list_courses_for_source"), {
        "source_id": source_id,
    }).fetchall()
    by_code = {row["code"]: row for row in rows}
    assert by_code["ECON 350"]["parse_status"] == "unparsed"
    assert by_code["ECON 302"]["parse_status"] == "ambiguous"

    upstream = _seam_config(directions=("upstream",))
    downstream = _seam_config(directions=("downstream",))
    for code in ("ECON 350", "ECON 302"):
        result = prereq_seam(db, collection, code, upstream)
        assert result.state == "dormant"
        mentions = db.execute(queries.get("list_mentions_for_course"), {
            "course_id": by_code[code]["course_id"],
        }).fetchall()
        assert any(mention["target_code"] == "ECON 101" for mention in mentions)

    for code in ("ECON 101", "MATH 111", "MATH 112"):
        result = prereq_seam(db, collection, code, downstream)
        assert result.state == "dormant"

    # Removing uncertain graph edges must not suppress the raw course evidence.
    self_result = prereq_seam(db, collection, "ECON 302",
                              _seam_config(directions=("self",)))
    assert self_result.state == "active"
    assert all(candidate.source_id == source_id for candidate in self_result.candidates)


def test_canonical_code_map_uses_only_explicit_relations():
    courses = [
        AtlasCourse(code="AAS 325", title="Black Love", crosslisted=["REL 325"]),
        AtlasCourse(code="REL 325", title="Black Love", crosslisted=["AAS 325"]),
        AtlasCourse(code="REL 330", title="Black Love"),
    ]
    mapping = canonical_code_map(courses)
    assert mapping["AAS 325"] == mapping["REL 325"] == "AAS 325"
    # A similar title with no relation stays its own course.
    assert mapping["REL 330"] == "REL 330"


def test_canonical_identity_survives_a_missing_alias():
    courses = [AtlasCourse(code="REL 340", title="Jewish Law", crosslisted=["JS 340"])]
    assert canonical_code_map(courses)["REL 340"] == "JS 340"


def _embed_off():
    config = load_retrieval_config()
    return replace(
        config, seams=replace(config.seams, embed=replace(config.seams.embed, enabled=False))
    )


def test_cross_listed_aliases_count_once_with_a_combined_label(db, collection, tmp_path):
    text = "\n".join(json.dumps(record) for record in [
        {"code": "AAS 325", "title": "Black Love", "description": "Religion and race.",
         "crosslisted": ["REL 325"]},
        {"code": "REL 325", "title": "Black Love", "description": "Religion and race.",
         "crosslisted": ["AAS 325"]},
        {"code": "MATH 112", "title": "Calculus II", "description": "Series and limits."},
    ])
    _import(db, collection, tmp_path, text)
    outcome = retrieve(
        db, collection_id=collection, query="religion race",
        embedder=FakeEmbedder(dim=8), embed_model=_MODEL, config=_embed_off(),
    )
    assert isinstance(outcome, Retrieved)
    love = [chunk for chunk in outcome.chunks if "Black Love" in chunk.text]
    assert len(love) == 1
    assert love[0].locator_label == "AAS 325 / REL 325"
