from uuid import uuid4

import pytest

from app.generation.claim_review import validate_model_review


def _context(text="The course requires MATH 111."):
    chunk_id, source_id = str(uuid4()), str(uuid4())
    return chunk_id, {chunk_id: {
        "chunk_id": chunk_id, "source_id": source_id, "source_name": "catalog.txt",
        "locator_label": "page 1", "text": text,
    }}


def _segment(answer, chunk_id, excerpt, *, assessment="supported"):
    return {"start": 0, "end": len(answer), "text": answer, "kind": "claim",
            "assessment": assessment, "confidence": 0.8,
            "evidence": [{"chunkId": chunk_id, "start": 0, "end": len(excerpt),
                          "excerpt": excerpt}]}


def test_model_assessment_never_becomes_human_verification():
    answer = "Students need MATH 111."
    chunk_id, context = _context()
    units = validate_model_review(answer, context, {"segments": [
        _segment(answer, chunk_id, context[chunk_id]["text"]),
    ]})
    assert units[0]["model_assessment"] == "supported"
    assert "human_verified" not in units[0]
    assert units[0]["evidence_spans"][0]["excerpt"] == context[chunk_id]["text"]


def test_compound_claim_is_withheld_as_unreviewed_gap():
    answer = "MATH 111 is required and CHEM 150 is required."
    chunk_id, context = _context("MATH 111 is required; CHEM 150 is required.")
    units = validate_model_review(answer, context, {"segments": [
        _segment(answer, chunk_id, context[chunk_id]["text"]),
    ]})
    assert units[0]["coverage_kind"] == "unreviewed_gap"
    assert units[0]["model_assessment"] == "unreviewed"


def test_incomplete_offsets_and_fabricated_quotes_fail_closed():
    answer = "Students need MATH 111."
    chunk_id, context = _context()
    invalid = _segment(answer, chunk_id, "MATH 111 is offered.")
    units = validate_model_review(answer, context, {"segments": [invalid]})
    assert units[0]["coverage_kind"] == "unreviewed_gap"
    assert units[0]["_invalid"] is True


def test_explicit_abstention_is_kept_as_model_observation():
    answer = "The course requires MATH 111."
    units = validate_model_review(answer, {}, {"segments": [{
        "start": 0, "end": len(answer), "text": answer, "kind": "claim",
        "assessment": "abstained", "confidence": None, "evidence": [],
    }]})
    assert units[0]["model_assessment"] == "abstained"


def _seg(answer, start, end, chunk_id, *, assessment="supported", confidence=0.8,
         evidence=True, kind="claim"):
    item = {"start": start, "end": end, "text": answer[start:end], "kind": kind,
            "assessment": assessment, "confidence": confidence, "evidence": []}
    if kind == "claim" and evidence:
        item["evidence"] = [{"chunkId": chunk_id, "start": start, "end": end,
                             "excerpt": answer[start:end]}]
    return item


def _gap(units) -> bool:
    return bool(units) and units[0].get("_invalid") is True


def test_exact_multisegment_review_keeps_every_valid_unit():
    answer = "First claim. Second claim."
    chunk_id, context = _context(answer)
    units = validate_model_review(answer, context, {"segments": [
        _seg(answer, 0, 12, chunk_id),
        _seg(answer, 13, len(answer), chunk_id, assessment="contradicted"),
    ]})
    assert not _gap(units)
    assert [unit["answer_text"] for unit in units] == ["First claim.", "Second claim."]
    assert [unit["model_assessment"] for unit in units] == ["supported", "contradicted"]


def test_review_covering_a_substantive_gap_fails_closed():
    answer = "First claim. Second claim."
    chunk_id, context = _context(answer)
    # The middle word "Second" is substantive and left uncovered.
    units = validate_model_review(answer, context, {"segments": [
        _seg(answer, 0, 4, chunk_id),
        _seg(answer, 11, 13, chunk_id),
    ]})
    assert _gap(units)


@pytest.mark.parametrize("bad", [
    {"assessment": "supported", "evidence": False},      # supported needs evidence
    {"assessment": "conflicting_evidence"},              # needs two distinct chunks
    {"confidence": 1.5},
    {"confidence": True},
])
def test_invalid_assessment_invariants_fail_closed(bad):
    answer = "The course requires MATH 111."
    chunk_id, context = _context(answer)
    segment = _seg(answer, 0, len(answer), chunk_id, **bad)
    if bad.get("assessment") == "conflicting_evidence":
        segment["evidence"] = [{"chunkId": chunk_id, "start": 0, "end": len(answer),
                                "excerpt": answer}]
    units = validate_model_review(answer, context, {"segments": [segment]})
    assert _gap(units)


def test_unreviewed_gap_must_not_carry_evidence_or_confidence():
    answer = "The course requires MATH 111."
    units = validate_model_review(answer, {}, {"segments": [
        _seg(answer, 0, len(answer), "unused", kind="unreviewed_gap",
             assessment="unreviewed", confidence=0.5),
    ]})
    assert _gap(units)


def test_review_exceeding_the_unit_cap_fails_closed():
    answer = "a" * 33
    chunk_id, context = _context(answer)
    units = validate_model_review(answer, context, {"segments": [
        _seg(answer, index, index + 1, chunk_id) for index in range(33)
    ]})
    assert _gap(units)


def test_out_of_order_segments_fail_closed():
    answer = "First claim. Second claim."
    chunk_id, context = _context(answer)
    units = validate_model_review(answer, context, {"segments": [
        _seg(answer, 13, len(answer), chunk_id),
        _seg(answer, 0, 12, chunk_id),
    ]})
    assert _gap(units)
