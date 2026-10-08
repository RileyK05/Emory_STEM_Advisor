"""Atlas import + prerequisite seam tests against a real Postgres. No models."""

import pytest

from app.atlas.importer import ATLAS_CONTENT_TYPE, import_atlas
from app.db import queries
from app.retrieval.config import PrereqSeamConfig, load_retrieval_config
from app.retrieval.seams.prereq import prereq_seam
from tests.fakes import FakeEmbedder, whitespace_tokens

pytestmark = pytest.mark.db

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


def _import(db, collection, tmp_path, text=_SAMPLE):
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
    )


def _seam_config(**overrides):
    base = {
        "enabled": True,
        "decay": 0.8,
        "directions": ("self", "upstream"),
        "limit": 40,
    }
    base.update(overrides)
    return PrereqSeamConfig(**base)


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


def test_tree_nodes_written_for_parsed_course(db, collection, tmp_path):
    _import(db, collection, tmp_path)
    course = db.execute(
        queries.get("get_course_by_code"),
        {"collection_id": collection, "code": "BUSA 210"},
    ).fetchone()
    nodes = db.execute(
        queries.get("list_nodes_for_course"), {"course_id": course["course_id"]}
    ).fetchall()
    types = [node["node_type"] for node in nodes]
    assert "any" in types
    assert any(node["condition_text"] == "equivalent" for node in nodes)


def test_reimport_replaces_old_atlas(db, collection, tmp_path):
    _import(db, collection, tmp_path)
    first_count = db.execute(
        "SELECT COUNT(*) AS n FROM courses WHERE collection_id = %(c)s",
        {"c": collection},
    ).fetchone()["n"]
    smaller = '{"code": "ECON 101", "title": "Macro", "description": "x", ' \
        '"requisites": null}'
    _import(db, collection, tmp_path, text=smaller)
    rows = db.execute(
        queries.get("list_atlas_sources"),
        {"collection_id": collection, "content_type": ATLAS_CONTENT_TYPE},
    ).fetchall()
    assert len(rows) == 1
    second_count = db.execute(
        "SELECT COUNT(*) AS n FROM courses WHERE collection_id = %(c)s",
        {"c": collection},
    ).fetchone()["n"]
    assert first_count > second_count == 1


def test_prereq_seam_self_and_upstream(db, collection, tmp_path):
    _import(db, collection, tmp_path)
    result = prereq_seam(db, collection, "ECON 301", _seam_config())
    assert result.state == "active"
    self_chunks = [c for c in result.candidates if c.raw_score == 1.0]
    upstream = [c for c in result.candidates if abs(c.raw_score - 0.8) < 1e-9]
    assert self_chunks
    assert upstream


def test_prereq_seam_lowercase_code_matches(db, collection, tmp_path):
    _import(db, collection, tmp_path)
    result = prereq_seam(db, collection, "what do I need for econ301?", _seam_config())
    assert result.state == "active"


def test_prereq_seam_no_codes_is_dormant(db, collection, tmp_path):
    _import(db, collection, tmp_path)
    result = prereq_seam(db, collection, "what is a matrix?", _seam_config())
    assert result.state == "dormant"


def test_prereq_seam_downstream_direction(db, collection, tmp_path):
    _import(db, collection, tmp_path)
    result = prereq_seam(
        db,
        collection,
        "MATH 111",
        _seam_config(directions=("self", "downstream")),
    )
    assert result.state == "active"
    downstream = [c for c in result.candidates if abs(c.raw_score - 0.8) < 1e-9]
    assert downstream
