"""Atlas import + prerequisite seam tests against a real Postgres. No models."""

import hashlib
import json
from dataclasses import replace

import pytest

from app.atlas.importer import ATLAS_CONTENT_TYPE, import_atlas, refresh_atlas_requisites
from app.atlas.records import AtlasCourse, canonical_code_map
from app.db import queries
from app.retrieval.config import PrereqSeamConfig, load_retrieval_config
from app.retrieval.retrieve import retrieve
from app.retrieval.seams.prereq import prereq_seam
from app.retrieval.types import Retrieved
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
               "parser_version = '3' WHERE course_id = %(c)s", {"c": course["course_id"]})
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
    old_source = db.execute(
        "SELECT is_current FROM sources WHERE source_id=%s", (first_source,)
    ).fetchone()
    assert old_source["is_current"] is False
    assert db.execute("SELECT count(*) AS n FROM sources WHERE collection_id=%s AND content_type=%s",
                      (collection, ATLAS_CONTENT_TYPE)).fetchone()["n"] == 2
    assert db.execute(
        queries.get("list_chunks_for_source"), {"source_id": first_source}
    ).fetchall() == first_chunks


def test_failed_atlas_replacement_preserves_active_graph(db, collection, tmp_path):
    first_source = _import(db, collection, tmp_path)

    class BrokenEmbedder:
        def embed_documents(self, _texts):
            raise RuntimeError("atlas staging failed")

    path = tmp_path / "new-atlas.jsonl"
    path.write_text('{"code":"ECON 101","title":"Changed","requisites":null}',
                    encoding="utf-8")
    with pytest.raises(RuntimeError, match="atlas staging failed"):
        import_atlas(db, collection_id=collection, path=path,
                     embedder=BrokenEmbedder(), embed_model=_MODEL,
                     count_tokens=whitespace_tokens, config=load_retrieval_config())
    assert db.execute("SELECT is_current FROM sources WHERE source_id=%s", (first_source,)).fetchone()[
        "is_current"
    ] is True
    course = db.execute(queries.get("get_course_by_code"),
                        {"collection_id": collection, "code": "ECON 101"}).fetchone()
    assert course["title"] == "Principles of Macroeconomics"


def test_prereq_seam_directions_codes_and_dormancy(db, collection, tmp_path):
    _import(db, collection, tmp_path)
    result = prereq_seam(db, collection, "ECON 301", _seam_config())
    assert result.state == "active"
    assert [c for c in result.candidates if c.raw_score == 1.0]  # the asked course itself
    assert [c for c in result.candidates if abs(c.raw_score - 0.8) < 1e-9]  # upstream

    lowercase = prereq_seam(db, collection, "what do I need for econ301?", _seam_config())
    assert lowercase.state == "active"
    assert prereq_seam(db, collection, "what is a matrix?", _seam_config()).state == "dormant"

    downstream = prereq_seam(db, collection, "MATH 111",
                             _seam_config(directions=("self", "downstream")))
    assert downstream.state == "active"
    assert [c for c in downstream.candidates if abs(c.raw_score - 0.8) < 1e-9]


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
