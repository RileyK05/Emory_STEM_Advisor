"""A passage must be read the way the answer names it.

The identity gate compares the code an answer names against the codes the
cited passage contains. Both sides have to be read the same way, or a correct
answer looks unsupported and is withheld.

The Atlas corpus spells subjects in ways a plain subject-word reader cannot
resolve. Measured on the development corpus, the gate could not find 14 of the
canonical codes its own passages named, and the live legacy path refused
three of six legitimate prerequisite questions because of it:

    "Math_OX 112 or Math_OX 112Z or Math 112 as prerequisites"
    "This course requires Psyc_ox 110, Psyc_ox 111 or equivalent ..."
    "This requisite mandates Phys_OX 151 as prerequisite."
    "Econ_OX 101, Econ 101 as prerequisite"
    "... BIOL 151or BIOL_OX141 ..."
    "Math 112_OX as corequisite"
    "... (BIOL 142 and BIOl 142L) ..."

The reader is the requirement parser's own scanner, so the gate and the graph
read the same identities.
"""

from __future__ import annotations

import pytest

from app.atlas.requisites import canonical_codes_in, parse_requisites
from app.generation.answer import unsupported_course_codes
from app.generation.research_final import DecisionRejected, validate_final
from tests.test_course_identity_guard import (
    DecisionBlock,
    _decision,
    _evidence,
)

_SUBJECTS = {"CS", "CS_OX", "MATH", "MATH_OX", "PHYS", "PHYS_OX", "BIOL",
             "PSYC", "PSYC_OX", "ECON", "ECON_OX"}


@pytest.mark.parametrize(("text", "expected"), [
    ("Math_OX 112 or Math_OX 112Z or Math 112 as prerequisites",
     ("MATH_OX 112", "MATH_OX 112Z", "MATH 112")),
    ("This course requires Psyc_ox 110, Psyc_ox 111 or equivalent transfer credit.",
     ("PSYC_OX 110", "PSYC_OX 111")),
    ("This requisite mandates Phys_OX 151 as prerequisite.", ("PHYS_OX 151",)),
    ("Econ_OX 101, Econ 101 as prerequisite", ("ECON_OX 101", "ECON 101")),
    ("This course requires BIOL 151or BIOL_OX141 or equivalent transfer credit.",
     ("BIOL 151", "BIOL_OX 141")),
    ("This course requires PHYS 461or equivalent transfer credit.",
     ("PHYS 461",)),
    ("Math 112_OX as corequisite", ("MATH_OX 112",)),
    ("[(BIOL 142 and BIOl 142L) or BIOL_OX142] and (BIOL 360 or NBB 301).",
     ("BIOL 142", "BIOL 142L", "BIOL_OX 142", "BIOL 360", "NBB 301")),
    ("Prerequisite: Mathematics 111.", ("MATH 111",)),
    ("Prerequisite: BIO 141 and BIO 141L.", ("BIOL 141", "BIOL 141L")),
    ("CS 171 or 171Z or CS_OX 171 or equivalent transfer credit.",
     ("CS 171", "CS 171Z", "CS_OX 171")),
    # A passage that lists codes carries the subject across the separator.
    ("CHEM 202Z + 202ZL; MATH 411-412; QTM 220.",
     ("CHEM 202Z", "CHEM 202ZL", "MATH 411", "MATH 412", "DATASCI 220")),
    # A level phrase still names the course it prints.
    ("GER 300 level courses-wildcard. Any 300 level GER or GER_OX courses.",
     ("GER 300",)),
])
def test_the_reader_resolves_the_spellings_the_corpus_uses(text, expected):
    """A passage's codes are read the way the course master spells them."""
    assert canonical_codes_in(text) == expected


@pytest.mark.parametrize("text", [
    "A student may change courses within the first two weeks of the semester.",
    "Ordinarily, no more than 6 credit hours may be double counted.",
    "The four-plus-one program is pursued in stages.",
    "This section is reserved for students in the BBA program.",
])
def test_the_reader_invents_no_course(text):
    """Prose that merely contains a number names no course."""
    assert canonical_codes_in(text) == ()


