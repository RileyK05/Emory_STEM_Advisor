"""Extraction tests. Locators must tile the text exactly."""

import json

import pytest

from app.ingest import extract as extract_module
from app.ingest.extract import EmptyExtraction, UnsupportedContentType, extract
from tests.fixtures.pdf import make_pdf


def _assert_tiling(text: str, locators) -> None:
    previous = 0
    for loc in locators:
        assert loc.start_char >= previous
        assert loc.end_char > loc.start_char
        previous = loc.end_char
    covered = [False] * len(text)
    for loc in locators:
        for i in range(loc.start_char, loc.end_char):
            assert not covered[i], f"double cover at {i}"
            covered[i] = True
    for i, char in enumerate(text):
        if not char.isspace():
            assert covered[i], f"uncovered at {i}"


def test_plain_line_range_locators_and_tiling():
    text = "\n".join(f"line {i}" for i in range(1, 91))
    result = extract(text.encode(), "text/plain", lines_per_locator=40)
    assert [loc.locator_type for loc in result.locators] == ["line_range"] * 3
    assert [loc.label for loc in result.locators] == ["lines 1-40", "lines 41-80", "lines 81-90"]
    _assert_tiling(result.text, result.locators)


@pytest.mark.parametrize(("code", "authority", "expected"), [
    ("CS_OX170", "atlas", "Campus: Oxford"),
    ("PAE8100", "atlas", "Requirement authority: Emory Course Atlas"),
    ("TATT605BSHES", "catalog", "Catalog fallback"),
    ("VM999D1", None, "Course description"),
])
def test_atlas_authority_and_exact_identity_survive_extraction(code, authority, expected):
    record = {"code": code, "title": "Course title", "description": "Course description",
              "authority": authority, "term": "Spring 2027", "careers": ["Oxford College"],
              "registration_notes": ["Instructor\x00 consent required"]}
    result = extract(json.dumps(record).encode(), "application/x-course-atlas+jsonl")
    assert expected in result.text
    assert "Instructor consent required" in result.text
    assert "\x00" not in result.text
    assert result.locators[0].label.replace(" ", "") == code
    _assert_tiling(result.text, result.locators)


def test_markdown_headings_outside_code_fence():
    doc = "# Title\n\nintro\n\n```\n# not a heading\n```\n\n## Section\n\nbody\n"
    result = extract(doc.encode(), "text/markdown")
    labels = [loc.label for loc in result.locators]
    assert "not a heading" not in labels
    assert "Title" in labels and "Section" in labels
    _assert_tiling(result.text, result.locators)


def test_markdown_preamble_gets_line_range():
    doc = "preamble line\n# Heading\n\nbody\n"
    result = extract(doc.encode(), "text/markdown")
    assert result.locators[0].locator_type == "line_range"
    assert result.locators[0].label == "lines 1-1"
    assert result.locators[1].locator_type == "section"
    _assert_tiling(result.text, result.locators)


def test_markdown_with_no_headings_is_all_line_range():
    doc = "just\nplain\nlines\n"
    result = extract(doc.encode(), "text/markdown")
    assert all(loc.locator_type == "line_range" for loc in result.locators)
    _assert_tiling(result.text, result.locators)


def test_bodyless_heading_merges_into_next_section():
    # A lone `##` heading must not become a passage holding only its own title.
    doc = "# Doc\n\nintro body\n\n## Only title\n\nreal body text\n"
    result = extract(doc.encode(), "text/markdown")
    labels = [loc.label for loc in result.locators]
    assert "Only title" in labels
    only = next(loc for loc in result.locators if loc.label == "Only title")
    assert "real body text" in result.text[only.start_char : only.end_char]
    _assert_tiling(result.text, result.locators)


def test_trailing_bodyless_heading_merges_into_previous_section():
    doc = "# Doc\n\nbody text\n\n## Dangling\n"
    result = extract(doc.encode(), "text/markdown")
    assert "Dangling" not in [loc.label for loc in result.locators]
    _assert_tiling(result.text, result.locators)


