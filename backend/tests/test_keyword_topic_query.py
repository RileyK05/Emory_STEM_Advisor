"""Topical keyword evidence must beat conversational catalog boilerplate."""

from dataclasses import replace

from app.retrieval.config import load_retrieval_config
from app.retrieval.seams.keyword import _tokens, keyword_seam
from tests.helpers import ingest_text


def test_query_scaffolding_does_not_hide_topic_or_course_codes():
    assert _tokens("I want to explore biotechnology. Recommend catalog courses.", 2) == [
        "to", "biotechnology",
    ]
    assert _tokens("What prerequisites are listed for MATH 112 and CS 171?", 2) == [
        "prerequisites", "are", "for", "math", "112", "and", "cs", "171",
    ]
    assert _tokens("section 1.8.2", 2) == ["section", "1", "8", "2"]


def test_topical_course_not_crowded_out_by_generic_policy(db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "policy.txt", "catalog courses current " * 30)
    ingest_text(db, collection, tmp_path, "lab.txt", "Biotechnology: gene cloning and cell culture.")
    config = replace(load_retrieval_config().seams.keyword, limit=1)
    result = keyword_seam(db, collection,
        "I want to explore biotechnology. Recommend current catalog courses.", config)
    assert len(result.candidates) == 1
    # The selected chunk actually contains the topic, rather than repeated instructions.
    from app.db import queries

    details = db.execute(queries.get("cited_chunk_details_many"), {
        "chunk_ids": [result.candidates[0].chunk_id], "workspace_key": None,
    }).fetchone()
    assert "Biotechnology" in details["text"]
