"""Database-backed tests for request-bound advising catalog context."""

from uuid import uuid4

import pytest

from app.advising.catalog import _requirements_are_cited, _restore_tree, load_advising_context
from app.advising.sequence import CourseRequirement, plan_sequence
from app.atlas.requisites import AllOf, CourseRef
from app.db import queries
from app.retrieval.types import CitedChunk

ATLAS_TYPE = "application/x-course-atlas+jsonl"


def _add_source(db, collection_id, *, filename, current=True):
    source_id = uuid4()
    db.execute(queries.get("upsert_source"), {
        "source_id": source_id,
        "collection_id": collection_id,
        "workspace_key": None,
        "filename": filename,
        "content_type": ATLAS_TYPE,
        "file_hash": str(uuid4()),
        "raw_path": "test-only",
        "origin_key": None,
    })
    db.execute(
        "UPDATE sources SET status = 'indexed', is_current = %(current)s "
        "WHERE source_id = %(source_id)s",
        {"source_id": source_id, "current": current},
    )
    return source_id


def _add_course(db, collection_id, source_id, code, *, label, parse_status="none", raw=None):
    locator_id, course_id = uuid4(), uuid4()
    ordinal = db.execute(
        "SELECT count(*) AS count FROM locators WHERE source_id = %(source_id)s",
        {"source_id": source_id},
    ).fetchone()["count"]
    db.execute(queries.get("insert_locator"), {
        "locator_id": locator_id,
        "source_id": source_id,
        "ordinal": ordinal,
        "locator_type": "course",
        "label": label,
        "start_char": 0,
        "end_char": 100,
    })
    db.execute(queries.get("insert_course"), {
        "course_id": course_id,
        "collection_id": collection_id,
        "source_id": source_id,
        "locator_id": locator_id,
        "code": code,
        "subject": code.split()[0],
        "title": f"Title for {code}",
        "requisites_raw": raw,
        "parse_status": parse_status,
        "parser_version": "3",
        "canonical_code": code,
    })
    return course_id


def _cited(source_id, label, *, slot=1, index=0, text="citation evidence", chunk_id=None):
    return CitedChunk(
        chunk_id=chunk_id or uuid4(), source_id=source_id, chunk_index=index,
        source_name="atlas.jsonl", locator_label=label, text=text,
        seams=("keyword",), normalized_score=1.0, source_slot=slot, rank=index + 1,
    )


def test_cross_listed_alias_label_still_resolves_the_course(db, collection):
    """Relabelling a citation must not hide the course it points at.

    Retrieval rewrites a cited cross-listed course's display label to all of its
    official codes ("CHEM 150 / CHEM 150X") while the locator keeps the single
    code. Scoping on that label dropped the course and reported
    `requirements_evidence_missing`; identity through the cited chunk wins.
    """
    source_id = _add_source(db, collection, filename="atlas.jsonl")
    course_id = _add_course(
        db, collection, source_id, "CHEM 150", label="CHEM 150",
        parse_status="parsed", raw="MATH 111",
    )
    locator_id = db.execute(
        "SELECT locator_id FROM courses WHERE course_id = %(course_id)s",
        {"course_id": course_id},
    ).fetchone()["locator_id"]
    chunk_id = uuid4()
    db.execute(
        "INSERT INTO chunks (chunk_id, source_id, locator_id, chunk_index,"
        " start_char, end_char, token_count, text)"
        " VALUES (%(chunk_id)s, %(source_id)s, %(locator_id)s, 0, 0, 10, 2, %(text)s)",
        {"chunk_id": chunk_id, "source_id": source_id, "locator_id": locator_id,
         "text": "MATH 111"},
    )
    db.commit()

    courses = load_advising_context(
        db, collection_id=collection,
        chunks=[_cited(source_id, "CHEM 150 / CHEM 150X", text="MATH 111",
                       chunk_id=chunk_id)],
    )["courses"]
    assert [course["code"] for course in courses] == ["CHEM 150"]
    assert courses[0]["requirements_evidence_complete"] is True


