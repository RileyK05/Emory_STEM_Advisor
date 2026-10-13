"""Bind a parsed program to its exact indexed source and align its lines to chunks.

No model, no fuzzy matching. A program is bound only through the exact-SHA ingest
identity the scraper recorded for the catalog file it parsed (origin key plus
content SHA-256), resolved against current, indexed, shared sources. Each
requirement line and header field is then located verbatim in that source's
chunk text, after the same whitespace folding that separates a PDF-wrapped line
from its Markdown rendering. Anything that cannot be located stays a discovery
lead (`evidence_unresolved`); it is never attached to a nearby chunk by guess.
"""

import hashlib
import re
from bisect import bisect_left
from dataclasses import dataclass
from uuid import UUID

from app.db import queries
from app.programs.requirements import _META

# Fields a research read may present as cited header facts (migration 038).
HEADER_FIELDS = ("title", "degree", "hours", "courses_required", "department")
_MAX_SEPARATOR_GAP = 2
_LEADING_MARK = re.compile(r"^(?:[-•●◦○]\s*)+")


@dataclass(frozen=True)
class SourceChunk:
    chunk_id: UUID
    locator_id: UUID | None
    start_char: int
    end_char: int
    text: str


@dataclass(frozen=True)
class Support:
    """One chunk's share of a located line; offsets are into that chunk's text."""

    chunk_id: UUID
    locator_id: UUID | None
    span_start: int
    span_end: int
    text_digest: str


@dataclass(frozen=True)
class BoundSource:
    source_id: UUID
    index_revision: int
    file_hash: str
    origin_key: str


@dataclass(frozen=True)
class Binding:
    """The outcome for one program: its status, source, and per-line support."""

    status: str  # bound | unbound | evidence_unresolved
    source: BoundSource | None
    items: dict[int, tuple[Support, ...]]
    fields: dict[str, tuple[Support, ...]]
    reason: str | None = None


def _fold(text: str) -> tuple[str, list[int]]:
    """Whitespace runs -> one space, with each folded index's original offset."""
    folded: list[str] = []
    offsets: list[int] = []
    in_space = False
    for index, char in enumerate(text):
        if char.isspace():
            if not in_space and folded:
                folded.append(" ")
                offsets.append(index)
            in_space = True
            continue
        folded.append(char)
        offsets.append(index)
        in_space = False
    return "".join(folded), offsets


class SourceText:
    """The source's indexed text, rebuilt from its (overlapping) chunks."""

    def __init__(self, chunks: list[SourceChunk]):
        self.chunks = sorted(chunks, key=lambda c: (c.start_char, c.end_char))
        length = max((c.end_char for c in self.chunks), default=0)
        chars: list[str | None] = [None] * length
        for chunk in self.chunks:
            if len(chunk.text) != chunk.end_char - chunk.start_char:
                raise ValueError(f"chunk {chunk.chunk_id} text does not match its offsets")
            for offset, char in enumerate(chunk.text):
                position = chunk.start_char + offset
                if chars[position] not in (None, char):
                    raise ValueError(f"chunk {chunk.chunk_id} disagrees with an overlapping chunk")
                chars[position] = char
        # The chunker leaves the separator newline between two locator regions out
        # of both chunks. Those gaps read as line breaks; `support` steps over a
        # gap only when it is that short, so a long hole can never be bridged.
        self._gaps = {index for index, char in enumerate(chars) if char is None}
        self.text = "".join(char if char is not None else "\n" for char in chars)
        self.folded, self._offsets = _fold(self.text)
        self._region_start = 0

    def _span(self, start: int, end: int) -> tuple[int, int]:
        """Folded [start, end) -> original [start, end)."""
        return self._offsets[start], self._offsets[end - 1] + 1

    def find(self, needle: str, start: int = 0) -> tuple[int, int] | None:
        folded_needle = _fold(_LEADING_MARK.sub("", needle.strip()))[0]
        if not folded_needle:
            return None
        index = self.folded.find(folded_needle, self._folded_index(start))
        if index < 0:
            return None
        return self._span(index, index + len(folded_needle))

    def find_unique(self, needle: str, start: int = 0) -> tuple[int, int] | None:
        """The match after `start`; failing that, the only match after `start` of the
        region before it. An earlier line that matched a later duplicate can push the
        cursor past this one; a line that occurs exactly once is still unambiguous."""
        found = self.find(needle, start)
        if found is not None:
            return found
        folded_needle = _fold(_LEADING_MARK.sub("", needle.strip()))[0]
        return None if not folded_needle else self._only(folded_needle)

    def _only(self, folded_needle: str) -> tuple[int, int] | None:
        first = self.folded.find(folded_needle, self._region_start)
        if first < 0 or self.folded.find(folded_needle, first + 1) >= 0:
            return None
        return self._span(first, first + len(folded_needle))

    def search(self, pattern: re.Pattern, start: int = 0) -> tuple[int, int] | None:
        match = pattern.search(self.folded, self._folded_index(start))
        return self._span(match.start(), match.end()) if match else None

    def _folded_index(self, original: int) -> int:
        return bisect_left(self._offsets, original)

    def support(self, start: int, end: int) -> tuple[Support, ...]:
        """The fewest chunks covering [start, end): one if any chunk holds it all."""
        whole = [c for c in self.chunks if c.start_char <= start and end <= c.end_char]
        if whole:
            return (self._piece(whole[0], start, end),)
        pieces: list[Support] = []
        position = start
        while position < end:
            covering = [c for c in self.chunks if c.start_char <= position < c.end_char]
            if not covering:
                gap = position
                while gap < end and gap in self._gaps:
                    gap += 1
                if gap == position or gap - position > _MAX_SEPARATOR_GAP:
                    return ()
                position = gap
                continue
            chunk = max(covering, key=lambda c: c.end_char)
            pieces.append(self._piece(chunk, position, min(end, chunk.end_char)))
            position = chunk.end_char
        return tuple(pieces)

    @staticmethod
    def _piece(chunk: SourceChunk, start: int, end: int) -> Support:
        local_start, local_end = start - chunk.start_char, end - chunk.start_char
        exact = chunk.text[local_start:local_end]
        return Support(chunk.chunk_id, chunk.locator_id, local_start, local_end,
                       hashlib.sha256(exact.encode("utf-8")).hexdigest())


