"""Format-aware extraction: text plus locators that tile the text exactly."""

import io
from dataclasses import dataclass

from pypdf import PdfReader

from app.ingest.normalize import decode_text, normalize_text


class UnsupportedContentType(ValueError):
    """The content type has no extractor."""


class EmptyExtraction(ValueError):
    """The source yielded no text (e.g. a PDF without a text layer)."""


@dataclass(frozen=True)
class LocatorSpan:
    locator_type: str
    label: str
    start_char: int
    end_char: int


@dataclass(frozen=True)
class Extracted:
    text: str
    locators: list[LocatorSpan]


def _assert_cover(text: str, locators: list[LocatorSpan]) -> None:
    previous_end = 0
    for loc in locators:
        if loc.start_char < previous_end:
            raise AssertionError("locators overlap or are out of order")
        if loc.end_char <= loc.start_char:
            raise AssertionError("empty locator span")
        previous_end = loc.end_char
    covered = [False] * len(text)
    for loc in locators:
        for i in range(loc.start_char, loc.end_char):
            if covered[i]:
                raise AssertionError("character covered twice")
            covered[i] = True
    for i, char in enumerate(text):
        if not char.isspace() and not covered[i]:
            raise AssertionError(f"uncovered character at {i}")


def _line_range_locators(
    text: str, lines_per_locator: int, first_line_number: int = 1
) -> list[LocatorSpan]:
    locators: list[LocatorSpan] = []
    lines = text.split("\n")
    offset = 0
    group: list[tuple[int, int, int]] = []
    for i, line in enumerate(lines):
        line_number = first_line_number + i
        group.append((offset, offset + len(line), line_number))
        offset += len(line) + 1
        last_line = i == len(lines) - 1
        if len(group) == lines_per_locator or last_line:
            start = group[0][0]
            end = group[-1][1]
            if end > start:
                locators.append(
                    LocatorSpan(
                        "line_range",
                        f"lines {group[0][2]}-{group[-1][2]}",
                        start,
                        end,
                    )
                )
            group = []
    return locators


def _heading_spans(text: str) -> list[tuple[int, int, str]]:
    """Return (start offset, level, title) for ATX headings outside code fences."""
    spans: list[tuple[int, int, str]] = []
    fence: str | None = None
    offset = 0
    for line in text.split("\n"):
        stripped = line.lstrip()
        if fence is None:
            if stripped.startswith(("```", "~~~")):
                fence = stripped[:3]
            elif stripped.startswith("#"):
                hashes = len(stripped) - len(stripped.lstrip("#"))
                if 1 <= hashes <= 6 and (
                    len(stripped) == hashes or stripped[hashes] == " "
                ):
                    spans.append((offset, hashes, stripped[hashes:].strip()))
        elif stripped.startswith(fence):
            fence = None
        offset += len(line) + 1
    return spans


def _run_end(text: str, headings: list[tuple[int, int, str]], i: int) -> int:
    """End (trailing whitespace stripped) of heading i's run, capped at the
    next heading."""
    next_start = headings[i + 1][0] if i + 1 < len(headings) else len(text)
    return len(text[:next_start].rstrip("\n"))


def _has_body(text: str, headings: list[tuple[int, int, str]], i: int) -> bool:
    """True when heading i has content beyond its own line."""
    line_end = text.find("\n", headings[i][0])
    if line_end == -1:
        return False
    next_start = headings[i + 1][0] if i + 1 < len(headings) else len(text)
    return bool(text[line_end:next_start].strip())


def _extract_markdown(text: str, lines_per_locator: int) -> list[LocatorSpan]:
    headings = _heading_spans(text)
    if not headings:
        return _line_range_locators(text, lines_per_locator)

    locators: list[LocatorSpan] = []
    preamble = text[: headings[0][0]].rstrip("\n")
    if preamble:
        locators.extend(_line_range_locators(preamble, lines_per_locator))

    # A body-less heading would otherwise become a locator holding only its
    # own title, and that title would be indexed and searched as a passage.
    # Merge such headings into the next heading that has a body, so the title
    # travels with the section it introduces; a trailing body-less run attaches
    # to the section above it instead.
    i = 0
    while i < len(headings):
        start, _level, title = headings[i]
        if _has_body(text, headings, i):
            locators.append(LocatorSpan("section", title, start, _run_end(text, headings, i)))
            i += 1
            continue
        j = i + 1
        while j < len(headings) and not _has_body(text, headings, j):
            j += 1
        if j < len(headings):
            locators.append(LocatorSpan("section", title, start, _run_end(text, headings, j)))
            i = j + 1
        else:
            end = len(text.rstrip("\n"))
            if end <= start:
                break
            if locators and locators[-1].locator_type == "section":
                previous = locators[-1]
                locators[-1] = LocatorSpan(
                    previous.locator_type, previous.label, previous.start_char, end
                )
            else:
                locators.append(LocatorSpan("section", title, start, end))
            break
    return locators


def _read_pdf(raw: bytes) -> tuple[str, list[str]]:
    reader = PdfReader(io.BytesIO(raw))
    pages = [normalize_text(page.extract_text() or "") for page in reader.pages]
    return "\n\n".join(pages), pages


def _extract_pdf(raw: bytes) -> Extracted:
    text, pages = _read_pdf(raw)
    locators: list[LocatorSpan] = []
    offset = 0
    for number, page_text in enumerate(pages, start=1):
        if page_text.strip():
            locators.append(
                LocatorSpan("page", f"page {number}", offset, offset + len(page_text))
            )
        offset += len(page_text) + 2
    if not locators:
        raise EmptyExtraction("no text layer; OCR is not supported")
    return Extracted(text=text, locators=locators)


def extract(raw: bytes, content_type: str, *, lines_per_locator: int = 40) -> Extracted:
    if content_type == "application/x-course-atlas+jsonl":
        from app.atlas.records import load_atlas_lines, render_courses

        courses = load_atlas_lines(decode_text(raw).splitlines())
        text, spans = render_courses(courses)
        result = Extracted(
            text=text,
            locators=[
                LocatorSpan(kind, label, start, end)
                for kind, label, start, end in spans
            ],
        )
    elif content_type == "application/pdf":
        result = _extract_pdf(raw)
    elif content_type == "text/markdown":
        text = normalize_text(decode_text(raw))
        result = Extracted(text=text, locators=_extract_markdown(text, lines_per_locator))
    elif content_type == "text/plain":
        text = normalize_text(decode_text(raw))
        result = Extracted(text=text, locators=_line_range_locators(text, lines_per_locator))
    else:
        raise UnsupportedContentType(content_type)

    _assert_cover(result.text, result.locators)
    return result
