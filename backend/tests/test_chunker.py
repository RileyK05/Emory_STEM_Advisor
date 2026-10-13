"""Chunker tests. Bounds, overlap, no locator crossing, determinism."""

import re

import pytest

from app.ingest.chunker import chunk
from app.ingest.extract import LocatorSpan
from tests.fakes import whitespace_tokens


def _locators(text: str) -> list[LocatorSpan]:
    return [
        LocatorSpan("line_range", "lines 1-1", 0, len(text)),
    ]


@pytest.mark.parametrize(("text", "target", "overlap"), [
    (" ".join(f"word{i}" for i in range(200)), 20, 5),
    (" ".join(f"w{i}" for i in range(30)), 5, 0),  # one long sentence is hard-split
], ids=["many words", "one long sentence"])
def test_every_chunk_within_target_tokens(text, target, overlap):
    spans = chunk(text, _locators(text), count_tokens=whitespace_tokens,
                  target_tokens=target, overlap_tokens=overlap)
    assert spans
    for span in spans:
        assert span.token_count <= target
        assert span.end_char > span.start_char
    covered = set()
    for span in spans:
        covered.update(text[span.start_char:span.end_char].split())
    assert covered == set(text.split())  # nothing dropped


def test_no_chunk_crosses_a_locator_boundary():
    text = "alpha beta gamma delta\n\nepsilon zeta eta theta"
    boundary = text.index("epsilon")
    locators = [
        LocatorSpan("section", "A", 0, boundary),
        LocatorSpan("section", "B", boundary, len(text)),
    ]
    spans = chunk(
        text, locators, count_tokens=whitespace_tokens, target_tokens=3, overlap_tokens=1
    )
    for span in spans:
        if span.locator_ordinal == 0:
            assert span.end_char <= boundary
        else:
            assert span.start_char >= boundary


def test_overlap_between_consecutive_chunks():
    text = "a b c. d e f. g h i. j k l."
    spans = chunk(
        text, _locators(text), count_tokens=whitespace_tokens, target_tokens=6, overlap_tokens=3
    )
    assert len(spans) >= 2
    first = set(text[spans[0].start_char : spans[0].end_char].split())
    second = set(text[spans[1].start_char : spans[1].end_char].split())
    assert first & second


def test_identical_input_gives_identical_spans():
    text = "one two three. four five six! seven eight nine?"
    args = {"count_tokens": whitespace_tokens, "target_tokens": 4, "overlap_tokens": 1}
    assert chunk(text, _locators(text), **args) == chunk(text, _locators(text), **args)


def test_single_word_over_budget_is_split_by_characters():
    # A form blank where every underscore is its own token (as in WordPiece).
    def char_tokens(s: str) -> int:
        return sum(len(word) for word in s.split())

    text = "Name: " + "_" * 50 + " ID: 123"
    spans = chunk(
        text, _locators(text), count_tokens=char_tokens, target_tokens=20, overlap_tokens=0
    )
    assert all(span.token_count <= 20 for span in spans)
    assert "".join(text[s.start_char:s.end_char] for s in spans).count("_") >= 50


def test_whitespace_that_costs_tokens_is_counted():
    # Like Granite's BPE: every run of spaces costs one token per space.
    def bpe_like(s: str) -> int:
        return len(s.split()) + sum(len(run) - 1 for run in re.findall(r" {2,}", s))

    text = "Name:" + " " * 60 + "ID#:" + " " * 60 + "Major: Public Policy"
    spans = chunk(text, _locators(text), count_tokens=bpe_like, target_tokens=30,
                  overlap_tokens=0)
    assert spans
    assert all(bpe_like(text[s.start_char:s.end_char]) <= 30 for s in spans)
