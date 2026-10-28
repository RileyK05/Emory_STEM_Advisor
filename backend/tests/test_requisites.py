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
from app.retrieval.seams.prereq import course_codes_in_query


def _c(*codes):
    return tuple(CourseRef(code) for code in codes)


# (text, expected prerequisite tree, expected mention codes in order)
_PARSED = {
    "four-digit Atlas code": ("PAE 8100", CourseRef("PAE 8100"), ["PAE 8100"]),
    "short Atlas code": ("ARCH 16", CourseRef("ARCH 16"), ["ARCH 16"]),
    "long Oxford suffix": ("BIOL_OX 385RWE", CourseRef("BIOL_OX 385RWE"),
                           ["BIOL_OX 385RWE"]),
    "alphanumeric Atlas code": ("M2M 401 and VM 999D1",
                                AllOf(_c("M2M 401", "VM 999D1")), None),
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
    "transfer credit tail is a modifier": (
        ("(CHEM 150 or CHEM 150Z) and (CHEM 202 or CHEM 202Z) or equivalent transfer credit "
         "as a prerequisite"),
        AnyOf((AllOf((AnyOf(_c("CHEM 150", "CHEM 150Z")),
                      AnyOf(_c("CHEM 202", "CHEM 202Z")))),
               Condition("equivalent transfer credit"))),
        ["CHEM 150", "CHEM 150Z", "CHEM 202", "CHEM 202Z"]),
    "transfer credit explicit and": (
        "(CHEM 150 or CHEM 150Z) and equivalent transfer credit as a prerequisite",
        AllOf((AnyOf(_c("CHEM 150", "CHEM 150Z")),
               Condition("equivalent transfer credit"))),
        ["CHEM 150", "CHEM 150Z"]),
    "transfer credit or after singleton": (
        "CS_OX 170 or equivalent transfer credit as a prerequisite",
        AnyOf((CourseRef("CS_OX 170"), Condition("equivalent transfer credit"))),
        ["CS_OX 170"]),
    "single labeled prerequisite with transfer alternative": (
        "MUS 121 as prerequisite or equivalent transfer credit.",
        AnyOf((CourseRef("MUS 121"), Condition("equivalent transfer credit"))),
        ["MUS 121"]),
    "transfer credit comma or after conjunction": (
        ("(CS 170 or CS_OX 170) and (CS 171 or CS_OX 171) and "
         "(CS 224 or CS_OX 224) and CS 253, or equivalent transfer credit "
         "as prerequisite"),
        AnyOf((AllOf((AnyOf(_c("CS 170", "CS_OX 170")),
                      AnyOf(_c("CS 171", "CS_OX 171")),
                      AnyOf(_c("CS 224", "CS_OX 224")),
                      CourseRef("CS 253"))),
               Condition("equivalent transfer credit"))),
        ["CS 170", "CS_OX 170", "CS 171", "CS_OX 171", "CS 224", "CS_OX 224", "CS 253"]),
    "transfer credit comma or after anyof": (
        ("(BIOL 142 and BIOL 142L) or BIOL_OX 142, or equivalent transfer credit "
         "as a prerequisite"),
        AnyOf((AllOf(_c("BIOL 142", "BIOL 142L")), CourseRef("BIOL_OX 142"),
               Condition("equivalent transfer credit"))),
        ["BIOL 142", "BIOL 142L", "BIOL_OX 142"]),
    "run-together code and conjunction": ("BIOL 151or CHEM 150",
                                          AnyOf(_c("BIOL 151", "CHEM 150")),
                                          ["BIOL 151", "CHEM 150"]),
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
    assert result.status in {"ambiguous", "unparsed"}
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


def test_combined_prerequisite_or_corequisite_label_parses():
    """EMO-028: "prerequisite or co-requisite" is one label, not unparsed prose."""
    result = parse_requisites("Prerequisite or co-requisite: CHEM 150L")
    assert result.status == "parsed"
    assert result.trees == {"corequisite": CourseRef("CHEM 150L")}
    assert [(m.kind, m.code) for m in result.mentions] == [("corequisite", "CHEM 150L")]


@pytest.mark.parametrize(("text", "tree"), [
    ("CHEM 150L or equivalent transfer credit as prerequisite or co-requisite",
     AnyOf((CourseRef("CHEM 150L"), Condition("equivalent transfer credit")))),
    (("(CHEM 204L or CHEM_OX 204L) and (CHEM 205L or CHEM_OX 205L) or "
      "equivalent transfer credit as prerequisite or co-requisite"),
     AnyOf((AllOf((AnyOf(_c("CHEM 204L", "CHEM_OX 204L")),
                   AnyOf(_c("CHEM 205L", "CHEM_OX 205L")))),
            Condition("equivalent transfer credit")))),
])
def test_transfer_with_combined_label_preserves_corequisite_timing(text, tree):
    result = parse_requisites(text)
    assert result.status == "parsed"
    assert result.trees == {"corequisite": tree}
    assert all(mention.kind == "corequisite" for mention in result.mentions)


@pytest.mark.parametrize(("text", "course_code", "status", "corequisite"), [
    # The combined label written inline after its courses keeps them.
    ("ENVS 240 as prerequisite or co-requisite, or equivalent transfer credit.", "ENVS 240L",
     "parsed", AnyOf((CourseRef("ENVS 240"), Condition("equivalent transfer credit")))),
    ("CHEM 202L as prerequisite and CHEM 203 or CHEM_OX 203 as prerequisite or co-requisite.",
     "CHEM 203L", "parsed", AnyOf(_c("CHEM 203", "CHEM_OX 203"))),
    ("CHEM 222 or equivalent transfer credit as a pre/co-requisite.", "CHEM 222L",
     "parsed", AnyOf((CourseRef("CHEM 222"), Condition("equivalent transfer credit")))),
    # Concurrent enrollment names the lab itself; only the other course is required.
    (("CHEM_OX 203 and CHEM_OX 203L as prerequisites or equivalent transfer credit. "
      "Students must enroll in CHEM_OX 204 and CHEM_OX 204L together. If not enrolled in "
      "both lecture and lab, students will be dropped at the end of add/drop/swap."),
     "CHEM_OX 204L", "parsed", CourseRef("CHEM_OX 204")),
    # Oxford's bare labels; the enrollment sentence restates the same corequisite.
    (("CHEM_OX 150 and CHEM_OX 150L prerequisites & CHEM_OX 202L co-requisite. Students must "
      "enroll in CHEM_OX 202 and CHEM_OX 202L together."),
     "CHEM_OX 202", "parsed", CourseRef("CHEM_OX 202L")),
    # A bare label word inside prose is not a label.
    ("MATH 111 prerequisite waivers are granted by the department.", None, "unparsed", None),
    ("Students must enroll in the lecture and lab together.", "CHEM_OX 204L", "unparsed", None),
])
def test_explicit_corequisite_wordings_name_the_corequisite(text, course_code, status, corequisite):
    result = parse_requisites(text, course_code=course_code)
    assert result.status == status
    assert result.trees.get("corequisite") == corequisite


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


@pytest.mark.parametrize(("text", "expected"), [
    ("Prerequisite: CS 101 and lab 201", ["CS 101"]),
    ("Prerequisite: held 345 seats", []),
    ("one 300-level course", []),
    ("two 300-level courses", []),
    ("any 300-level course", []),
    ("Prerequisite: MATH 111or112", ["MATH 111", "MATH 112"]),
    ("Prerequisite: Physics 151", ["PHYS 151"]),
    ("Prerequisite: Arabic 201", ["ARAB 201"]),
    ("Prerequisite: ENVS OX 230", ["ENVS_OX 230"]),
    ("Prerequisite: Anthropology 308", ["ANT 308"]),
    ("Prerequisite: Anthropology 309, 310, and 311", ["ANT 309", "ANT 310", "ANT 311"]),
    ("Prerequisite: MATH 111, 112, OR 115", ["MATH 111", "MATH 112", "MATH 115"]),
])
def test_parser_never_emits_unrecognized_subjects(text, expected):
    assert [m.code for m in parse_requisites(text).mentions] == expected


@pytest.mark.parametrize(("text", "kind", "code"), [
    ("BIOL 142 as a corequisite.", "corequisite", "BIOL 142"),
    ("CHEM 202Z as co-requisite.", "corequisite", "CHEM 202Z"),
    ("MATH 111 as a prerequisite.", "prerequisite", "MATH 111"),
    ("PHYS 151: MATH 111 as prerequisites and MATH 112 as co-requisites.",
     "corequisite", "MATH 112"),
])
def test_inline_requisite_labels_supply_the_kind(text, kind, code):
    result = parse_requisites(text)
    assert (kind, code) in {(m.kind, m.code) for m in result.mentions}


@pytest.mark.parametrize("text", [
    "PROHIBITS: Enrollment PE 150",
    "Students completing MATH 119 or equivalent cannot enroll for MATH 111.",
])
def test_prohibitions_and_exclusions_are_not_requirements(text):
    result = parse_requisites(text)
    assert result.mentions == ()
    assert result.trees == {}


def test_cannot_take_exclusion_removes_only_its_sentence():
    text = (
        "(CHEM 204 or CHEM_OX 204) and (CHEM 204L or CHEM_OX 204L) and "
        "(BIOL 141 or BIOL_OX 141) or equivalent transfer credit as prerequisites. "
        "Students who took BIOL 301, or have transfer credit equivalent to BIOL 301, "
        "cannot take this course."
    )
    result = parse_requisites(text)
    assert result.status == "parsed"
    assert "prerequisite" in result.trees
    assert {m.code for m in result.mentions} == {
        "CHEM 204", "CHEM_OX 204", "CHEM 204L", "CHEM_OX 204L",
        "BIOL 141", "BIOL_OX 141",
    }


def test_transfer_tail_does_not_discard_a_following_condition():
    result = parse_requisites(
        "ECON 101 or equivalent transfer credit as a prerequisite and sophomore standing"
    )
    assert result.status == "unparsed"
    assert result.trees == {}
    assert result.leftover


def test_transfer_tail_does_not_discard_a_following_alternative():
    result = parse_requisites(
        "ENVS 483 or equivalent transfer credit as prerequisites OR permission of instructor"
    )
    assert result.status in {"ambiguous", "unparsed"}
    assert result.trees == {}
    assert [mention.code for mention in result.mentions] == ["ENVS 483"]


@pytest.mark.parametrize("text", [
    ("CS 170 or CS_OX 170 as prerequisite and MATH 111 or MATH_OX 111 "
     "as co-requisite or equivalent transfer credit."),
    ("(PHYS 142 or PHYS_OX 142) as prerequisite and (MATH 211 or MATH_OX 211) "
     "as co-requisite or equivalent transfer credit."),
])
def test_split_transfer_clause_with_unclear_scope_is_unresolved(text):
    result = parse_requisites(text)
    assert result.status == "unparsed"
    assert any("equivalent transfer credit" in part for part in result.leftover)


@pytest.mark.parametrize("text", [
    "QTM100 equivalent transfer credit as a prerequisite",
    "ECON 201 AND ECON 212 AND ECON 420 or ECON 422 equivalent transfer credit as a prerequisite",
])
def test_transfer_clause_without_connector_is_unresolved(text):
    result = parse_requisites(text)
    assert result.status == "unparsed"
    assert any("equivalent transfer credit" in part for part in result.leftover)


def test_used_for_trailer_does_not_create_a_self_loop():
    result = parse_requisites(
        "FREN 310 as prerequisite. Used for: FREN 313, FREN 314",
        course_code="FREN 313",
    )
    assert [(m.kind, m.code) for m in result.mentions] == [("prerequisite", "FREN 310")]


def test_a_course_is_never_its_own_prerequisite():
    result = parse_requisites("MATH 111", course_code="MATH 111")
    assert result.mentions == ()
    assert result.trees == {}


@pytest.mark.parametrize(("query", "codes"), [
    ("MATH 111 and 112", ["MATH 111", "MATH 112"]),  # "and" is not a subject; subject carries
    ("Physics 141", ["PHYS 141"]),  # a spelled-out subject maps to its code
    ("Chemistry 150 and 202", ["CHEM 150", "CHEM 202"]),
    ("Biology 141", ["BIOL 141"]),
    ("Physics 152 prerequisite", ["PHYS 152"]),
    ("what do I need for econ301?", ["ECON 301"]),
    ("section 111 applies", []),  # a bare number is ignored
    ("MATH 111 and MATH 111 or ECON 101", ["MATH 111", "ECON 101"]),  # deduped, in order
    ("Can I take DATASCI 355 before AAS 490RW?", ["DATASCI 355", "AAS 490RW"]),
    ("CS_OX 170 and 171", ["CS_OX 170", "CS_OX 171"]),
    ("BIOL_OX 385RWE", ["BIOL_OX 385RWE"]),
    ("PAE 8100 and ARCH 16", ["PAE 8100", "ARCH 16"]),
    ("M2M 401 and VM 999D1", ["M2M 401", "VM 999D1"]),
])
def test_prereq_seam_finds_course_codes_in_a_query(query, codes):
    assert course_codes_in_query(query) == codes


@pytest.mark.parametrize("query", [
    "Which courses does CS 253 unlock?",
    "What can I take after completing CS 253?",
    "What courses can I take after CS 253?",
    "Which courses list CS 253 as a prerequisite?",
])
def test_reverse_prereq_intent_is_recognized(query):
    from app.retrieval.seams.prereq import is_reverse_query
    assert is_reverse_query(query)


@pytest.mark.parametrize("query", [
    "What are the prerequisites for CS 253?",
    "What does CS 253 require?",
])
def test_forward_prereq_questions_are_not_reverse(query):
    from app.retrieval.seams.prereq import is_reverse_query
    assert not is_reverse_query(query)
