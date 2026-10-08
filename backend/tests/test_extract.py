"""Extraction tests. Locators must tile the text exactly."""

import pytest

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
