"""Token-bounded chunking. A chunk never crosses a locator boundary (D4)."""

import re
from collections.abc import Callable
from dataclasses import dataclass

from app.ingest.extract import LocatorSpan

_SEP_RE = re.compile(r"(?<=[.!?])\s+|\n[ \t]*\n")
_WORD_RE = re.compile(r"\S+")


@dataclass(frozen=True)
class ChunkSpan:
    locator_ordinal: int
    start_char: int
    end_char: int
    token_count: int


@dataclass(frozen=True)
class _Unit:
    start: int
    end: int
    tokens: int


def _sentence_spans(text: str, start: int, end: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    cursor = start
    for match in _SEP_RE.finditer(text, start, end):
        spans.append((cursor, match.start()))
        cursor = match.end()
    spans.append((cursor, end))

    trimmed: list[tuple[int, int]] = []
    for raw_start, raw_end in spans:
        piece = text[raw_start:raw_end]
        lead = len(piece) - len(piece.lstrip())
        trail = len(piece) - len(piece.rstrip())
        s = raw_start + lead
        e = raw_end - trail
        if e > s:
            trimmed.append((s, e))
    return trimmed


def _units(
    text: str,
    *,
    count_tokens: Callable[[str], int],
    target_tokens: int,
    start: int,
    end: int,
) -> list[_Unit]:
    units: list[_Unit] = []
    for s, e in _sentence_spans(text, start, end):
        tokens = count_tokens(text[s:e])
        if tokens <= target_tokens:
            units.append(_Unit(s, e, tokens))
            continue

        cur_start: int | None = None
        cur_end = 0
        cur_tokens = 0
        for word in _WORD_RE.finditer(text, s, e):
            word_tokens = count_tokens(text[word.start():word.end()])
            if cur_start is None:
                cur_start, cur_end, cur_tokens = word.start(), word.end(), word_tokens
            elif cur_tokens + word_tokens <= target_tokens:
                cur_end = word.end()
                cur_tokens += word_tokens
            else:
                units.append(_Unit(cur_start, cur_end, cur_tokens))
                cur_start, cur_end, cur_tokens = word.start(), word.end(), word_tokens
        if cur_start is not None:
            units.append(_Unit(cur_start, cur_end, cur_tokens))
    return units


def _pack(units: list[_Unit], target_tokens: int, overlap_tokens: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    n = len(units)
    i = 0
    while i < n:
        end = i
        total = 0
        while end < n and total + units[end].tokens <= target_tokens:
            total += units[end].tokens
            end += 1
        if end == i:
            end = i + 1
        spans.append((units[i].start, units[end - 1].end))
        if end >= n:
            break
        cursor = end
        accumulated = 0
        while cursor - 1 > i and accumulated < overlap_tokens:
            cursor -= 1
            accumulated += units[cursor].tokens
        i = cursor
    return spans


def chunk(
    text: str,
    locators: list[LocatorSpan],
    *,
    count_tokens: Callable[[str], int],
    target_tokens: int,
    overlap_tokens: int,
) -> list[ChunkSpan]:
    chunks: list[ChunkSpan] = []
    for ordinal, locator in enumerate(locators):
        units = _units(
            text,
            count_tokens=count_tokens,
            target_tokens=target_tokens,
            start=locator.start_char,
            end=locator.end_char,
        )
        for start, end in _pack(units, target_tokens, overlap_tokens):
            segment = text[start:end]
            lead = len(segment) - len(segment.lstrip())
            trail = len(segment) - len(segment.rstrip())
            s = start + lead
            e = end - trail
            if e <= s:
                continue
            chunks.append(
                ChunkSpan(
                    locator_ordinal=ordinal,
                    start_char=s,
                    end_char=e,
                    token_count=count_tokens(text[s:e]),
                )
            )
    return chunks
