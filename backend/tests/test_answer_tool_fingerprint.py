from uuid import uuid4

from app.config import load_settings
from app.generation.answer_tools import answer_tool_context
from app.retrieval.types import CitedChunk


def _chunk():
    return CitedChunk(
        chunk_id=uuid4(), source_id=uuid4(), chunk_index=0,
        source_name="atlas.jsonl", locator_label="MATH 111",
        text="Evidence for MATH 111", seams=("keyword",),
        normalized_score=1.0, source_slot=1, rank=1,
    )


def test_answer_tool_fingerprint_includes_campus_equivalents():
    chunks = [_chunk()]
    courses = [{
        "code": "MATH 111", "title": "MATH 111", "citation_numbers": (1,),
        "parse_status": "none", "prerequisites": None, "corequisites": None,
        "requisites_raw": None, "requirements_evidence_complete": True,
        "blocker_reason": None,
    }]
    ctx = {"courses": courses, "topic_groups": []}
    settings = load_settings()
    common = dict(
        query="q", chunks=chunks, workspace_key="w", settings=settings,
        advising_context=ctx,
    )
    without = answer_tool_context(**common, campus_equivalents=None)[2]
    with_eq = answer_tool_context(
        **common, campus_equivalents={"MATH 111": frozenset({"MATH_OX 111"})},
    )[2]
    assert without != with_eq
