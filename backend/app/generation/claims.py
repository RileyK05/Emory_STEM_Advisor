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


def answer_paragraph_spans(text: str) -> tuple[tuple[int, int, str], ...]:
    """Return each nonempty paragraph with its original text span."""
    spans = []
    for match in re.finditer(r"\S(?:.*?\S)?(?=\r?\n[^\S\r\n]*\r?\n+|$)", text, re.DOTALL):
        start, end = match.span()
        spans.append((start, end, text[start:end]))
    return tuple(spans)


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
