"""Keyword seam tests against a real Postgres. No models load."""

import json
from dataclasses import replace

import pytest

from app.db import queries
from app.retrieval.config import KeywordSeamConfig, load_retrieval_config
from app.retrieval.seams.keyword import (
    _course_code_header_pattern,
    _tokens,
    expand_program_names,
    keyword_seam,
)
from tests.helpers import ingest_text
from tests.test_atlas_import import _import as import_atlas_sample


def _config(**overrides):
    base = {"enabled": True, "limit": 40, "min_token_len": 2}
    base.update(overrides)
    return KeywordSeamConfig(**base)


def test_any_single_word_matches(db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "a.txt", "alpha beta\n")
    ingest_text(db, collection, tmp_path, "b.txt", "gamma delta\n")
    result = keyword_seam(db, collection, "beta delta", _config())
    sources = {c.source_id for c in result.candidates}
    assert len(sources) == 2


@pytest.mark.parametrize(
    "query",
    ["f(x) = x^2", "a <-> b & !c", "''", "((", ":*", "!!!", "a & | b"],
)
def test_hostile_queries_do_not_error(db, collection, tmp_path, query):
    ingest_text(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    result = keyword_seam(db, collection, query, _config())
    assert result.state in {"active", "dormant"}


def test_no_searchable_terms_is_dormant(db, collection):
    result = keyword_seam(db, collection, "!  @  #", _config())
    assert result.state == "dormant"
    assert result.reason == "no searchable terms"


def test_disabled_in_config(db, collection):
    result = keyword_seam(db, collection, "alpha", _config(enabled=False))
    assert result.state == "disabled"


def test_failed_source_chunks_never_appear(db, collection, tmp_path):
    source_id = ingest_text(db, collection, tmp_path, "a.txt", "alpha beta gamma\n")
    db.execute("UPDATE sources SET status = 'failed' WHERE source_id = %(s)s", {"s": source_id})
    db.commit()
    result = keyword_seam(db, collection, "alpha", _config())
    assert result.candidates == []


def test_ordering_is_deterministic(db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "a.txt", "alpha beta\n")
    ingest_text(db, collection, tmp_path, "b.txt", "alpha gamma\n")
    first = keyword_seam(db, collection, "alpha", _config())
    second = keyword_seam(db, collection, "alpha", _config())
    assert [(c.source_id, c.chunk_index) for c in first.candidates] == [
        (c.source_id, c.chunk_index) for c in second.candidates
    ]
    # Within one source, chunks are ordered by chunk_index ascending.
    per_source: dict = {}
    for cand in first.candidates:
        per_source.setdefault(cand.source_id, []).append(cand.chunk_index)
    for indices in per_source.values():
        assert indices == sorted(indices)


def test_min_token_len_respected(db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "a.txt", "alpha to beta\n")
    result = keyword_seam(db, collection, "to", _config(min_token_len=3))
    assert result.state == "dormant"


def test_continuation_chunk_matches_its_section_heading(db, collection, tmp_path):
    from uuid import uuid4

    from app.db import queries

    source_id = ingest_text(db, collection, tmp_path, "policy.md", "Body without the section words.\n")
    locator_id, chunk_id = uuid4(), uuid4()
    db.execute(queries.get("insert_locator"), {
        "locator_id": locator_id, "source_id": source_id, "ordinal": 1,
        "locator_type": "section", "label": "Advanced Placement Policy",
        "start_char": 0, "end_char": 10,
    })
    db.execute(queries.get("insert_chunk"), {
        "chunk_id": chunk_id, "source_id": source_id, "locator_id": locator_id,
        "chunk_index": 99, "start_char": 0, "end_char": 10, "token_count": 2,
        "text": "Body without the section words.",
    })
    db.commit()
    result = keyword_seam(db, collection, "advanced placement policy", _config())
    assert chunk_id in {candidate.chunk_id for candidate in result.candidates}


def test_bound_program_evidence_is_returned_for_a_program_question(db, collection, tmp_path):
    from app.programs.importer import import_programs
    from app.programs.requirements import parse_program, to_record
    from tests.test_programs import CS_BS, _ingest_catalog_file

    identity = _ingest_catalog_file(db, collection, tmp_path, "Computer Science (BS)", CS_BS)
    record = to_record(parse_program("Computer Science (BS)", CS_BS),
                       catalog="Test Catalog", source_url="https://example.test/catalog.pdf",
                       first_page=3, last_page=4)
    record["source"] = identity
    path = tmp_path / "programs.jsonl"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    assert import_programs(db, collection_id=collection, path=path)["bound"] == 1

    result = keyword_seam(db, collection, "Computer Science BS requirements",
                          _config(limit=40))
    assert any(candidate.program_evidence for candidate in result.candidates)


def test_a_hidden_chunk_is_skipped_but_kept(db, collection, tmp_path):
    source = ingest_text(db, collection, tmp_path, "a.txt", "alpha beta\n")
    db.execute("UPDATE chunks SET search_hidden = true WHERE source_id = %s", (source,))
    assert keyword_seam(db, collection, "alpha", _config()).candidates == []
    assert db.execute("SELECT count(*) AS n FROM chunks WHERE source_id = %s",
                      (source,)).fetchone()["n"] == 1


@pytest.mark.parametrize("query,expected", [
    ("math major requirements", "mathematics major requirements"),
    ("econ and math joint major", "economics and mathematics joint major"),
    ("what is math 111 about", "what is math 111 about"),
    ("can i double major", "can i double major two majors"),
])
def test_program_nicknames_expand_only_in_program_questions(query, expected):
    assert expand_program_names(query) == expected


def test_the_subject_outranks_program_vocabulary(db, collection, tmp_path):
    wordy = ingest_text(db, collection, tmp_path, "wordy.txt",
                        "major requirements and more major requirements of history\n")
    subject = ingest_text(db, collection, tmp_path, "subject.txt",
                          "A chemistry student reads the major.\n")
    result = keyword_seam(db, collection, "chemistry major requirements", _config())
    assert [c.source_id for c in result.candidates] == [subject, wordy]


def test_a_course_named_by_its_title_comes_first(db, collection, tmp_path):
    import_atlas_sample(db, collection, tmp_path)
    ingest_text(db, collection, tmp_path, "notes.txt",
                "principles macroeconomics principles macroeconomics notes\n")
    result = keyword_seam(db, collection, "principles of macroeconomics class", _config())
    first = db.execute("SELECT co.code FROM courses co JOIN chunks c ON c.locator_id = co.locator_id "
                       "WHERE c.chunk_id = %s", (result.candidates[0].chunk_id,)).fetchone()
    assert first["code"] == "ECON 101"


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
