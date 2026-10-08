"""Chunker tests. Bounds, overlap, no locator crossing, determinism."""

from app.ingest.chunker import chunk
from app.ingest.extract import LocatorSpan
from tests.fakes import whitespace_tokens


def _locators(text: str) -> list[LocatorSpan]:
    return [
        LocatorSpan("line_range", "lines 1-1", 0, len(text)),
    ]


def test_every_chunk_within_target_tokens():
    text = " ".join(f"word{i}" for i in range(200))
    spans = chunk(
        text, _locators(text), count_tokens=whitespace_tokens, target_tokens=20, overlap_tokens=5
    )
    assert spans
    for span in spans:
        assert span.token_count <= 20
        assert span.end_char > span.start_char


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


def test_long_sentence_is_hard_split():
    text = " ".join(f"w{i}" for i in range(30))
    spans = chunk(
        text, _locators(text), count_tokens=whitespace_tokens, target_tokens=5, overlap_tokens=0
    )
    assert all(span.token_count <= 5 for span in spans)
    assert sum(span.token_count for span in spans) >= 30


def test_identical_input_gives_identical_spans():
    text = "one two three. four five six! seven eight nine?"
    args = {"count_tokens": whitespace_tokens, "target_tokens": 4, "overlap_tokens": 1}
    assert chunk(text, _locators(text), **args) == chunk(text, _locators(text), **args)
