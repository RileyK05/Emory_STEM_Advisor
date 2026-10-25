"""Topical keyword evidence must beat conversational catalog boilerplate."""

from dataclasses import replace

import pytest

from app.db import queries
from app.retrieval.config import load_retrieval_config
from app.retrieval.seams.keyword import _course_code_header_pattern, _tokens, keyword_seam
from tests.helpers import ingest_text


def test_query_scaffolding_does_not_hide_topic_or_course_codes():
    assert _tokens("I want to explore biotechnology. Recommend catalog courses.", 2) == [
        "biotechnology",
    ]
    assert _tokens("What prerequisites are listed for MATH 112 and CS 171?", 2) == [
        "prerequisites", "math", "112", "cs", "171",
    ]
    assert _tokens("section 1.8.2", 2) == ["section", "1", "8", "2"]


@pytest.mark.parametrize(("query", "expected"), [
    ("CS171", ["cs171", "cs", "171"]),
    ("CS_OX170", ["cs", "ox170", "ox", "170"]),
    ("CS_OX 170", ["cs", "ox", "170"]),
    ("PAE8100", ["pae8100", "pae", "8100"]),
    ("M2M401", ["m2m401", "m2m", "401"]),
    ("ARCH16", ["arch16", "arch", "16"]),
    ("TATT605BSHES", ["tatt605bshes", "tatt", "605bshes"]),
    ("BIOL_OX385RWE", ["biol", "ox385rwe", "ox", "385rwe"]),
])
def test_course_code_tokenization_keeps_joined_identity_and_oxford_marker(query, expected):
    assert _tokens(query, 2) == expected


def test_course_code_header_pattern_skips_terms_but_keeps_four_digit_course_codes():
    assert _course_code_header_pattern("FIN 201 registration restrictions Spring 2027") == (
        r"(^|\n)##[ \t]+(FIN[ \t]*201)(?![a-z0-9])"
    )
    assert _course_code_header_pattern("Spring 2027") is None
    assert _course_code_header_pattern("PAE2027") == (
        r"(^|\n)##[ \t]+(PAE[ \t]*2027)(?![a-z0-9])"
    )


@pytest.mark.parametrize(("query", "target_text", "near_match_text"), [
    ("CS171", "CS 171: Introduction to Computer Science II.",
     "CS 170: Introduction to Computer Science I."),
    ("CS_OX170", "CS_OX 170: Introduction to Computer Science at Oxford.",
     "CS 170: Introduction to Computer Science in Atlanta."),
    ("PAE8100", "PAE 8100: Advanced Physician Assistant Practice.",
     "PAE 800: Physician Assistant Practice."),
    ("M2M401", "M2M 401: Medicine to Management Seminar.",
     "M2M 402: Medicine to Management Capstone."),
])
def test_joined_course_code_finds_its_spaced_offering_identity(
    db, collection, tmp_path, query, target_text, near_match_text,
):
    ingest_text(db, collection, tmp_path, "target.txt", target_text)
    ingest_text(db, collection, tmp_path, "near-match.txt", near_match_text)
    config = replace(load_retrieval_config().seams.keyword, limit=500)
    result = keyword_seam(db, collection, query, config)

    assert result.candidates
    top = db.execute(
        queries.get("cited_chunk_details_many"),
        {"chunk_ids": [result.candidates[0].chunk_id], "workspace_key": None},
    ).fetchone()
    assert top["source_name"] == "target.txt"


def test_exact_course_code_outranks_repeated_term_boilerplate(db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "offering.md", "## FIN 201: Corporate Finance.\n")
    ingest_text(
        db, collection, tmp_path, "boilerplate.txt",
        "Registration restrictions apply for Spring 2027. " * 80
        + "FIN 201 is mentioned here, but the heading identifies another course. "
        + "## FIN 321 — Topics in Finance.",
    )
    config = replace(load_retrieval_config().seams.keyword, limit=5)
    result = keyword_seam(
        db, collection, "FIN 201 registration restrictions Spring 2027", config,
    )
    assert result.candidates
    top = db.execute(queries.get("cited_chunk_details_many"), {
        "chunk_ids": [result.candidates[0].chunk_id], "workspace_key": None,
    }).fetchone()
    assert top["source_name"] == "offering.md"


@pytest.mark.parametrize(("query", "target", "noise", "name"), [
    ("I want to explore biotechnology. Recommend current catalog courses.",
     "Biotechnology: gene cloning and cell culture.", "catalog courses current " * 30, "lab.txt"),
    ("What is the S/U credit cap? Do ARCHE credits count?",
     "ARCHE counts toward the limit of 20 credit hours of satisfactory/unsatisfactory courses.",
     "Credit is required for this degree. " * 30, "grading.txt"),
    ("My friend promised 12 credits always count in a 4+1 program automatically. Is that true?",
     "4+1 programs ordinarily permit no more than 6 credit hours to be double counted.",
     "Approved courses automatically count for this opportunity. " * 30, "programs.txt"),
    ("Which economics electives count for the Artificial Intelligence minor?",
     "ECON 315 and ECON 320 are approved electives for Artificial Intelligence.",
     "Economics of Artificial Intelligence describes market decisions. " * 30,
     "artificial-intelligence-minor.md"),
])
def test_topical_course_not_crowded_out_by_generic_policy(db, collection, tmp_path,
                                                       query, target, noise, name):
    ingest_text(db, collection, tmp_path, "policy.txt", noise)
    ingest_text(db, collection, tmp_path, name, target)
    config = replace(load_retrieval_config().seams.keyword, limit=1)
    result = keyword_seam(db, collection, query, config)
    assert len(result.candidates) == 1
    # The selected chunk actually contains the topic, rather than repeated instructions.
    from app.db import queries

    details = db.execute(queries.get("cited_chunk_details_many"), {
        "chunk_ids": [result.candidates[0].chunk_id], "workspace_key": None,
    }).fetchone()
    assert details["source_name"] == name


def test_repeated_generic_words_do_not_outrank_a_longer_topic(db, collection, tmp_path):
    # DA-03: "work", "first", and "fit" stay in the query. Repeating them must
    # not rank an unrelated policy above the longer topical token.
    ingest_text(
        db, collection, tmp_path, "policy.txt",
        "Incomplete work is the first fit for this policy. " * 40,
    )
    ingest_text(
        db, collection, tmp_path, "course.txt",
        "Robotics: sensors, control, and planning.",
    )
    config = replace(load_retrieval_config().seams.keyword, limit=5)
    result = keyword_seam(
        db, collection, "How does robotics work as a first fit?", config,
    )
    assert result.candidates
    from app.db import queries

    top = db.execute(queries.get("cited_chunk_details_many"), {
        "chunk_ids": [result.candidates[0].chunk_id], "workspace_key": None,
    }).fetchone()
    assert "Robotics" in top["text"]
    if len(result.candidates) > 1:
        assert result.candidates[0].raw_score > result.candidates[-1].raw_score
