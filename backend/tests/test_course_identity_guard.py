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
from app.generation.answer_checks.policy_facts import disclose_retired_courses
from app.generation.research_final import (
    DecisionRejected,
    validate_final,
)

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


@pytest.mark.parametrize(("text", "recommended", "rejected"), [
    ("CS 171 comes before CS 253.", (), True),
    # The next sentence is too late: a student can stop reading at the first.
    ("CS 171 comes before CS 253. It is no longer offered.", (), True),
    ("CS 253 accepts CS 171, which is no longer offered.", (), False),
    ("CS 171 was retired, so start from CS 253.", (), False),
    # A requisite's old alternative stays in the list, said to be unoffered.
    ("CS 253 accepts CS 171, which has not been offered since Fall 2023.", (), False),
    ("CS 253 accepts CS 171, which is no longer offered.", ("CS 171",), True),
])
def test_a_course_the_atlas_no_longer_shows_is_never_presented_as_current(
        text, recommended, rejected):
    decision = _decision([DecisionBlock(kind="fact", text=text, evidence_ids=("e1",))]).model_copy(
        update={"recommended_course_codes": recommended})
    known = frozenset({"CS 171", "CS 253"})
    check = lambda: validate_final(decision, None, _evidence(), {"e1"}, {},
                                   known_course_codes=known, retired_courses=frozenset({"CS 171"}))
    if rejected:
        with pytest.raises(DecisionRejected, match="no longer offered|not offered"):
            check()
    else:
        check()
    # The server writes the disclosure itself; only recommending the course is still refused.
    decision = disclose_retired_courses(decision, frozenset({"CS 171"}))
    if recommended:
        with pytest.raises(DecisionRejected, match="no longer offered"):
            check()
    else:
        check()
    if rejected and not recommended:
        assert "(CS 171 isn't on any recent Course Atlas schedule)." in decision.blocks[0].text


def test_a_retired_course_gets_its_full_label_once_per_answer():
    decision = disclose_retired_courses(_decision([
        DecisionBlock(kind="fact", text="CS 253 needs CS 171 or CS 170.", evidence_ids=("e1",)),
        DecisionBlock(kind="judgment", text="Finish CS 171 first. Then take CS 253.",
                      evidence_ids=("e1",)),
    ]), frozenset({"CS 171"}))
    assert [block.text for block in decision.blocks] == [
        "CS 253 needs CS 171 or CS 170 (CS 171 isn't on any recent Course Atlas schedule).",
        "Finish CS 171 first (not on recent schedules). Then take CS 253.",
    ]
    validate_final(decision, None, _evidence(), {"e1"}, {},
                   known_course_codes=frozenset({"CS 170", "CS 171", "CS 253"}),
                   retired_courses=frozenset({"CS 171"}))


@pytest.mark.parametrize(("title", "kind"), [
    ("Special Topics:African Studies", "topics"),
    ("Spec Topics:Dance & Mvmnt Studies", "topics"),
    ("Adv.Topics Luso-Afro-Braz.St.", "topics"),
    ("Topics in Chinese Studies", "topics"),
    ("Directed Study: Current Legal Topics", "independent"),
    ("Supervised Reading", "independent"),
    ("Individual Research in ENVS", "independent"),
    ("Research", "independent"),
    ("Honors", "honors"),
    ("Honors Seminar in French", "honors"),
    ("Internshp in Political Science", "internship"),
    ("Computer Science Practicum", "practicum"),
    ("Honor Council Practicum", "practicum"),
    ("Senior Seminar", "capstone"),
    ("Environmental Management Capstone", "capstone"),
    # Ordinary classes whose titles share a word with a special kind.
    ("Research in Health Economics", None),
    ("Research Methods in Sociology", None),
    ("Accounting Research I", None),
    ("International Trade", None),
])
def test_an_atlas_title_names_a_course_with_its_own_rules(title, kind):
    from app.atlas.designations import course_designation

    designation = course_designation(title)
    assert (designation.kind if designation else None) == kind


@pytest.mark.parametrize(("blocks", "caveated"), [
    ([("judgment", "Take SOC 497R in your junior year.")], [0]),
    # Already said in the answer's own words, or only asked about: no second caveat.
    ([("judgment", "Take SOC 497R once a faculty mentor agrees to supervise it.")], []),
    ([("question", "Do you want SOC 497R?")], []),
    # Once per course, after the first block that names it.
    ([("fact", "SOC 497R counts as an elective."), ("judgment", "Take SOC 497R.")], [0]),
])
def test_an_answer_naming_a_special_course_carries_its_caveat(blocks, caveated):
    from app.atlas.designations import course_designation
    from app.generation.answer_checks.policy_facts import disclose_special_courses

    decision = _decision([DecisionBlock(kind=kind, text=text, evidence_ids=("e1",))
                          for kind, text in blocks])
    disclosed = disclose_special_courses(
        decision, {"SOC 497R": course_designation("Undergraduate Research")})
    caveat = ("SOC 497R is an independent research or study course: you arrange it with a "
              "faculty mentor, usually with department permission.")
    assert [index for index, block in enumerate(disclosed.blocks)
            if block.text.endswith(caveat)] == caveated
