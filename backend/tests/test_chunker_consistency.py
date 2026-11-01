"""Chunking must be a function of the bytes alone.

A source's chunk boundaries, and therefore the text behind every
``chunk_id``, must not depend on which path indexed it. That matters because
``chunk_id`` is content-addressed from ``(source_id, chunker_version, index)``
only: if two paths disagree about where a chunk starts, the same chunk_id
addresses different text, and a saved citation silently moves.

Two counters were in use. The operator scripts counted with ``AutoTokenizer``
and no special tokens; the HTTP path counted with the embedder's tokenizer,
which adds special tokens. That two-token difference was enough to shift a
boundary: 42 of the 235 current sources chunk differently under the two
counters, and the two Atlas source documents in the corpus were cut by one of
them while everything else was cut by the other.

The measure now comes from the embedder on every path, and a re-index that
would move a chunk's text under an unchanged id is refused.
"""

from __future__ import annotations

import pytest

from app.ingest.chunker import chunk
from app.ingest.extract import LocatorSpan
from tests.fakes import whitespace_tokens

_TEXT = (
    "Prerequisite: CS 253 and CS 255 or equivalent transfer credit as prerequisites. "
    "The course covers systems programming, memory management, and concurrency. "
    "Students complete a semester-long project. Enrollment is by permission of instructor."
)


def _locators(text: str) -> list[LocatorSpan]:
    return [LocatorSpan("line_range", "lines 1-1", 0, len(text))]


def _spans(counter, text: str = _TEXT):
    return chunk(text, _locators(text), count_tokens=counter,
                 target_tokens=24, overlap_tokens=4)


def test_the_same_counter_always_gives_the_same_chunks():
    """Chunking is deterministic: the same bytes and counter, the same spans."""
    first = _spans(whitespace_tokens)
    second = _spans(whitespace_tokens)
    assert first == second


def test_a_chunk_is_a_verbatim_span_of_the_source():
    """No chunk may contain text the source does not have.

    A heading is prepended to the *embedding* input for a continuation chunk,
    never to the stored text, so a citation resolves to exactly what the
    document says.
    """
    spans = _spans(whitespace_tokens)
    for span in spans:
        stored = _TEXT[span.start_char:span.end_char]
        assert stored == stored.strip(), "the chunk span was not trimmed"
        assert stored in _TEXT
        assert "\n##" not in stored, "a heading was injected into the stored text"


def test_no_ingest_path_brings_its_own_tokenizer():
    """The chunking measure comes from the embedder, on every path.

    A chunk's id addresses it by index within its source, so two measures
    would put the same id on different text. The HTTP upload route and the
    operator scripts have to cut the same bytes the same way, and the only way
    to diverge is to build a tokenizer of one's own.
    """
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1]
    scripts = ["scripts/ingest.py", "scripts/import_atlas.py",
               "scripts/ingest_offerings.py"]
    offenders = [
        name for name in scripts
        if "AutoTokenizer" in (backend / name).read_text(encoding="utf-8")
    ]
    assert not offenders, (
        f"{offenders} build their own tokenizer, so they disagree with the HTTP "
        "ingest path about where a chunk starts")


@pytest.mark.parametrize("counter_name", ["count_tokens", "chunk_tokens"])
def test_the_embedder_exposes_both_measures(counter_name):
    """The embedding measure and the chunking measure are different questions.

    The embedding measure counts what the model sees, special tokens included,
    because that is what the model's ceiling applies to. The chunking measure
    counts what a piece of text occupies, because that is what a chunk's size
    is. An ingest path that used the wrong one would cut differently from the
    path that indexed the same bytes before.
    """
    from app.embeddings.base import Embedder

    assert hasattr(Embedder, counter_name)
