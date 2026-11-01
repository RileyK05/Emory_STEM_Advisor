"""Regression: atlas render includes credits/crosslists; CS 171 data integrity."""

from pathlib import Path

from app.atlas.records import AtlasCourse, load_atlas, render_courses


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


def test_cs_171_requisites_are_cs_170_not_mus_221():
    """Atlas scrape once corrupted CS 171 to require MUS 221; keep it fixed.

    Official: Oxford catalog and CS placement guide list CS 170 as the prereq.
    https://oxford.emory.edu/catalog/course-descriptions/computer-science.html
    """
    path = Path(__file__).resolve().parents[2] / "data" / "atlas" / "emory_college.jsonl"
    courses = {c.code: c for c in load_atlas(path)}
    assert "CS 171" in courses
    raw = courses["CS 171"].requisites or ""
    assert "MUS 221" not in raw
    assert "CS 170" in raw
