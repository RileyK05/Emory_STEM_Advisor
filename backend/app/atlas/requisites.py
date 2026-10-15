"""Deterministic prerequisite parser. No model, no inference, no guessing.

Pure functions over the atlas `requisites` text. Every course code mentioned is
recorded as a mention (always); the boolean tree is emitted only when the parse
is unambiguous. Mixed and/or precedence is never chosen: such input is
`ambiguous`. A comma list with no explicit conjunction is ambiguous too.
"""

import re
from dataclasses import dataclass

from app.atlas.records import normalize_code

PARSER_VERSION = "5"

# A two-letter suffix (AAS 490RW) and a 6-7 letter subject (DATASCI 355) must be
# uppercase, so ordinary words and "111or" are not read as codes. The catalog's
# mixed-case suffix (202zL) is allowed, matching the program parser.
_DIGITS = r"\d{3}(?:[A-Z]{1,2}|[a-z][A-Z]?)?"
_SUBJECT = r"(?:[A-Z]{6,7}|[A-Za-z]{2,5})(?:_OX)?"
_CODE_RE = re.compile(rf"({_SUBJECT})\s?({_DIGITS})\b|\b({_DIGITS})\b")
_GRADE_RE = re.compile(
    r"with\s+(?:a\s+)?(?:minimum\s+)?grade\s+of\s+[A-D][+-]?\s+or\s+(?:better|higher)",
    re.IGNORECASE,
)
_GRADE_RE2 = re.compile(
    r"with\s+(?:a\s+)?[A-D][+-]?\s+or\s+(?:better|higher)", re.IGNORECASE
)
_LABEL_RE = re.compile(
    r"\b(recommended\s+preparation|recommended|co[ -]?requisites?|pre[ -]?requisites?)\s*:\s*",
    re.IGNORECASE,
)
_CONDITIONS = [
    (re.compile(r"(permission|consent)\s+of\s+(the\s+)?instructor", re.IGNORECASE),
     "permission of instructor"),
    (re.compile(r"equivalent\s+transfer\s+credit\s+as\s+(?:a\s+)?prerequisites?\b", re.IGNORECASE),
     "equivalent transfer credit"),
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


def requisite_text(raw: str | None, description: str) -> str | None:
    """Use the official field, or an explicitly labeled description tail.

    Preserve every word after the label. Cutting at the first sentence could
    drop additional restrictions; unfamiliar prose must fail parsing instead.
    An existing structured requisite field always retains precedence.
    """
    if raw and raw.strip():
        return raw
    match = re.search(r"\b(?:pre|co)[ -]?requisites?\s*:", description, re.IGNORECASE)
    return description[match.start():].strip() if match else raw


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


# "BIOL 151or", "PHYS 461or": a missing space glues a code to the conjunction.
_RUN_TOGETHER_RE = re.compile(r"(\d{3}[A-Za-z]?)(and|or)\b", re.IGNORECASE)
# "Prerequisite or co-requisite" is one combined label; corequisite is the
# weaker (concurrent) interpretation.
_COMBINED_LABEL_RE = re.compile(
    r"\bpre[ -]?requisite\s+or\s+co[ -]?requisite\s*:?\s*", re.IGNORECASE)
# A trailing "... or equivalent transfer credit ..." is a modifier, not an `or`
# operand; keeping it as an operand makes mixed and/or look ambiguous (EMO-028).
# Only a clause that ends the segment qualifies, so a following restriction
# sentence (CHEM 340) is never dropped.
_TRANSFER_TAIL_RE = re.compile(
    r"\s*(?:,|;|or|and)?\s*equivalent\s+transfer\s+credit\b[^.]*\.?\s*\Z",
    re.IGNORECASE)


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
        if char in "()[]":
            tokens.append(("paren", {"[": "(", "]": ")"}.get(char, char)))
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


def _parse_with_transfer_tail(segment: str) -> Req | None:
    """Retry a segment whose only ambiguity is a trailing transfer-credit clause.

    "(A or A_OX) and (B or B_OX) or equivalent transfer credit as a prerequisite"
    mixes and/or only because the transfer clause is written as another `or`
    operand. Strip that trailing clause and attach it as a modifier instead
    (EMO-028). A segment that stays mixed after stripping, or has no such tail,
    keeps its ambiguity.
    """
    tail = _TRANSFER_TAIL_RE.search(segment)
    if tail is None:
        return None
    prefix = segment[: tail.start()].strip().strip(",").strip()
    if not prefix:
        return None
    tokens = _scan(prefix)
    if any(ttype == "leftover" for ttype, _ in tokens):
        return None
    try:
        tree = _Parser(tokens).parse()
    except _Ambiguous:
        return None
    return AllOf((tree, Condition("equivalent transfer credit")))


def parse_requisites(text: str | None) -> ParsedRequisites:
    if text is None or not text.strip():
        return ParsedRequisites("none", (), {}, ())

    stripped = text.strip()
    # This catalog wrapper introduces the field; it has no prerequisite meaning.
    stripped = re.sub(r"^This course requires\s+", "", stripped, flags=re.IGNORECASE)
    # Repair run-together codes and normalize the combined label (EMO-028).
    stripped = _RUN_TOGETHER_RE.sub(r"\1 \2", stripped)
    stripped = _COMBINED_LABEL_RE.sub("co-requisite: ", stripped)
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
            tree = _parse_with_transfer_tail(segment)
            if tree is None:
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