def test_context_is_exactly_cited_current_atlas_scope_and_restores_graph(db, collection):
    source_id = _add_source(db, collection, filename="atlas.jsonl")
    historical_id = _add_source(db, collection, filename="old-atlas.jsonl", current=False)
    other_collection = uuid4()
    db.execute(queries.get("insert_collection"), {
        "collection_id": other_collection, "name": f"other-{other_collection}",
    })
    foreign_id = _add_source(db, other_collection, filename="foreign-atlas.jsonl")

    grounded_id = _add_course(
        db, collection, source_id, "CHEM 150", label="CHEM 150: Foundations",
        parse_status="parsed", raw="MATH 111 and MATH 112",
    )
    uncited_id = _add_course(
        db, collection, source_id, "CHEM 151", label="CHEM 151: Lab",
    )
    _add_course(db, collection, historical_id, "CHEM 152", label="CHEM 152")
    _add_course(db, other_collection, foreign_id, "CHEM 153", label="CHEM 153")

    node_ids = [uuid4(), uuid4(), uuid4()]
    for node_id, parent_id, position, kind, target_code in (
        (node_ids[0], None, 0, "all", None),
        (node_ids[1], node_ids[0], 0, "course", "MATH 111"),
        (node_ids[2], node_ids[0], 1, "course", "MATH 112"),
    ):
        db.execute(queries.get("insert_prereq_node"), {
            "node_id": node_id, "course_id": grounded_id, "kind": "prerequisite",
            "parent_node_id": parent_id, "position": position, "node_type": kind,
            "target_code": target_code, "condition_text": None,
        })

    build_id, root_id, department_id, topic_id = (uuid4() for _ in range(4))
    db.execute(queries.get("insert_sim_build"), {
        "build_id": build_id, "collection_id": collection, "model": "test",
        "config_version": 1, "nudges_hash": "test", "content_hash": None,
    })
    for cluster_id, parent_id, level, label, kind, department_key, size in (
        (root_id, None, 0, "All", "root", None, 2),
        (department_id, root_id, 1, "Chemistry", "department", "chemistry", 2),
        (topic_id, department_id, 2, "Foundations", "topic", "chemistry", 2),
    ):
        db.execute(queries.get("insert_sim_cluster"), {
            "cluster_id": cluster_id, "build_id": build_id,
            "parent_cluster_id": parent_id, "level": level, "label": label,
            "medoid_course_id": grounded_id, "medoid_code": None, "medoid_title": None,
            "centroid": "[1,0]", "size": size,
            "kind": kind, "department_key": department_key,
        })
        db.execute(queries.get("insert_sim_cluster_member"), {
            "cluster_id": cluster_id, "course_id": grounded_id, "code": None, "title": None,
        })
        if cluster_id != root_id:
            db.execute(queries.get("insert_sim_cluster_member"), {
                "cluster_id": cluster_id, "course_id": uncited_id, "code": None, "title": None,
            })
    db.execute(queries.get("set_current_build"), {"build_id": build_id})
    db.commit()

    context = load_advising_context(
        db, collection_id=collection,
        chunks=[_cited(source_id, "CHEM 150: Foundations", slot=1,
                       text="MATH 111 and MATH 112"),
                _cited(source_id, "CHEM 150: Foundations", slot=1, index=1,
                       text="course description"),
                _cited(source_id, "CHEM 151: Lab"),
                _cited(historical_id, "CHEM 152"),
                _cited(foreign_id, "CHEM 153")],
    )

    assert [item["code"] for item in context["courses"]] == ["CHEM 150", "CHEM 151"]
    course = context["courses"][0]
    assert course["citation_numbers"] == (1, 2)
    assert course["prerequisites"] == AllOf((CourseRef("MATH 111"), CourseRef("MATH 112")))
    assert course["trees_valid"] is True
    assert course["parse_status"] == course["stored_parse_status"] == "parsed"
    assert len(context["topic_groups"]) == 1
    topic = context["topic_groups"][0]
    assert topic["path"] == ("Chemistry", "Foundations")
    assert topic["course_codes"] == ("CHEM 150", "CHEM 151")
    assert topic["total_size"] == 2
    assert topic["evidence"] == (
        {"course_code": "CHEM 150", "citation_numbers": (1, 2)},
        {"course_code": "CHEM 151", "citation_numbers": (3,)},
    )


def test_missing_stored_tree_blocks_parsed_requisite_claim(db, collection):
    source_id = _add_source(db, collection, filename="atlas.jsonl")
    _add_course(
        db, collection, source_id, "BIOL 100", label="BIOL 100",
        parse_status="parsed", raw="CHEM 150",
    )
    db.commit()

    course = load_advising_context(
        db, collection_id=collection,
        chunks=[_cited(source_id, "BIOL 100", text="CHEM 150")],
    )["courses"][0]
    assert course["prerequisites"] is None
    assert course["trees_valid"] is False
    assert course["parse_status"] == "unparsed"
    assert course["stored_parse_status"] == "parsed"


def test_description_only_citation_cannot_support_stored_prerequisite_or_ready_plan(db, collection):
    source_id = _add_source(db, collection, filename="atlas.jsonl")
    course_id = _add_course(
        db, collection, source_id, "BIOL 100", label="BIOL 100",
        parse_status="parsed", raw="Prerequisite: CHEM 150",
    )
    db.execute(queries.get("insert_prereq_node"), {
        "node_id": uuid4(), "course_id": course_id, "kind": "prerequisite",
        "parent_node_id": None, "position": 0, "node_type": "course",
        "target_code": "CHEM 150", "condition_text": None,
    })
    db.commit()

    course = load_advising_context(
        db, collection_id=collection,
        chunks=[_cited(source_id, "BIOL 100", text="BIOL 100 is an introduction to biology.")],
    )["courses"][0]

    assert course["requirements_evidence_complete"] is False
    assert course["blocker_reason"] == "requirements_evidence_missing"
    assert course["parse_status"] == "unparsed"
    assert course["stored_parse_status"] == "parsed"
    assert course["prerequisites"] is None
    assert course["corequisites"] is None
    assert course["requisites_raw"] is None

    plan = plan_sequence({
        course["code"]: CourseRequirement(
            code=course["code"], title=course["title"], parse_status=course["parse_status"],
            prerequisites=course["prerequisites"], corequisites=course["corequisites"],
            requisites_raw=course["requisites_raw"],
        ),
    }, [course["code"]])
    assert plan.stages == ()
    assert any(blocker.reason == "requirements_unparsed" for blocker in plan.blockers)


