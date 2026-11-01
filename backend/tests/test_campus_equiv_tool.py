from app.generation.advising_tools import (
    CampusEquivalentsInput,
    advising_specs,
)


def test_campus_equivalents_tool_returns_known_pairs_without_course_context():
    specs = advising_specs(
        None,
        [],
        campus_equivalents={
            "MATH_OX 111": frozenset({"MATH 111"}),
            "MATH 111": frozenset({"MATH_OX 111"}),
        },
    )
    by_id = {spec.tool_id: spec for spec in specs}
    assert "advisor_lookup_campus_equivalents" in by_id
    out = by_id["advisor_lookup_campus_equivalents"].handler(
        CampusEquivalentsInput(course_codes=["MATH_OX 111", "CS 999"]),
        None,
    )
    rows = {row.code: row for row in out.courses}
    assert rows["MATH_OX 111"].known is True
    assert rows["MATH_OX 111"].equivalents == ["MATH 111"]
    assert rows["CS 999"].known is False
    assert rows["CS 999"].equivalents == []


def test_campus_equivalents_tool_absent_when_mapping_empty():
    assert advising_specs(None, [], campus_equivalents=None) == ()
    assert advising_specs(None, [], campus_equivalents={}) == ()


def test_unsupported_course_codes_accepts_atlas_campus_equivalents():
    """Program list citing CS 171 may support an answer that names CS_OX 171."""
    from app.generation.answer import unsupported_course_codes
    subjects = {"CS", "CS_OX"}
    paragraph = (
        "CS_OX 171 can satisfy the CS BS requirement slot that lists CS 171 [1]."
    )
    support = "Computer Science (BS) requires CS 170, CS 171, CS 224."
    assert unsupported_course_codes(paragraph, support, subjects=subjects) == ("CS_OX 171",)
    equiv = {"CS 171": frozenset({"CS_OX 171"}), "CS_OX 171": frozenset({"CS 171"})}
    assert unsupported_course_codes(
        paragraph, support, subjects=subjects, campus_equivalents=equiv,
    ) == ()


def test_campus_equivalents_tool_description_mentions_program_slots():
    specs = advising_specs(
        None,
        [],
        campus_equivalents={"CS 171": frozenset({"CS_OX 171"})},
    )
    assert len(specs) == 1
    assert "program requirement slot" in specs[0].description
