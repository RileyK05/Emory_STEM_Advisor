"""Deterministic prerequisite parser. No model, no inference, no guessing.

Pure functions over the atlas `requisites` text. Every course code mentioned is
recorded as a mention (always); the boolean tree is emitted only when the parse
is unambiguous. Mixed and/or precedence is never chosen: such input is
`ambiguous`. A comma list with no explicit conjunction is ambiguous too.
"""

import re
from dataclasses import dataclass

from app.atlas.records import normalize_code

PARSER_VERSION = "1"

_DIGITS = r"\d{3}[A-Za-z]?"
_CODE_RE = re.compile(rf"([A-Za-z]{{2,5}})\s?({_DIGITS})|\b({_DIGITS})\b")
_GRADE_RE = re.compile(
    r"with\s+(?:a\s+)?(?:minimum\s+)?grade\s+of\s+[A-D][+-]?\s+or\s+(?:better|higher)",
    re.IGNORECASE,
)
_GRADE_RE2 = re.compile(
    r"with\s+(?:a\s+)?[A-D][+-]?\s+or\s+(?:better|higher)", re.IGNORECASE
)
_LABEL_RE = re.compile(
    r"\b(recommended\s+preparation|recommended|co-?requisites?|prerequisites?)\s*:\s*",
    re.IGNORECASE,
)
_CONDITIONS = [
    (re.compile(r"(permission|consent)\s+of\s+(the\s+)?instructor", re.IGNORECASE),
     "permission of instructor"),
    (re.compile(r"equivalent", re.IGNORECASE), "equivalent"),
    (re.compile(r"(freshman|sophomore|junior|senior)\s+standing", re.IGNORECASE),
     None),  # canonical text is the matched phrase, so the year is kept
]
_AND_OR = re.compile(r"(and|or)\b", re.IGNORECASE)
# Between a code and a bare number, only these separators are allowed for the
# subject to carry forward: whitespace, commas, slashes, and/or. So
# "MATH 111, 112, or 115" carries MATH across ", " and ", or ".
_CARRY_SEP = re.compile(r"\s*(?:(?:,|/|and|or)\s*)*")


@dataclass(frozen=True)
class CourseRef:
    code: str


@dataclass(frozen=True)
class Condition:
    text: str


@dataclass(frozen=True)
class AllOf:
    items: tuple["Req", ...]


@dataclass(frozen=True)
class AnyOf:
    items: tuple["Req", ...]


Req = CourseRef | Condition | AllOf | AnyOf


@dataclass(frozen=True)
class Mention:
    kind: str
    code: str
    position: int


@dataclass(frozen=True)
class ParsedRequisites:
    status: str  # none | parsed | ambiguous | unparsed
    mentions: tuple[Mention, ...]
    trees: dict[str, Req]
    leftover: tuple[str, ...]


def _kind_for_label(label: str) -> str:
    label = label.lower()
    if "recommended" in label:
        return "recommended"
    if "co" in label:  # co-requisite / corequisite
        return "corequisite"
    return "prerequisite"


def _strip_grades(text: str) -> str:
    return _GRADE_RE.sub("", _GRADE_RE2.sub("", text))


class _Ambiguous(Exception):
    pass


