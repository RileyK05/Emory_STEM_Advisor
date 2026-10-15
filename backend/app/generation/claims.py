"""Citation-bearing review units, never automatic factual verification."""

import re
from dataclasses import dataclass

from app.generation.citations import (
    InvalidCitation,
    citation_numbers_visible,
    citation_visible_text,
)


@dataclass(frozen=True)
class ClaimUnit:
    ordinal: int
    text: str
    citation_numbers: tuple[int, ...]


_BULLET_LINE_RE = re.compile(r"^[^\S\r\n]*(?:[-*+•·]|\d{1,3}[.)])[ \t]")


def answer_paragraph_spans(text: str) -> tuple[tuple[int, int, str], ...]:
    """Return each nonempty answer unit with its original text span.

    A unit is a blank-line-separated paragraph or a single bullet item. Splitting
    bullets apart matters for the citation guard: when a whole bullet list was one
    paragraph, one cited bullet silently covered every uncited bullet and any
    uncited model self-talk after it (EMO-027). Splitting on lines also keeps a
    paragraph that ends in a trailing space from swallowing the next one.
    """
    spans: list[tuple[int, int]] = []
    offset = 0
    start = end = None
    previous_blank = True
    for line in text.splitlines(keepends=True):
        line_start = offset
        offset += len(line)
        if not line.strip():
            if start is not None:
                spans.append((start, end))
                start = end = None
            previous_blank = True
            continue
        if start is None:
            start = line_start
        elif previous_blank or _BULLET_LINE_RE.match(line):
            spans.append((start, end))
            start = line_start
        end = offset
        previous_blank = False
    if start is not None:
        spans.append((start, end))
    trimmed = []
    for begin, finish in spans:
        while begin < finish and text[begin].isspace():
            begin += 1
        while finish > begin and text[finish - 1].isspace():
            finish -= 1
        if begin < finish:
            trimmed.append((begin, finish))
    return tuple((begin, finish, text[begin:finish]) for begin, finish in trimmed)


def review_units(text: str, context_size: int) -> tuple[ClaimUnit, ...]:
    """Keep complete paragraphs as review units rather than inventing propositions.

    A unit may contain several factual assertions. Citation syntax merely gives
    reviewers candidate supporting passages; it does not establish entailment.
    """
    result = []
    visible = citation_visible_text(text)
    for start, end, paragraph in answer_paragraph_spans(text):
        try:
            numbers = citation_numbers_visible(visible[start:end], context_size)
        except InvalidCitation:
            numbers = ()
        result.append(ClaimUnit(len(result), paragraph, numbers))
    return tuple(result)
