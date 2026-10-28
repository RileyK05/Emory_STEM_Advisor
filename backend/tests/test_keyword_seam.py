"""Keyword seam tests against a real Postgres. No models load."""

import json

import pytest

from app.retrieval.config import KeywordSeamConfig
from app.retrieval.seams.keyword import keyword_seam
from tests.helpers import ingest_text


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
