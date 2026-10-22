"""Deterministic prerequisite parser. No model, no inference, no guessing.

Pure functions over the atlas `requisites` text. Every course code mentioned is
recorded as a mention (always); the boolean tree is emitted only when the parse
is unambiguous. Mixed and/or precedence is never chosen: such input is
`ambiguous`. A comma list with no explicit conjunction is ambiguous too.
"""

import re
from dataclasses import dataclass

from app.atlas.records import normalize_code
from app.programs.requirements import (
    _NOT_SUBJECTS,
    SUBJECT_ALIASES,
    _is_level_descriptor,
)

PARSER_VERSION = "9"

# A two-letter suffix (AAS 490RW) and a 6-7 letter subject (DATASCI 355) must be
# uppercase, so ordinary words and "111or" are not read as codes. Spelled-out
# subjects ("Physics 151") and the catalog's spaced Oxford form ("ENVS OX 230")
# are resolved through the program parser's shared aliases. A subject that is
# not recognized is never emitted as a code.
_DIGITS = r"\d{2,4}(?:[A-Z][A-Z0-9]{0,7}|[a-z][A-Z]?)?"
_SPELLING_ALIASES = "|".join(
    re.escape(alias)
    for alias in sorted(
        (alias for alias in SUBJECT_ALIASES if re.fullmatch(r"[A-Z][a-z]+", alias)),
        key=len,
        reverse=True,
    )
)
_SUBJECT = (rf"(?:[A-Z][A-Z0-9]{{0,5}}[A-Z]|(?:{_SPELLING_ALIASES}))"
            r"(?:_OX|\s+OX(?=\s?\d))?")
_CODE_RE = re.compile(rf"\b({_SUBJECT})\s?({_DIGITS})\b|\b({_DIGITS})\b")
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
_CARRY_SEP = re.compile(r"\s*(?:(?:,|/|and|or)\s*)*", re.IGNORECASE)
_INLINE_LABEL_RE = re.compile(
    r"\s+as\s+(?:an?\s+)?"
    r"(recommended\s+preparation|recommended|prerequisite\s+or\s+co[ -]?requisite|"
    r"co[ -]?requisites?|pre[ -]?requisites?)\b",
    re.IGNORECASE,
)
_EXCLUSION_RE = re.compile(
    r"(?:^|(?<=[.!?])\s*)[^.!?]*\bcannot\s+(?:enroll|take)\b[^.!?]*[.!?]?",
    re.IGNORECASE,
)
_PROHIBITS_RE = re.compile(r"\bPROHIBITS\s*:.*", re.IGNORECASE | re.DOTALL)
_USED_FOR_RE = re.compile(r"\bUsed\s+for\s*:.*", re.IGNORECASE | re.DOTALL)


_ALIAS_LOWER = {key.lower(): value for key, value in SUBJECT_ALIASES.items()}


def _resolve_subject(word: str) -> str | None:
    """Resolve a catalog subject or alias; unknown words are never codes."""
    word = re.sub(r"\s+OX$", "_OX", word.strip())
    if word.upper() in _NOT_SUBJECTS:
        return None
    if word in SUBJECT_ALIASES:
        return SUBJECT_ALIASES[word]
    alias = _ALIAS_LOWER.get(word.lower())
    if alias is not None:
        return alias
    if re.fullmatch(r"[A-Z][A-Z0-9]{0,5}[A-Z](?:_OX)?", word):
        return word
    return None


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


def _trim_separators(text: str) -> str:
    return re.sub(r"^(?:and|or|[,;])\s+", "", text.strip(), flags=re.IGNORECASE).strip(" ,.;")


def _split_inline(kind: str, content: str) -> list[tuple[str, str]]:
    matches = list(_INLINE_LABEL_RE.finditer(content))
    if not matches:
        return [(kind, content)]
    output: list[tuple[str, str]] = []
    start = 0
    for match in matches:
        chunk = _trim_separators(content[start:match.start()])
        if chunk:
            output.append((_kind_for_label(match.group(1)), chunk))
        start = match.end()
    tail = _trim_separators(content[start:])
    if tail:
        output.append((kind, tail))
    return output


