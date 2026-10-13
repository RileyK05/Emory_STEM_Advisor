"""The deterministic requisites parser (exact outputs), and the prereq seam's code finder."""

import pytest

from app.atlas.requisites import (
    AllOf,
    AnyOf,
    Condition,
    CourseRef,
    parse_requisites,
    requisite_text,
)
from app.retrieval.seams.prereq import _codes


def _c(*codes):
    return tuple(CourseRef(code) for code in codes)


# (text, expected prerequisite tree, expected mention codes in order)
_PARSED = {
    "single": ("Prerequisite: ECON 101.", CourseRef("ECON 101"), ["ECON 101"]),
    "carry-forward or": ("MATH 111 or 112", AnyOf(_c("MATH 111", "MATH 112")),
                         ["MATH 111", "MATH 112"]),
    "and": ("ECON 101 and MATH 111", AllOf(_c("ECON 101", "MATH 111")), None),
    "oxford comma list": ("ECON 101, ECON 112, and MATH 111",
                          AllOf(_c("ECON 101", "ECON 112", "MATH 111")),
                          ["ECON 101", "ECON 112", "MATH 111"]),
    "parentheses disambiguate": ("ECON 101 and (MATH 111 or MATH 112)",
                                 AllOf((CourseRef("ECON 101"), AnyOf(_c("MATH 111", "MATH 112")))),
                                 None),
    "grade phrase stripped": ("ECON 101 with a grade of C or better", CourseRef("ECON 101"), None),
    "permission of instructor": ("ECON 101 or permission of instructor",
                                 AnyOf((CourseRef("ECON 101"),
                                        Condition("permission of instructor"))), None),
    "or equivalent": ("ECON 101 or equivalent",
                      AnyOf((CourseRef("ECON 101"), Condition("equivalent"))), None),
    "slash means or": ("MATH 111/112", AnyOf(_c("MATH 111", "MATH 112")),
                       ["MATH 111", "MATH 112"]),
    "comma-or carry-forward": ("MATH 111, 112, or 115",
                               AnyOf(_c("MATH 111", "MATH 112", "MATH 115")),
                               ["MATH 111", "MATH 112", "MATH 115"]),
    "comma-and carry-forward": ("MATH 111, 112 and 115",
                                AllOf(_c("MATH 111", "MATH 112", "MATH 115")),
                                ["MATH 111", "MATH 112", "MATH 115"]),
    "standing keeps the year": ("Junior standing", Condition("junior standing"), None),
    "standing or permission": ("Senior standing or permission of instructor",
                               AnyOf((Condition("senior standing"),
                                      Condition("permission of instructor"))), None),
    "long subject and two-letter suffix": ("DATASCI 220 and AAS 490RW",
                                           AllOf(_c("DATASCI 220", "AAS 490RW")), None),
}


@pytest.mark.parametrize(("text", "tree", "codes"), _PARSED.values(), ids=_PARSED.keys())
def test_parsed_requisites(text, tree, codes):
    result = parse_requisites(text)
    assert result.status == "parsed"
    assert result.trees["prerequisite"] == tree
    if codes is not None:
        assert [m.code for m in result.mentions] == codes


@pytest.mark.parametrize(("text", "codes"), [
    ("ECON 101, ECON 112", ["ECON 101", "ECON 112"]),  # list with no conjunction
    ("ECON 101 and MATH 111 or MATH 112", ["ECON 101", "MATH 111", "MATH 112"]),  # mixed
])
def test_ambiguous_requisites_keep_mentions_but_build_no_tree(text, codes):
    result = parse_requisites(text)
    assert result.status == "ambiguous"
    assert result.trees == {}
    assert [m.code for m in result.mentions] == codes


def test_unparsed_keeps_mentions_and_leftover():
    result = parse_requisites("ECON 101 and a love of graphs")
    assert result.status == "unparsed"
    assert result.trees == {}
    assert [m.code for m in result.mentions] == ["ECON 101"]
    assert result.leftover


def test_no_requisites():
    result = parse_requisites(None)
    assert (result.status, result.mentions, result.trees) == ("none", (), {})


def test_prerequisite_and_corequisite_labels():
    result = parse_requisites("Prerequisite: ECON 101. Corequisite: MATH 211.")
    assert result.status == "parsed"
    assert result.trees == {"prerequisite": CourseRef("ECON 101"),
                            "corequisite": CourseRef("MATH 211")}
    assert {(m.kind, m.code) for m in result.mentions} == {
        ("prerequisite", "ECON 101"), ("corequisite", "MATH 211")}


def test_description_requirements_are_extracted_only_when_explicit():
    text = requisite_text(None, "Algorithm analysis. Pre-requisite: CS 120. "
                          "Open only to approved honors candidates.")
    assert text == "Pre-requisite: CS 120. Open only to approved honors candidates."
    assert parse_requisites(text).status == "unparsed"  # the restriction is never dropped
    assert requisite_text("CS 110", "Prerequisite: CS 120.") == "CS 110"
    assert requisite_text(None, "Students learn how prerequisite graphs work.") is None

    labelled = parse_requisites(
        requisite_text(None, "Overview. Pre-requisite: CS 120. Co requisite: MATH 111."))
    assert labelled.status == "parsed"
    assert labelled.trees == {"prerequisite": CourseRef("CS 120"),
                              "corequisite": CourseRef("MATH 111")}


def test_ordinary_long_words_before_numbers_are_not_subjects():
    assert all(m.code != "COMPLETE 101" for m in parse_requisites("Complete 101 credits").mentions)


@pytest.mark.parametrize(("query", "codes"), [
    ("MATH 111 and 112", ["MATH 111", "MATH 112"]),  # "and" is not a subject; subject carries
    ("Physics 141", []),  # "141" has no valid subject before it
    ("what do I need for econ301?", ["ECON 301"]),
    ("section 111 applies", []),  # a bare number is ignored
    ("MATH 111 and MATH 111 or ECON 101", ["MATH 111", "ECON 101"]),  # deduped, in order
    ("Can I take DATASCI 355 before AAS 490RW?", ["DATASCI 355", "AAS 490RW"]),
])
def test_prereq_seam_finds_course_codes_in_a_query(query, codes):
    assert _codes(query) == codes
