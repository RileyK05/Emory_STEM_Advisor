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