def split_requisite_segments(text: str) -> tuple[tuple[str, str], ...]:
    """Split a requisites blob into (kind, text) segments.

    Handles both colon labels and the catalog's trailing ``as a corequisite``
    form. Prohibitions, enrollment exclusions, and ``Used for:`` block trailers
    are not requirements and are removed before scanning.
    """
    stripped = re.sub(r"^This course requires\s+", "", text.strip(), flags=re.IGNORECASE)
    stripped = _RUN_TOGETHER_RE.sub(r"\1 \2 ", stripped)
    stripped = _TRANSFER_AS_LABEL_RE.sub(r"\1", stripped)
    stripped = _COMBINED_LABEL_RE.sub("co-requisite: ", stripped)
    stripped = _PROHIBITS_RE.sub("", stripped)
    stripped = _EXCLUSION_RE.sub("", stripped)
    stripped = _USED_FOR_RE.sub("", stripped)
    labels = list(_LABEL_RE.finditer(stripped))
    segments: list[tuple[str, str]] = []
    if not labels:
        segments.extend(_split_inline("prerequisite", stripped))
    else:
        prefix = _trim_separators(stripped[:labels[0].start()])
        if prefix:
            segments.extend(_split_inline("prerequisite", prefix))
        for index, label in enumerate(labels):
            kind = _kind_for_label(label.group(1))
            end = labels[index + 1].start() if index + 1 < len(labels) else len(stripped)
            content = _trim_separators(stripped[label.end():end])
            if content:
                segments.extend(_split_inline(kind, content))
    return tuple((kind, content) for kind, content in segments if content)


# "BIOL 151or", "PHYS 461or": a missing space glues a code to the conjunction.
_RUN_TOGETHER_RE = re.compile(r"(\d{3}[A-Za-z]?)(and|or)(?=\d|\b)", re.IGNORECASE)
_TRANSFER_AS_LABEL_RE = re.compile(
    r"(equivalent\s+transfer\s+credit)\s+as\s+(?:an?\s+)?"
    r"prerequisites?(?:\s+or\s+co[ -]?requisites?)?\b",
    re.IGNORECASE,
)
# "Prerequisite or co-requisite" is one combined label; corequisite is the
# weaker (concurrent) interpretation.
_COMBINED_LABEL_RE = re.compile(
    r"\bpre[ -]?requisite\s+or\s+co[ -]?requisite\s*:?\s*", re.IGNORECASE)
# A trailing "... or equivalent transfer credit ..." is a modifier, not an `or`
# operand; keeping it as an operand makes mixed and/or look ambiguous (EMO-028).
# Only a clause that ends the segment qualifies, so a following restriction
# sentence (CHEM 340) is never dropped.
_TRANSFER_TAIL_RE = re.compile(
    r"\s*(?:[,;]\s*)?(?P<connector>or|and)?\s*equivalent\s+transfer\s+credit"
    r"(?:\s+as\s+(?:a\s+)?prerequisites?"
    r"(?:\s+or\s+co[ -]?requisites?)?)?\s*\.?\s*\Z",
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
                resolved = _resolve_subject(match.group(1))
                if resolved is None:
                    # Do not let a following bare number inherit an earlier
                    # subject across an unrecognized word ("... and lab 201").
                    last_subject = None
                    tokens.append(("leftover", match.group(0)))
                    i = match.end()
                    continue
                subject, tail = resolved, match.group(2)
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
            if _is_level_descriptor(segment, match.end()):
                i = match.end()
                continue
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
    return _attach_transfer_condition(tree, tail.group("connector"))


def _attach_transfer_condition(tree: Req, connector: str | None) -> Req:
    condition = Condition("equivalent transfer credit")
    connector = (connector or "").lower()
    # Unmarked legacy tails retain the parser's prior conservative behavior:
    # a single course or an alternative list gains the condition as one more
    # alternative; anything else requires it.
    if connector == "or" or (not connector and isinstance(tree, (CourseRef, AnyOf))):
        items = tree.items if isinstance(tree, AnyOf) else (tree,)
        return AnyOf((*items, condition))
    items = tree.items if connector == "and" and isinstance(tree, AllOf) else (tree,)
    return AllOf((*items, condition))


def parse_requisites(
    text: str | None, *, course_code: str | None = None
) -> ParsedRequisites:
    if text is None or not text.strip():
        return ParsedRequisites("none", (), {}, ())

    segments = split_requisite_segments(text)
    if not segments:
        return ParsedRequisites("none", (), {}, ())

    mentions: list[Mention] = []
    seen: set[tuple[str, str]] = set()
    trees: dict[str, Req] = {}
    leftover: list[str] = []
    ambiguous = False
    unparsed = False
    self_code = normalize_code(course_code) if course_code else None

    for kind, raw in segments:
        segment = _strip_grades(raw).strip().strip(".").strip()
        if not segment:
            continue
        transfer_tail = _TRANSFER_TAIL_RE.search(segment)
        if transfer_tail is not None:
            segment = segment[:transfer_tail.start()].strip()
            if not segment:
                continue
        tokens = _scan(segment)
        if self_code is not None and any(
            ttype == "code" and value == self_code for ttype, value in tokens
        ):
            # A course is never its own prerequisite. If the source still says
            # so after prohibition/trailer stripping, fail closed.
            ambiguous = True
            continue
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
        if transfer_tail is not None:
            tree = _attach_transfer_condition(tree, transfer_tail.group("connector"))
        trees[kind] = tree

    if unparsed:
        status = "unparsed"
    elif ambiguous:
        status = "ambiguous"
    else:
        status = "parsed"
    return ParsedRequisites(status, tuple(mentions), trees, tuple(leftover))