def _scan(segment: str) -> list[tuple[str, str]]:
    """Single pass. Emits (code, value) | (op, and|or|comma) | (cond, text)
    | (paren, ()|) | (leftover, text). Bare numbers inherit the last subject."""
    tokens: list[tuple[str, str]] = []
    last_subject: str | None = None
    previous_end = -1
    i = 0
    while i < len(segment):
        char = segment[i]
        if char.isspace():
            i += 1
            continue
        if char == ",":
            tokens.append(("op", "comma"))
            i += 1
            continue
        if char == "/":
            tokens.append(("op", "or"))
            i += 1
            continue
        if char in "()":
            tokens.append(("paren", char))
            i += 1
            continue
        match = _AND_OR.match(segment, i)
        if match:
            tokens.append(("op", match.group(1).lower()))
            i = match.end()
            continue
        matched_condition = False
        for pattern, canonical in _CONDITIONS:
            match = pattern.match(segment, i)
            if match:
                # canonical None means: keep the matched phrase verbatim, so
                # "junior standing" does not collapse to just "standing".
                tokens.append(("cond", canonical or match.group(0).lower()))
                i = match.end()
                matched_condition = True
                break
        if matched_condition:
            continue
        match = _CODE_RE.match(segment, i)
        if match:
            if match.group(1):
                subject, tail = match.group(1), match.group(2)
            else:
                if last_subject is None:
                    tokens.append(("leftover", match.group(0)))
                    i = match.end()
                    continue
                between = segment[previous_end:match.start()]
                if _CARRY_SEP.fullmatch(between) is None:
                    tokens.append(("leftover", match.group(0)))
                    i = match.end()
                    continue
                subject, tail = last_subject, match.group(3)
            code = normalize_code(f"{subject} {tail}")
            if code is None:
                tokens.append(("leftover", match.group(0)))
            else:
                tokens.append(("code", code))
                last_subject = subject.upper()
                previous_end = match.end()
            i = match.end()
            continue
        match = re.match(r"[A-Za-z]+", segment[i:])
        if match:
            tokens.append(("leftover", match.group(0)))
            i += match.end()
            continue
        tokens.append(("leftover", char))
        i += 1
    return tokens


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]]):
        self.tokens = tokens
        self.pos = 0

    def _peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def parse(self) -> Req:
        expr = self._expr()
        if self.pos != len(self.tokens):
            raise _Ambiguous("trailing tokens")
        return expr

    def _expr(self) -> Req:
        items = [self._term()]
        explicit: set[str] = set()
        saw_separator = False
        while True:
            token = self._peek()
            if token is None or token[0] != "op":
                break
            saw_separator = True
            if token[1] == "comma":
                self.pos += 1
                following = self._peek()
                if following is not None and following[0] == "op" and following[1] in {
                    "and",
                    "or",
                }:
                    explicit.add(following[1])
                    self.pos += 1
            else:
                explicit.add(token[1])
                self.pos += 1
            items.append(self._term())

        if len(explicit) > 1:
            raise _Ambiguous("mixed and/or")
        if not explicit and saw_separator:
            raise _Ambiguous("comma list without a conjunction")
        if len(items) == 1:
            return items[0]
        if explicit == {"or"}:
            return AnyOf(tuple(items))
        return AllOf(tuple(items))

    def _term(self) -> Req:
        token = self._peek()
        if token is None:
            raise _Ambiguous("unexpected end")
        if token == ("paren", "("):
            self.pos += 1
            expr = self._expr()
            if self._peek() != ("paren", ")"):
                raise _Ambiguous("unbalanced parens")
            self.pos += 1
            return expr
        if token[0] == "cond":
            self.pos += 1
            return Condition(token[1])
        if token[0] == "code":
            self.pos += 1
            return CourseRef(token[1])
        raise _Ambiguous(f"unexpected token {token!r}")


def parse_requisites(text: str | None) -> ParsedRequisites:
    if text is None or not text.strip():
        return ParsedRequisites("none", (), {}, ())

    stripped = text.strip()
    labels = list(_LABEL_RE.finditer(stripped))
    segments: list[tuple[str, str]] = []
    if not labels:
        segments.append(("prerequisite", stripped))
    else:
        if labels[0].start() > 0:
            segments.append(("prerequisite", stripped[: labels[0].start()]))
        for i, label in enumerate(labels):
            kind = _kind_for_label(label.group(1))
            end = labels[i + 1].start() if i + 1 < len(labels) else len(stripped)
            segments.append((kind, stripped[label.end():end]))

    mentions: list[Mention] = []
    seen: set[tuple[str, str]] = set()
    trees: dict[str, Req] = {}
    leftover: list[str] = []
    ambiguous = False
    unparsed = False

    for kind, raw in segments:
        segment = _strip_grades(raw).strip().strip(".").strip()
        if not segment:
            continue
        tokens = _scan(segment)
        for ttype, value in tokens:
            if ttype == "code":
                key = (kind, value)
                if key not in seen:
                    seen.add(key)
                    mentions.append(Mention(kind, value, len(mentions)))
        leftovers = [value for ttype, value in tokens if ttype == "leftover"]
        if leftovers:
            leftover.extend(leftovers)
            unparsed = True
            continue
        try:
            tree = _Parser(tokens).parse()
        except _Ambiguous:
            ambiguous = True
            continue
        if kind in trees:
            ambiguous = True
            continue
        trees[kind] = tree

    if unparsed:
        status = "unparsed"
    elif ambiguous:
        status = "ambiguous"
    else:
        status = "parsed"
    return ParsedRequisites(status, tuple(mentions), trees, tuple(leftover))
