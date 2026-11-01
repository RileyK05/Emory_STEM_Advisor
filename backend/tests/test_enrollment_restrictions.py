"""Enrollment restrictions are not course requirements (parser v19).

An Atlas record mixes three different things: who may enrol, which courses are
required, and notes about a particular section. Only the middle one is a
prerequisite. The parser must drop the first and the third without ever
dropping a requirement that happens to sit next to an enrolment sentence, and
without inventing an edge from a sentence that names no course.

These cases are deliberately adversarial in both directions: an enrolment
sentence that must be removed, a requirement that must survive next to one,
and text that states no requirement at all.
"""

from __future__ import annotations

import pytest

from app.atlas.requisites import parse_requisites, split_requisite_segments


def _codes(text: str) -> set[str]:
    return {m.code for m in parse_requisites(text).mentions}


def _tree(text: str):
    parsed = parse_requisites(text)
    return parsed.trees.get("prerequisite")


@pytest.mark.parametrize("text", [
    "This section is reserved for students in the BBA program.",
    "Varies by section",
    "HPMMPH or HPMMPHOL students only.",
    "MB program students",
    "Program restrictions apply",
    "Only students who have permission to enroll may take this course.",
    "Students cannot enroll in this course without an override.",
    "This section is reserved for students in the Evening MBA program.",
    "This section is reserved for students in the One-Year or Two-Year MBA program.",
])
def test_enrollment_restriction_states_no_requirement(text):
    """Who may enrol is not a prerequisite: no segment, no mention, no tree."""
    parsed = parse_requisites(text)
    assert parsed.status == "none", (text, parsed.status)
    assert parsed.mentions == ()
    assert parsed.trees == {}
    assert split_requisite_segments(text) == ()


@pytest.mark.parametrize(("text", "code"), [
    ("Prerequisite: PHIL 300 or permission of instructor. HPMMPH students only.", "PHIL 300"),
    ("Students enrolled in the NDBU program not allowed to enroll in this course. "
     + "Prerequisite: CS 170.",
     "CS 170"),
    ("Prerequisite: CS 253. This section is reserved for students in the MS program.",
     "CS 253"),
])
def test_a_requirement_next_to_an_enrolment_sentence_survives(text, code):
    """Dropping the enrolment sentence must not take the requirement with it."""
    parsed = parse_requisites(text)
    assert parsed.status == "parsed", (text, parsed.status, parsed.leftover)
    assert code in _codes(text)
    assert _tree(text) is not None


def test_a_sentence_containing_a_digit_is_never_removed():
    """A digit may mark a course number, so the whole sentence is kept.

    The enrolment rule only removes text it can prove is about enrolment, and a
    digit makes that unsafe: "BBA 2" could be a course.
    """
    text = "This section is reserved for students in the BBA 2 program."
    parsed = parse_requisites(text)
    assert parsed.status == "unparsed"
    assert parsed.leftover, "the digit-bearing sentence should survive as leftover"


def test_a_code_bearing_enrolment_sentence_keeps_its_codes():
    """Codes in a sentence that also says "students only" are still mentions.

    The sentence is not removed (it has digits), so the codes are extracted even
    though the leftover text keeps the record out of the parsed set.
    """
    text = "BIO 300, BIOS 301 students only."
    parsed = parse_requisites(text)
    assert parsed.status == "unparsed"
    assert _codes(text) == {"BIOL 300", "BIOS 301"}


@pytest.mark.parametrize("text", [
    "Two PHIL courses are required.",
    "Prerequisite: approval of adviser and the director of undergraduate studies.",
    "Prerequisite: consent of instructor.",
])
def text_without_a_course_code_stays_unresolved(text):
    """A requirement naming no course needs a person; it is never a tree."""
    parsed = parse_requisites(text)
    assert parsed.status == "unparsed"
    assert parsed.trees == {}


def test_a_condition_only_requirement_is_a_condition():
    """Permission to enrol is a condition, and it parses as one."""
    text = "Prerequisite: permission of instructor."
    parsed = parse_requisites(text)
    assert parsed.status == "parsed"
    assert parsed.mentions == ()
    assert _tree(text) is not None
    assert "permission of instructor" in str(_tree(text))


def test_a_real_requirement_is_unaffected_by_the_enrolment_rules():
    """The ordinary case still parses exactly as before."""
    parsed = parse_requisites("Prerequisite: CS 170 or CS_OX 170.")
    assert parsed.status == "parsed"
    assert _codes("Prerequisite: CS 170 or CS_OX 170.") == {"CS 170", "CS_OX 170"}


def test_the_enrolment_rule_does_not_reach_across_a_sentence_boundary():
    """"students only" inside a requirement sentence is not an enrolment rule.

    "BIOS 301 students only" names a course; removing it would lose the course.
    """
    parsed = parse_requisites("Prerequisite: BIOS 301 and BIOS 302.")
    assert parsed.status == "parsed"
    assert _codes("Prerequisite: BIOS 301 and BIOS 302.") == {"BIOS 301", "BIOS 302"}


@pytest.mark.parametrize("text", [
    # An earlier removal leaves a space in front of this sentence.
    ("Students enrolled in the NDBU program not allowed to enroll in this course. "
     "This section is reserved for students in the BBA program."),
    # The catalog runs two enrolment statements together without punctuation.
    "Program restrictions apply This section is reserved for students in the BBA program.",
    "BBA or SPECSTUBUS students This section is reserved for students in the BBA program.",
    "Program restrictions apply: BBA or LIBAS students.",
])
def test_an_enrolment_statement_is_removed_wherever_it_stands(text):
    """Enrolment wording is recognised even when it is not a whole sentence.

    An earlier removal leaves a space in front of the next statement, and the
    catalog runs two enrolment statements together without punctuation. Neither
    may survive as a requirement the planner then refuses to plan around.
    """
    parsed = parse_requisites(text)
    assert parsed.status == "none", (parsed.status, parsed.leftover)
    assert split_requisite_segments(text) == ()


def test_text_that_states_no_requirement_is_not_a_parsed_requirement():
    """A course with only enrolment wording has no requirement at all.

    Reporting "parsed" for text that produced no requirement would tell the
    planner that the course's requirement text was read and understood.
    """
    parsed = parse_requisites("This section is reserved for students in the BBA program.")
    assert parsed.status == "none"
    assert parsed.trees == {}
    assert parsed.mentions == ()


@pytest.mark.parametrize(("text", "code"), [
    ("Prerequisite: MATH 111. Only BSHEMPH and BSHEMPHOL have permission to enroll "
     + "in this class.", "MATH 111"),
    ("Prerequisite: MATH 111. This section is reserved for students in the BBA program.",
     "MATH 111"),
    ("Prerequisite: MATH 111. Program restrictions apply to BBA students.", "MATH 111"),
    ("Prerequisite: MATH 111. BBA or SPECSTUBUS students may enroll.", "MATH 111"),
    ("Prerequisite: MATH 111. HPMMPH or HPMMPHOL students only.", "MATH 111"),
])
def test_an_enrolment_sentence_never_takes_a_requirement_with_it(text, code):
    """Removing the enrolment sentence must not remove the requirement.

    The enrolment rules work on the sentence, not on the whole blob: a
    requirement that names a course survives whatever follows it.
    """
    parsed = parse_requisites(text)
    assert parsed.status == "parsed", (parsed.status, parsed.leftover)
    assert code in _codes(text)
    assert _tree(text) is not None