def test_the_reader_finds_every_code_the_parser_finds():
    """The reader may see more than the parser; never less.

    The parser removes enrollment exclusions and requirement structure before
    it scans, so it sees fewer codes than the text holds. The reader resolves
    identities wherever they appear, so it must find at least what the
    parser found.
    """
    from app.config import load_settings
    from app.db.connection import open_connection

    settings = load_settings()
    with open_connection(settings) as conn:
        rows = conn.execute(
            "select code, requisites_raw from courses "
            "where requisites_raw is not null and btrim(requisites_raw) <> '' "
            "order by code").fetchall()

    missing = []
    for row in rows:
        text = row["requisites_raw"] or ""
        parsed = {m.code for m in parse_requisites(text, course_code=row["code"]).mentions}
        absent = parsed - set(canonical_codes_in(text))
        if absent:
            missing.append((row["code"], sorted(absent)))
    assert missing == [], missing[:10]


def test_the_reader_finds_every_code_the_previous_reader_found():
    """The reader must not lose what the gate used to find.

    A code the reader misses makes a correct answer look unsupported, so the
    new reader has to find at least everything the retrieval seam's query
    reader found.
    """
    from app.config import load_settings
    from app.db.connection import open_connection
    from app.retrieval.seams.prereq import course_codes_in_query

    settings = load_settings()
    with open_connection(settings) as conn:
        rows = conn.execute(
            "select code, requisites_raw from courses "
            "where requisites_raw is not null and btrim(requisites_raw) <> '' "
            "order by code").fetchall()

    lost = []
    for row in rows:
        text = row["requisites_raw"] or ""
        absent = set(course_codes_in_query(text)) - set(canonical_codes_in(text))
        if absent:
            lost.append((row["code"], sorted(absent)))
    assert lost == [], lost[:10]


@pytest.mark.parametrize(("passage", "code"), [
    ("This course requires Psyc_ox 110, Psyc_ox 111 or equivalent transfer credit.",
     "PSYC_OX 110"),
    ("This requisite mandates Phys_OX 151 as prerequisite.", "PHYS_OX 151"),
    ("This course requires PHYS 461or equivalent transfer credit.", "PHYS 461"),
    ("This course requires BIOL 141 and BIOL 141L or BIOL 151or BIOL_OX141.",
     "BIOL 151"),
    ("Math_OX 112 or Math_OX 112Z or Math 112 as prerequisites", "MATH_OX 112"),
    ("Econ_OX 101, Econ 101 as prerequisite", "ECON_OX 101"),
])
def test_the_gate_accepts_an_answer_naming_a_code_the_passage_spells_oddly(
        passage, code):
    """A correct answer is not withheld because the passage spells it oddly.

    The passage really names the course; the answer names it the way the course
    master does. The gate must see that they are the same identity.
    """
    answer = f"{code} is a prerequisite for this course."
    assert unsupported_course_codes(answer, passage, subjects=_SUBJECTS) == ()


def test_the_research_gate_accepts_the_same_answer():
    """The research path reads the passage the same way the legacy path does."""
    passage = "This course requires Psyc_ox 110, Psyc_ox 111 or equivalent."
    record = _evidence()[0]
    support = type(record)(
        **{**record.model_dump(),
           "exact_text": passage, "locator_label": "PSYC_OX 210"})
    decision = _decision([
        DecisionBlock(kind="fact",
                      text="PSYC_OX 110 is a prerequisite for PSYC_OX 210.",
                      evidence_ids=("e1",)),
    ])
    validate_final(decision, None, [support], {"e1"}, {})


def test_the_gate_still_rejects_a_code_the_passage_does_not_name():
    """Widening the rule must not weaken it."""
    passage = "This course requires Psyc_ox 110, Psyc_ox 111 or equivalent."
    answer = "MATH 221 is a prerequisite for this course."
    assert unsupported_course_codes(answer, passage, subjects=_SUBJECTS) == ("MATH 221",)


@pytest.mark.parametrize("clause", [
    # The phrasings a model actually produces when it reports that a course is
    # absent. Each names a course the cited passage does not contain, and each
    # says so rather than asserting the course exists.
    'neither "CS 495" nor "Senior Seminar" appears anywhere in the stated requirements',
    "MATH 211, MATH 212, and MATH 250 are not part of the CS BS math requirement",
    "MATH 211 and MATH 212 are not part of the CS BS math requirement.",
    "There is no MATH 212 in the requirement list.",
    "CS 495 does not appear in the catalog requirements.",
    "CS 495 is absent from the captured catalog.",
    "No course numbered CS 495 appears in the captured catalog.",
    ("No course-level description for CS 495 was read, so its characterization "
     "here is only the name you supplied."),
    "The CS 170 record was not captured, so its prerequisites are unknown here.",
])
def test_an_absence_clause_may_name_the_absent_course(clause):
    """A clause that reports a course is absent may name it.

    The identity gate asks whether the cited passage contains the course the
    answer names. A clause that says the passage does not establish the course
    is the one case where naming it is the point.
    """
    assert unsupported_course_codes(clause, "CS 170 is required.", subjects=_SUBJECTS) == ()