def test_single_course_department_is_returned_without_a_learned_topic_cluster(db, collection):
    source_id = _add_source(db, collection, filename="atlas.jsonl")
    course_id = _add_course(db, collection, source_id, "MATH 100", label="MATH 100")
    build_id, root_id, department_id = uuid4(), uuid4(), uuid4()
    db.execute(queries.get("insert_sim_build"), {
        "build_id": build_id, "collection_id": collection, "model": "test",
        "config_version": 1, "nudges_hash": "test", "content_hash": None,
    })
    for cluster_id, parent_id, level, label, kind, department_key in (
        (root_id, None, 0, "All courses", "root", None),
        (department_id, root_id, 1, "Mathematics", "department", "MATH"),
    ):
        db.execute(queries.get("insert_sim_cluster"), {
            "cluster_id": cluster_id, "build_id": build_id,
            "parent_cluster_id": parent_id, "level": level, "label": label,
            "medoid_course_id": course_id, "medoid_code": None, "medoid_title": None,
            "centroid": "[1,0]", "size": 1,
            "kind": kind, "department_key": department_key,
        })
        db.execute(queries.get("insert_sim_cluster_member"), {
            "cluster_id": cluster_id, "course_id": course_id, "code": None, "title": None,
        })
    db.execute(queries.get("set_current_build"), {"build_id": build_id})
    db.commit()

    context = load_advising_context(
        db, collection_id=collection,
        chunks=[_cited(source_id, "MATH 100", text="MATH 100 is calculus.")],
    )

    assert context["topic_groups"] == [{
        "path": ("Mathematics",),
        "cluster_id": department_id,
        "course_codes": ("MATH 100",),
        "total_size": 1,
        "evidence": ({"course_code": "MATH 100", "citation_numbers": (1,)},),
    }]


def test_empty_citation_scope_performs_no_database_reads():
    class NoReads:
        def execute(self, *_args, **_kwargs):
            raise AssertionError("empty citation scope must not read the database")

    assert load_advising_context(NoReads(), collection_id=uuid4(), chunks=[]) == {
        "courses": [], "topic_groups": [],
    }


def _cited_chunk(index: int, text: str) -> CitedChunk:
    return CitedChunk(uuid4(), uuid4(), index, "atlas.jsonl", "MATH 100", text,
                      ("keyword",), 1.0, 1, index + 1)


def test_completed_requisite_run_survives_a_later_ambiguous_index():
    raw = "MATH 111 and MATH 112"
    chunks = [_cited_chunk(0, "MATH 111"), _cited_chunk(1, "and MATH 112")]
    assert _requirements_are_cited(raw, chunks) is True
    # An ambiguous duplicate index after a gap must not discard the earlier run.
    chunks += [_cited_chunk(3, "unrelated A"), _cited_chunk(3, "unrelated B")]
    assert _requirements_are_cited(raw, chunks) is True


def test_tree_restore_rejects_excess_size_depth_and_unknown_kind():
    with pytest.raises(ValueError, match="supported size"):
        _restore_tree([{}] * 2_001)

    deep_rows = []
    parent_id = None
    for index in range(65):
        node_id = uuid4()
        deep_rows.append({
            "node_id": node_id, "kind": "prerequisite", "parent_node_id": parent_id,
            "position": 0, "node_type": "all", "target_code": None,
            "condition_text": None,
        })
        deep_rows.append({
            "node_id": uuid4(), "kind": "prerequisite", "parent_node_id": node_id,
            "position": 1, "node_type": "course", "target_code": "MATH 111",
            "condition_text": None,
        })
        parent_id = node_id
    deep_rows.append({
        "node_id": uuid4(), "kind": "prerequisite", "parent_node_id": parent_id,
        "position": 0, "node_type": "course", "target_code": "MATH 112",
        "condition_text": None,
    })
    with pytest.raises(ValueError, match="supported depth"):
        _restore_tree(deep_rows)

    unknown_kind = [{
        "node_id": uuid4(), "kind": "unexpected", "parent_node_id": None,
        "position": 0, "node_type": "course", "target_code": "MATH 111",
        "condition_text": None,
    }]
    with pytest.raises(ValueError, match="unknown or mixed node kind"):
        _restore_tree(unknown_kind)
