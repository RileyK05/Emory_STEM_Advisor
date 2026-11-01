"""A course identity the evidence does not contain must not reach a student.

Both answering paths guard against naming a course that the cited passages do
not contain. The legacy path checks every paragraph of the answer, whatever
kind of statement it is. The research path checks only some blocks: the code
against the cited evidence runs for ``fact`` blocks whose text contains
inclusion language, so a ``limitation`` block is never checked, and a ``fact``
block without that language is never checked either.

That gap is not theoretical. On the development corpus the research path
answered "Which courses does completing CS 253 unlock?" with a limitation
block reading:

    "Oxford-campus counterparts of the Group A courses (for example CS_OX 325,
     CS_OX 326, CS_OX 350, CS_OX 355, CS_OX 428) were not read, so whether any
     of them accepts CS_OX 253 ... is unverified in this evidence set."

The framing is honest - the model says it did not read them - but Emory's
catalog has exactly five CS_OX courses (170, 171, 224, 253, 297RE). The five
identities were inferred by symmetry from their Atlanta namesakes, and a
student reads five specific course codes that do not exist.

The legacy gate catches exactly this; the research gate does not.
"""

from __future__ import annotations

import pytest

from app.advising.research_types import (
    DecisionBlock,
    EvidenceRecord,
    FinalDecision,
)
from app.generation.answer import unsupported_course_codes
from app.generation.research_final import DecisionRejected, validate_final

_SUBJECTS = {"CS", "CS_OX", "MATH", "PHYS", "BIOL", "CHEM", "ENVS", "DATASCI"}

_LIMITATION = (
    "Coverage gaps: Oxford-campus counterparts of the Group A courses "
    "(for example CS_OX 325, CS_OX 326, CS_OX 350, CS_OX 355, CS_OX 428) "
    "were not read, so whether any of them accepts CS_OX 253 as an alternative "
    "to CS 253 is unverified in this evidence set."
)

_SUPPORT = (
    "CS 253: Data Structures and Algorithms. "
    "Requisites: CS 171 or 171Z or CS_OX 171 or equivalent transfer credit."
)


def _evidence() -> list[EvidenceRecord]:
    return [EvidenceRecord(
        evidence_id="e1", exact_text=_SUPPORT, source_id="s1", source_revision=1,
        source_kind="institutional_catalog",
        source_name="atlas-primary-spring-2027.jsonl",
        locator_label="CS 253", chunk_id="c1",
        text_digest="0" * 64,
        acquired_step=1, content_completeness="complete")]


def _decision(blocks) -> FinalDecision:
    return FinalDecision(
        blocks=tuple(blocks),
        outcome="information",
        completeness="partial",
        candidate_ids=(),
        recommended_course_codes=(),
        preparation_check_ids=(),
        preparation_claims=(),
        unresolved_questions=(),
    )


def test_the_legacy_gate_catches_a_code_the_evidence_does_not_contain():
    """The narrower path's rule, applied everywhere, would have caught it."""
    missing = unsupported_course_codes(_LIMITATION, _SUPPORT, subjects=_SUBJECTS)
    assert "CS_OX 325" in missing
    assert "CS_OX 428" in missing


def test_a_fact_block_naming_an_absent_code_is_rejected():
    """The rule that does exist: a fact that names an unread course fails."""
    decision = _decision([
        DecisionBlock(kind="fact", text="CS_OX 325 is a required course.",
                      evidence_ids=("e1",)),
    ])
    with pytest.raises(DecisionRejected):
        validate_final(decision, None, _evidence(), {"e1"}, {})


def test_a_limitation_naming_an_absent_code_is_rejected():
    """A limitation may name what was not read, but not invent identities.

    The codes are offered as examples of what was not checked, which is a
    reasonable thing to say. Emory's catalog has no CS_OX 325, and naming it
    tells a student a course exists.
    """
    decision = _decision([
        DecisionBlock(kind="limitation", text=_LIMITATION, evidence_ids=("e1",)),
    ])
    with pytest.raises(DecisionRejected):
        validate_final(decision, None, _evidence(), {"e1"}, {})


def test_a_fact_listing_courses_without_inclusion_language_is_checked():
    """A fact block is checked whatever words it uses.

    The block states which courses name CS 253. It does not need the word
    "required" for that to be a claim about course identity.
    """
    decision = _decision([
        DecisionBlock(
            kind="fact",
            text=("Group A - courses whose requisites name CS 253 directly: "
                  "CS 325, CS_OX 325, CS_OX 326."),
            evidence_ids=("e1",)),
    ])
    with pytest.raises(DecisionRejected):
        validate_final(decision, None, _evidence(), {"e1"}, {})


def test_a_limitation_saying_the_evidence_lacks_a_code_is_allowed():
    """A clause that reports the gap may name the code it is missing.

    This is the honest form of the limitation: the answer says the evidence
    does not establish something about a course. The legacy path allows the
    same, and the research path must not punish honesty.
    """
    decision = _decision([
        DecisionBlock(
            kind="limitation",
            text=("The cited passages do not name CS 325, so its requirements "
                  "are unverified here."),
            evidence_ids=("e1",)),
    ])
    validate_final(decision, None, _evidence(), {"e1"}, {})

