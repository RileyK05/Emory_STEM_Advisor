"""The parser test table from the plan (Phase 4), exact outputs."""

from app.atlas.requisites import (
    AllOf,
    AnyOf,
    Condition,
    CourseRef,
    parse_requisites,
)


def _prereq(result):
    return result.trees.get("prerequisite")


def test_none():
    result = parse_requisites(None)
    assert result.status == "none"
    assert result.mentions == ()
    assert result.trees == {}


def test_single_prerequisite():
    result = parse_requisites("Prerequisite: ECON 101.")
    assert result.status == "parsed"
    assert _prereq(result) == CourseRef("ECON 101")
    assert [m.code for m in result.mentions] == ["ECON 101"]


def test_subject_carry_forward_or():
    result = parse_requisites("MATH 111 or 112")
    assert result.status == "parsed"
    assert _prereq(result) == AnyOf((CourseRef("MATH 111"), CourseRef("MATH 112")))
    assert [m.code for m in result.mentions] == ["MATH 111", "MATH 112"]


def test_and():
    result = parse_requisites("ECON 101 and MATH 111")
    assert result.status == "parsed"
    assert _prereq(result) == AllOf((CourseRef("ECON 101"), CourseRef("MATH 111")))


def test_oxford_comma_list():
    result = parse_requisites("ECON 101, ECON 112, and MATH 111")
    assert result.status == "parsed"
    assert _prereq(result) == AllOf(
        (CourseRef("ECON 101"), CourseRef("ECON 112"), CourseRef("MATH 111"))
    )
    assert [m.code for m in result.mentions] == ["ECON 101", "ECON 112", "MATH 111"]


def test_comma_list_without_conjunction_is_ambiguous():
    result = parse_requisites("ECON 101, ECON 112")
    assert result.status == "ambiguous"
    assert result.trees == {}
    assert [m.code for m in result.mentions] == ["ECON 101", "ECON 112"]


def test_mixed_and_or_is_ambiguous():
    result = parse_requisites("ECON 101 and MATH 111 or MATH 112")
    assert result.status == "ambiguous"
    assert result.trees == {}
    assert [m.code for m in result.mentions] == ["ECON 101", "MATH 111", "MATH 112"]


def test_parentheses_disambiguate():
    result = parse_requisites("ECON 101 and (MATH 111 or MATH 112)")
    assert result.status == "parsed"
    assert _prereq(result) == AllOf(
        (CourseRef("ECON 101"), AnyOf((CourseRef("MATH 111"), CourseRef("MATH 112"))))
    )


def test_grade_phrase_stripped():
    result = parse_requisites("ECON 101 with a grade of C or better")
    assert result.status == "parsed"
    assert _prereq(result) == CourseRef("ECON 101")


def test_condition_permission_of_instructor():
    result = parse_requisites("ECON 101 or permission of instructor")
    assert result.status == "parsed"
    assert _prereq(result) == AnyOf(
        (CourseRef("ECON 101"), Condition("permission of instructor"))
    )


def test_condition_equivalent():
    result = parse_requisites("ECON 101 or equivalent")
    assert result.status == "parsed"
    assert _prereq(result) == AnyOf((CourseRef("ECON 101"), Condition("equivalent")))


def test_prerequisite_and_corequisite_labels():
    result = parse_requisites("Prerequisite: ECON 101. Corequisite: MATH 211.")
    assert result.status == "parsed"
    assert result.trees["prerequisite"] == CourseRef("ECON 101")
    assert result.trees["corequisite"] == CourseRef("MATH 211")
    assert {(m.kind, m.code) for m in result.mentions} == {
        ("prerequisite", "ECON 101"),
        ("corequisite", "MATH 211"),
    }


def test_unparsed_keeps_mentions():
    result = parse_requisites("ECON 101 and a love of graphs")
    assert result.status == "unparsed"
    assert result.trees == {}
    assert [m.code for m in result.mentions] == ["ECON 101"]
    assert result.leftover


def test_slash_means_or():
    result = parse_requisites("MATH 111/112")
    assert result.status == "parsed"
    assert _prereq(result) == AnyOf((CourseRef("MATH 111"), CourseRef("MATH 112")))
    assert [m.code for m in result.mentions] == ["MATH 111", "MATH 112"]


def test_comma_or_carry_forward():
    result = parse_requisites("MATH 111, 112, or 115")
    assert result.status == "parsed"
    assert _prereq(result) == AnyOf(
        (CourseRef("MATH 111"), CourseRef("MATH 112"), CourseRef("MATH 115"))
    )
    assert [m.code for m in result.mentions] == ["MATH 111", "MATH 112", "MATH 115"]


def test_comma_and_carry_forward():
    result = parse_requisites("MATH 111, 112 and 115")
    assert result.status == "parsed"
    assert _prereq(result) == AllOf(
        (CourseRef("MATH 111"), CourseRef("MATH 112"), CourseRef("MATH 115"))
    )
    assert [m.code for m in result.mentions] == ["MATH 111", "MATH 112", "MATH 115"]


def test_standing_condition_keeps_year():
    result = parse_requisites("Junior standing")
    assert result.status == "parsed"
    assert _prereq(result) == Condition("junior standing")
    result = parse_requisites("Senior standing or permission of instructor")
    assert _prereq(result) == AnyOf(
        (Condition("senior standing"), Condition("permission of instructor"))
    )