def test_bodyless_heading_not_stored_as_a_title_only_passage():
    # Every section locator must contain more than its own heading line.
    doc = "# A\n\n## B\n\n## C\n\ncontent here\n"
    result = extract(doc.encode(), "text/markdown")
    for loc in result.locators:
        if loc.locator_type != "section":
            continue
        body = result.text[loc.start_char : loc.end_char]
        assert body.strip()
    _assert_tiling(result.text, result.locators)


def test_pdf_page_text_matches_locator_slice():
    data = make_pdf(["Page one text", "Page two words", "Page three here"])
    result = extract(data, "application/pdf")
    assert [loc.label for loc in result.locators] == ["page 1", "page 2", "page 3"]
    for loc in result.locators:
        assert result.text[loc.start_char : loc.end_char].strip()
    _assert_tiling(result.text, result.locators)


def test_pdf_without_text_layer_raises():
    data = make_pdf(["", ""])
    with pytest.raises(EmptyExtraction):
        extract(data, "application/pdf")


def test_unsupported_content_type_raises():
    with pytest.raises(UnsupportedContentType):
        extract(b"data", "application/octet-stream")


def test_mixed_pdf_missing_page_is_visible_and_page_identity_is_preserved():
    result = extract(make_pdf(["First page", "", "Third page"]), "application/pdf")
    assert [loc.label for loc in result.locators] == ["page 1", "page 3"]
    assert any("page 2:" in warning for warning in result.warnings)


def test_pdf_replacement_characters_are_warned_and_indexed(monkeypatch):
    # A real text-layer PDF cannot easily carry U+FFFD through pypdf's simple
    # font encodings, so pin the warning branch at the extraction boundary.
    monkeypatch.setattr(
        extract_module, "_read_pdf",
        lambda raw: ("cour\ufffdse listing", ["cour\ufffdse listing"]),
    )
    result = extract(b"ignored", "application/pdf")
    assert "\ufffd" in result.text
    assert any("replacement characters" in warning for warning in result.warnings)
    _assert_tiling(result.text, result.locators)


def test_pdf_delimiter_characters_round_trip():
    data = make_pdf(["CS 101 (3 credits) \\ lab"])
    result = extract(data, "application/pdf")
    assert "CS 101 (3 credits)" in result.text
    assert "\\ lab" in result.text
    _assert_tiling(result.text, result.locators)


def test_pdf_split_course_numbers_are_rejoined():
    """EMO-014: pypdf kerning inserts a space inside a course number."""
    from app.ingest.extract import _repair_pdf_kerning

    assert _repair_pdf_kerning("Math 1 11 and PHIL 1 15") == "Math 111 and PHIL 115"
    assert _repair_pdf_kerning("Psychology 1 10") == "Psychology 110"
    # Ordinary prose and unknown title-case labels are not joined.
    assert _repair_pdf_kerning("page 1 11") == "page 1 11"
    assert _repair_pdf_kerning("Section 2 14 of the policy") == "Section 2 14 of the policy"
    assert _repair_pdf_kerning("Table 3 12") == "Table 3 12"
    assert _repair_pdf_kerning("May 3 24 deadline") == "May 3 24 deadline"


def test_pdf_page_offsets_keep_each_page_slice_distinct():
    data = make_pdf(["alpha page", "beta page"])
    result = extract(data, "application/pdf")
    first, second = result.locators
    assert [first.label, second.label] == ["page 1", "page 2"]
    assert result.text[first.start_char : first.end_char].strip() == "alpha page"
    assert result.text[second.start_char : second.end_char].strip() == "beta page"
    assert first.end_char <= second.start_char
    _assert_tiling(result.text, result.locators)


def test_pdf_header_only_page_is_indexed_without_header_detection():
    # Pins current behavior: a page holding only a running header is a passage,
    # not silently dropped or specially warned about. Header detection is not
    # implemented; this documents the boundary rather than claiming it is.
    result = extract(make_pdf(["Page 3 of 12"]), "application/pdf")
    assert [loc.label for loc in result.locators] == ["page 1"]
    assert result.text.strip() == "Page 3 of 12"
    assert result.warnings == ()