def _header_pattern(field: str, value: str) -> re.Pattern | None:
    label = next((pattern.pattern.lstrip("^") for pattern, key in _META if key == field), None)
    if label is None:
        return None
    # A multi-line contact list was joined with "; "; each name stays in order.
    parts = [re.escape(_fold(part)[0]) for part in value.split("; ") if part.strip()]
    return re.compile(label + r"\s*" + r".{0,200}?".join(parts)) if parts else None


def align(record: dict, text: SourceText) -> tuple[dict[int, tuple[Support, ...]],
                                                   dict[str, tuple[Support, ...]], list[str]]:
    """Locate the record's header fields and requirement lines, in document order."""
    fields: dict[str, tuple[Support, ...]] = {}
    missing: list[str] = []
    cursor = 0
    title = text.find(record["title"])
    if title:
        fields["title"] = text.support(*title)
        cursor = title[1]
    else:
        missing.append("title")
    for field in HEADER_FIELDS[1:]:
        value = record.get(field)
        if not value:
            continue
        pattern = _header_pattern(field, value)
        found = text.search(pattern, cursor) if pattern else None
        if found:
            fields[field] = text.support(*found)
            cursor = max(cursor, found[1])
        else:
            missing.append(field)
    items: dict[int, tuple[Support, ...]] = {}
    text._region_start = text._folded_index(cursor)  # Requirement lines follow the header.
    for item in record["items"]:
        if not any(char.isalnum() for char in item["text"]):
            continue  # A rule line ("------") states no requirement to support.
        found = text.find_unique(item["text"], cursor)
        support = text.support(*found) if found else ()
        if support:
            items[item["position"]] = support
            cursor = max(cursor, found[1])
        else:
            missing.append(f"item {item['position']}")
    return items, fields, missing


def resolve_source(conn, collection_id: UUID, identity: dict | None) -> BoundSource | None:
    """The current indexed shared source with exactly this manifest identity."""
    if not identity or not identity.get("origin_key") or not identity.get("content_sha256"):
        return None
    row = conn.execute(queries.get("resolve_program_source"), {
        "collection_id": collection_id, "origin_key": identity["origin_key"],
        "file_hash": identity["content_sha256"],
    }).fetchone()
    if row is None:
        return None
    return BoundSource(row["source_id"], row["index_revision"], row["file_hash"],
                       row["origin_key"])


def bind(conn, collection_id: UUID, record: dict) -> Binding:
    """Read-only: decide one program's binding before the import transaction."""
    source = resolve_source(conn, collection_id, record.get("source"))
    if source is None:
        reason = ("no source identity in the parsed record" if not record.get("source")
                  else "no current indexed source has that exact identity")
        return Binding("unbound", None, {}, {}, reason)
    chunks = [SourceChunk(row["chunk_id"], row["locator_id"], row["start_char"],
                          row["end_char"], row["text"])
              for row in conn.execute(queries.get("list_source_chunks_for_binding"),
                                      {"source_id": source.source_id}).fetchall()]
    items, fields, missing = align(record, SourceText(chunks))
    if missing:
        return Binding("evidence_unresolved", source, items, fields,
                       "unaligned: " + ", ".join(missing[:20]))
    return Binding("bound", source, items, fields)
