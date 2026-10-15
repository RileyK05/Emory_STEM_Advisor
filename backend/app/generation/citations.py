"""Code-aware numeric citation parsing, preserving first-mention order."""

import re

_FENCE_START = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_INLINE = re.compile(r"(`+).*?\1")
_MARKER = re.compile(r"\[([+-]?[0-9][0-9\s,;\-–]*)([\])])")
_UNCLOSED = re.compile(r"\[[0-9][0-9\s,;\-–]*(?=$|\n)")
# Raw tool-evidence tokens a model sometimes echoes instead of a numbered marker.
_RESOURCE_MARKER = re.compile(r"\[resource(?::|_id=)[^\]\n]{0,200}\]", re.IGNORECASE)


class InvalidCitation(ValueError):
    """A citation marker cannot resolve to supplied context."""


def citation_visible_text(text: str) -> str:
    """Mask code while preserving character offsets and paragraph boundaries."""
    chars = list(text)
    fence: tuple[str, int] | None = None
    offset = 0
    for line in text.splitlines(keepends=True):
        match = _FENCE_START.match(line)
        if fence:
            marker, length = fence
            stripped = line.lstrip(" ")
            if stripped.startswith(marker * length) and not stripped.lstrip(marker).strip():
                fence = None
            _mask_non_newlines(chars, offset, offset + len(line))
        elif match:
            fence = (match[1][0], len(match[1]))
            _mask_non_newlines(chars, offset, offset + len(line))
        else:
            for inline in _INLINE.finditer(line):
                _mask_non_newlines(chars, offset + inline.start(), offset + inline.end())
        offset += len(line)
    return "".join(chars)


def _mask_non_newlines(chars: list[str], start: int, end: int) -> None:
    for index in range(start, end):
        if chars[index] not in "\r\n":
            chars[index] = " "


def citation_numbers(text: str, context_count: int) -> tuple[int, ...]:
    return citation_numbers_visible(citation_visible_text(text), context_count)


def strip_resource_markers(text: str) -> str:
    """Strip raw ``[resource:<id>]`` tokens a model echoes as citations (EMO-033).

    Tool/planning mode advertises ``resource_id=<uuid>`` in the context block;
    some models copy it verbatim into the answer, where it reaches the student.
    Removing the token leaves the surrounding numbered citation intact.
    """
    return _RESOURCE_MARKER.sub("", text)


def citation_numbers_visible(visible: str, context_count: int) -> tuple[int, ...]:
    """Parse citation markers in text already masked by citation_visible_text."""
    if _UNCLOSED.search(visible):
        raise InvalidCitation("unclosed citation marker")
    ordered: list[int] = []
    for match in _MARKER.finditer(visible):
        if visible[match.end():].startswith("("):
            continue  # A Markdown link label is not an inline evidence marker.
        if match[2] != "]":
            raise InvalidCitation("malformed citation marker")
        for part in re.split(r"[,;]", match[1].strip()):
            values = re.fullmatch(r"(\d+)(?:\s*[-–]\s*(\d+))?", part.strip())
            if values is None or any(len(v) > 12 for v in values.groups() if v):
                raise InvalidCitation("malformed citation reference")
            first = int(values[1])
            last = int(values[2]) if values[2] else first
            if first < 1 or last < first or last > context_count:
                raise InvalidCitation("citation outside context or reversed range")
            for number in range(first, last + 1):
                if number not in ordered:
                    ordered.append(number)
    return tuple(ordered)
