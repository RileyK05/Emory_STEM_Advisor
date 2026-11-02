"""Regression: atlas render includes credits, crosslists and campus equivalents."""

from app.atlas.records import AtlasCourse, render_courses


def test_render_courses_includes_credits_and_crosslisted():
    text, locators = render_courses([
        AtlasCourse(
            code="CS 329",
            title="Computational Linguistics",
            description="Syntax and semantics.",
            credits="3",
            crosslisted=["LING 329"],
            requisites="CS 171",
        )
    ])
    assert "Credits: 3" in text
    assert "Cross-listed as: LING 329" in text
    assert "Requisites: CS 171" in text
    assert locators == [("course", "CS 329", 0, len(text))]


def test_render_courses_omits_empty_credits_and_crosslist():
    text, _ = render_courses([
        AtlasCourse(code="CS 170", title="Intro", description="Java.", credits=None)
    ])
    assert "Credits:" not in text
    assert "Cross-listed" not in text


def test_render_courses_includes_campus_equivalents():
    text, _ = render_courses(
        [AtlasCourse(code="MATH_OX 111", title="Calculus I", description="Calc.")],
        campus_equivalents={"MATH_OX 111": ["MATH 111"]},
    )
    assert "Campus equivalents: MATH 111" in text