def test_a_coverage_gap_may_not_name_courses_the_run_never_heard_of():
    """Saying a course was not read does not license naming it.

    Emory's catalog has no CS_OX 325. An answer that offers it as an example
    of a group of unread courses tells a student a course exists.
    """
    clause = ("Coverage gaps: the Oxford-campus counterparts of the Group A "
              "courses (for example CS_OX 325, CS_OX 428) were not read, so "
              "whether any of them accepts CS_OX 253 is unverified here.")
    missing = unsupported_course_codes(clause, "CS 170 is required.", subjects=_SUBJECTS)
    assert "CS_OX 325" in missing
    assert "CS_OX 428" in missing


@pytest.mark.parametrize("clause", [
    "CS 253 does not require MATH 111.",
    "MATH 231 is required for the Computer Science BS.",
])
def test_a_requirement_claim_may_not_use_the_absence_escape(clause):
    """"Not required" is a claim about a requirement, not about presence.

    The escape hatch covers statements that a course is absent from a source.
    A clause that denies a requirement asserts something the gate cannot let
    through unchecked.
    """
    assert unsupported_course_codes(clause, "CS 170 is required.", subjects=_SUBJECTS) != ()


def test_the_research_gate_still_rejects_a_code_the_evidence_does_not_name():
    passage = "This course requires Psyc_ox 110, Psyc_ox 111 or equivalent."
    record = _evidence()[0]
    support = type(record)(
        **{**record.model_dump(),
           "exact_text": passage, "locator_label": "PSYC_OX 210"})
    decision = _decision([
        DecisionBlock(kind="fact",
                      text="MATH 221 is a prerequisite for PSYC_OX 210.",
                      evidence_ids=("e1",)),
    ])
    with pytest.raises(DecisionRejected):
        validate_final(decision, None, [support], {"e1"}, {})


def test_the_research_gate_accepts_a_course_the_run_read_elsewhere():
    """A block may name a course the run established, not only one it cites.

    The identity question is about the run's evidence, not one block's
    citations. A block can cite a narrow passage while naming a course the
    run read earlier - a course record the model quoted two blocks ago, say.
    """
    record = _evidence()[0]
    course_record = type(record)(
        **{**record.model_dump(), "evidence_id": "e2",
           "exact_text": "CS 170: Introduction to Computer Science I.",
           "locator_label": "CS 170"})
    decision = _decision([
        DecisionBlock(kind="limitation",
                      text="The CS 170 course description was not read in this run.",
                      evidence_ids=("e1",)),
    ])
    validate_final(decision, None, [record, course_record], {"e1", "e2"}, {})


def test_the_research_gate_accepts_a_real_course_the_run_never_read():
    """A course Emory offers may be named even if the run did not read it.

    Existence and coverage are different questions. The course master is the
    authority on existence; a run that never read a course's record may still
    say so, and the answer is honest.
    """
    decision = _decision([
        DecisionBlock(kind="limitation",
                      text="MATH_OX 221 was not read and is not treated as "
                           "interchangeable with MATH 221.",
                      evidence_ids=("e1",)),
    ])
    validate_final(decision, None, _evidence(), {"e1"}, {},
                   known_course_codes=frozenset({"MATH_OX 221", "MATH 221"}))


def test_the_research_gate_rejects_a_course_that_does_not_exist():
    """A course in no master and in no passage is not a course at all.

    Emory's catalog has no CS_OX 325. An answer that names it as an example
    of a group of unread courses tells a student a course exists.
    """
    decision = _decision([
        DecisionBlock(kind="limitation",
                      text="The Oxford counterparts (for example CS_OX 325) "
                           "were not read.",
                      evidence_ids=("e1",)),
    ])
    with pytest.raises(DecisionRejected):
        validate_final(decision, None, _evidence(), {"e1"}, {},
                       known_course_codes=frozenset({"CS 253", "CS 171"}))
