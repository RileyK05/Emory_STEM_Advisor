"""Deterministic prerequisite parser. No model, no inference, no guessing.

Pure functions over the atlas `requisites` text. Every course code mentioned is
recorded as a mention (always); the boolean tree is emitted only when the parse
is unambiguous. Mixed and/or precedence is never chosen: such input is
`ambiguous`. A comma list with no explicit conjunction is ambiguous too.
"""

import re
from dataclasses import dataclass, replace

from app.atlas.records import normalize_code
from app.programs.requirements import (
    _NOT_SUBJECTS,
    SUBJECT_ALIASES,
    _is_level_descriptor,
)

PARSER_VERSION = "24"

# A two-letter suffix (AAS 490RW) and a 6-7 letter subject (DATASCI 355) must be
# uppercase, so ordinary words and "111or" are not read as codes. Spelled-out
# subjects ("Physics 151") and the catalog's spaced Oxford form ("ENVS OX 230")
# are resolved through the program parser's shared aliases. Two miscased forms
# are read as codes: a title-case subject before an Oxford suffix ("Psyc_ox 110",
# "Math_OX 112"), and an uppercase subject with a stray lowercase last letter
# right before its number ("BIOl 142L"). A subject that is not recognized is
# never emitted as a code.
_DIGITS = r"\d{2,4}(?:[A-Z][A-Z0-9]{0,7}|[a-z][A-Z]?)?"
_SPELLING_ALIASES = "|".join(
    re.escape(alias)
    for alias in sorted(
        (alias for alias in SUBJECT_ALIASES if re.fullmatch(r"[A-Z][a-z]+", alias)),
        key=len,
        reverse=True,
    )
)
_SUBJECT = (rf"(?:[A-Z][A-Z0-9]{{0,5}}[A-Z]|[A-Z]{{3,6}}[a-z](?=\s?\d)"
            rf"|[A-Z][a-z]{{2,6}}(?=_(?:OX|ox)\b)|(?:{_SPELLING_ALIASES}))"
            r"(?:_(?:OX|ox)|\s+OX(?=\s?\d))?")
_CODE_RE = re.compile(rf"\b({_SUBJECT})\s?({_DIGITS})\b|\b({_DIGITS})\b")
_GRADE_RE = re.compile(
    r"with\s+(?:a\s+)?(?:minimum\s+)?grade\s+of\s+[A-D][+-]?\s+or\s+(?:better|higher)",
    re.IGNORECASE,
)
_GRADE_RE2 = re.compile(
    r"with\s+(?:a\s+)?[A-D][+-]?\s+or\s+(?:better|higher)", re.IGNORECASE
)
_LABEL_RE = re.compile(
    r"\b(?:(recommended\s+preparation|recommended|co[ -]?requisites?|pre[ -]?requisites?)\s*:\s*"
    # A colon-less label counts only when a course code follows it directly.
    r"|(pre[ -]?requisites?)\s+(?=(?-i:[A-Z][A-Z0-9]{0,5}[A-Z](?:_OX)?\s?\d)))",
    re.IGNORECASE,
)
_CONDITIONS = [
    (re.compile(r"(permission|consent)\s+of\s+(the\s+)?instructor(?!\s+and\b)", re.IGNORECASE),
     "permission of instructor"),
    # Someone else's approval, kept verbatim: "approval of adviser and the director
    # of undergraduate studies". It ends with its clause.
    (re.compile(r"(?:prior\s+|written\s+)?(?:permission|consent|approval)\s+(?:of|by|from)\s+"
                r"[^.;,()]{3,90}?(?=\s*(?:[.;,)]|\bor\b|$))", re.IGNORECASE), None),
    (re.compile(r"(?:the\s+)?instructor(?:'s)?\s+consent\b", re.IGNORECASE),
     "permission of instructor"),
    # "Two PHIL courses", "one 300 level SPAN or SPAN_OX course", "Any NBB or WGS
    # course", "One HIST course 200 level or above", "two courses in literature".
    (re.compile(r"(?:at\s+least\s+)?(?:any|one|two|three|four|five)\s+(?:\d{3}[- ]level\s+)?"
                r"(?:[A-Za-z_]+\s+(?:or\s+[A-Za-z_]+\s+)?)??(?:courses?|class(?:es)?)\b"
                r"(?:\s+(?:in\s+[a-z]+|\d{3}\s+level\s+or\s+above))?", re.IGNORECASE), None),
    (re.compile(r"any\s+(?-i:[A-Z_]{2,})\s+or\s+(?-i:[A-Z_]{2,})(?=\s+or\b|\s*$)",
                re.IGNORECASE), None),
    (re.compile(r"\d{3}[- ]level\s+[a-z]+\s+(?:course|class)\b", re.IGNORECASE), None),
    (re.compile(r"(?-i:[A-Z]{2,7})\s+course\s+at\s+the\s+\d{3}\s+level", re.IGNORECASE), None),
    # A placement or an exam score stands in for a course.
    (re.compile(r"(?:AP|IB)\s+(?:[A-Z][a-z]+\s+)?score\s+of\s+\d(?:\s*,\s*\d\s*,?)?"
                r"(?:\s+or\s+(?:\d|higher))?", re.IGNORECASE), None),
    (re.compile(r"AP\s+Calculus\s+(?:BC|AB)\b"), None),
    (re.compile(r"(?-i:[A-Z]{2,7})\s+AP/AB\s+or\s+IB\s+credits?\b", re.IGNORECASE), None),
    (re.compile(r"(?:by\s+)?placement(?:\s+recommendation)?\b", re.IGNORECASE), None),
    (re.compile(r"academic\s+eligibility\b|admission\s+to\s+the\s+honors\s+program\b"
                r"|qualification\s+for\s+honors\b", re.IGNORECASE), None),
    (re.compile(r"juniors?\s+or\s+seniors?\s+only\b", re.IGNORECASE), None),
    # A student group or a transfer placeholder that Atlas lists as an alternative.
    (re.compile(r"(?-i:[A-Z]{4,})\s+students?\b", re.IGNORECASE), None),
    (re.compile(r"(?-i:[A-Z]{2,7}(?:_OX)?\s+(?:ELEC|XFR))\b"), None),
    (re.compile(r"equivalent\s+transfer\s+credits?\s+as\s+(?:a\s+)?prerequisites?\b",
                re.IGNORECASE),
     "equivalent transfer credit"),
    # As a plain operand, so "CHEM 221, CHEM 221Z, CHEM_OX 221, or equivalent
    # transfer credit" reads as one Oxford-comma list.
    (re.compile(r"equivalent\s+transfer\s+credits?\b", re.IGNORECASE),
     "equivalent transfer credit"),
    (re.compile(r"applicable\s+transfer\s+credits?\b", re.IGNORECASE),
     "applicable transfer credit"),
    (re.compile(r"equivalent", re.IGNORECASE), "equivalent"),
    (re.compile(r"(freshman|sophomore|junior|senior)\s+standing", re.IGNORECASE),
     None),  # canonical text is the matched phrase, so the year is kept
]
_AND_OR = re.compile(r"(and|or)\b", re.IGNORECASE)
# Between a code and a bare number, only these separators are allowed for the
# subject to carry forward: whitespace, commas, slashes, and/or. So
# "MATH 111, 112, or 115" carries MATH across ", " and ", or ".
_CARRY_SEP = re.compile(r"\s*(?:(?:,|/|and|or)\s*)*", re.IGNORECASE)
# A passage that lists codes carries the subject further than a requirement
# does: "CHEM 202Z + 202ZL; MATH 411-412" names both labs and both courses.
# The identity reader asks which codes a passage names, not what it requires.
_IDENTITY_CARRY_SEP = re.compile(r"\s*(?:(?:,|/|&|\+|-|–|and|or|plus)\s*)*", re.IGNORECASE)
_INLINE_LABEL_RE = re.compile(
    r"\s+as\s+(?:an?\s+)?"
    r"(recommended\s+preparation|recommended|prerequisite\s+or\s+co[ -]?requisite|"
    r"co[ -]?requisites?|pre[ -]?requisites?)\b"
    # Oxford's bare form, "CHEM_OX 150 prerequisites & CHEM_OX 202L co-requisite",
    # counts only where the label closes a clause.
    r"|\s+(co[ -]?requisites?|pre[ -]?requisites?)(?=\s*(?:[.,;&]|\b(?:and|or)\b|$))",
    re.IGNORECASE,
)
_EXCLUSION_RE = re.compile(
    r"(?:^|(?<=[.!?])\s*)[^.!?]*\b(?:cannot\s+(?:enroll|take)|may\s+not\s+enroll"
    r"|not\s+allowed\s+to\s+enroll|can\s+enroll\s+only\s+in)\b[^.!?]*[.!?]?",
    re.IGNORECASE,
)
# Who may enroll is an enrollment restriction, not a course requirement:
# "HPMMPH or HPMMPHOL students only.", "This section is reserved for students in
# the Executive MBA program.", "MB program students". A sentence with a digit
# may name a course and is never removed by this.
#
# The distinctive wording is matched wherever it stands, because the catalog
# runs two enrollment statements together without punctuation ("BBA or
# SPECSTUBUS students This section is reserved for students in the BBA
# program."). The general wording ("students only") is matched only at a
# sentence boundary, so a course code in the same sentence keeps it.
_PROGRAM_ONLY_RE = re.compile(
    r"(?:"
    r"This\s+section\s+is\s+reserved\s+for\s+[^.!?;\d]*\bprograms?"
    r"|Program\s+restrictions\s+apply\b[^.!?;\d]*"
    r"|Only\s+[^.!?;\d]*\bhave\s+permission\s+to\s+enroll\b[^.!?;\d]*"
    r"|(?:^\s*|(?<=[.!?;])\s*)[^.!?;\d]*\bstudents?\s+(?:can|may|(?:are\s+)?allowed\s+to)\s+enroll\b"
    r"[^.!?;\d]*"
    r"|(?:^\s*|(?<=[.!?;])\s*)[^.!?;\d]*\bstudents\s+only"
    r"|(?:^\s*|(?<=[.!?;])\s*)[^.!?;\d]*\bprogram\s+students"
    r"|(?:^\s*|(?<=[.!?;])\s*)(?-i:[A-Z][A-Z&]{2,})"
    r"(?:\s+or\s+(?-i:[A-Z][A-Z&]{2,}))*\s+students\b[^.!?;\d]*"
    r")\s*(?:[.!?]|$)",
    re.IGNORECASE,
)
# Atlas's placeholder when a course's sections state their own requirements.
_VARIES_RE = re.compile(r"^\s*Varies\s+by\s+section\s*\.?\s*$", re.IGNORECASE)
_PROHIBITS_RE = re.compile(r"\bPROHIBITS\s*:.*", re.IGNORECASE | re.DOTALL)
_USED_FOR_RE = re.compile(r"\bUsed\s+for\s*:.*", re.IGNORECASE | re.DOTALL)
# "For concerns, please contact the Mathematics department." follows an exclusion
# (MATH 111, PSYC 111); it points to help, it is not a requirement.
_CONTACT_RE = re.compile(
    r"(?:^|(?<=[.!?]))\s*For\s+(?:concerns|questions)\s*,\s*please\s+contact\s+the\s+"
    r"[^.!?]*?\bdepartment\s*\.?", re.IGNORECASE)
# "May be repeated for a maximum of eight hours credit." limits repeats, not entry.
_REPEAT_RE = re.compile(
    r"(?:^|(?<=[.!?])\s*)May\s+be\s+repeated\b[^.!?]*[.!?]?", re.IGNORECASE)


_ALIAS_LOWER = {key.lower(): value for key, value in SUBJECT_ALIASES.items()}


def _resolve_subject(word: str) -> str | None:
    """Resolve a catalog subject or alias; unknown words are never codes."""
    word = re.sub(r"(?:_|\s+)OX$", "_OX", word.strip(), flags=re.IGNORECASE)
    base, oxford = (word[:-3], "_OX") if word.endswith("_OX") else (word, "")
    if base.upper() in _NOT_SUBJECTS:
        return None
    if oxford and re.fullmatch(r"[A-Z][A-Z0-9]{0,5}[A-Z]", base):
        # Oxford keeps its own subjects (ANTH_OX, not the Atlanta ANT).
        return base + oxford
    if oxford and re.fullmatch(r"[A-Z][a-z]{2,6}", base):
        # Title-case Oxford subjects ("Anth_ox", "Psyc_ox") keep Oxford's
        # spelling too; alias expansion would rewrite ANTH to Atlanta's ANT.
        return base.upper() + oxford
    if base in SUBJECT_ALIASES:
        return SUBJECT_ALIASES[base] + oxford
    alias = _ALIAS_LOWER.get(base.lower())
    if alias is not None:
        return alias + oxford
    if re.fullmatch(r"[A-Z][A-Z0-9]{0,5}[A-Z]", base) or re.fullmatch(r"[A-Z]{3,6}[a-z]", base):
        return base.upper() + oxford
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
    starts = [match.start() for pattern in (_DESCRIPTION_LABEL_RE, _DESCRIPTION_CLOSING_RE)
              if (match := pattern.search(description))]
    if not starts:
        return raw
    start = min(starts)
    # A restriction stated before the requirement ("written permission of
    # instructor required before registration.") is kept, so it fails parsing
    # rather than disappearing.
    restriction = _DESCRIPTION_RESTRICTION_RE.search(description, 0, start)
    return description[restriction.start() if restriction else start:].strip()


# A course code's start, case-sensitive inside otherwise case-insensitive labels.
_CODE_AHEAD = r"(?-i:[A-Z][A-Z0-9]{0,5}[A-Z](?:_OX)?\s?\d)"
# "Prerequisite: X", "Prerequisites/concurrent: X", or a colon-less label that
# runs straight into a course code ("Prerequisites EPI 530, ...").
_DESCRIPTION_LABEL_RE = re.compile(
    r"\b(?:pre|co)[ -]?requisites?(?:\s*/\s*(?:concurrent|co[ -]?requisites?))?\s*:"
    rf"|\bpre[ -]?requisites?\s+(?={_CODE_AHEAD})",
    re.IGNORECASE)
# A sentence naming courses and closing with a label that binds them to this
# course ("ENGCW 270, 271, or 272 required as prerequisite."). Forward
# references ("... is a prerequisite for CHEM 202") never match.
_DESCRIPTION_CLOSING_RE = re.compile(
    rf"(?:^|(?<=[.!?])\s+)(?=[^.!?]*{_CODE_AHEAD})[^.!?]*?(?:"
    r"\brequired\s+as\s+(?:an?\s+)?pre[ -]?requisites?\b(?!\s+for\b)"
    r"|\bis\s+(?:a\s+)?pre[ -]?requisite\s+(?:to|for)\s+(?:tak(?:e|ing)\s+)?this\b)",
    re.IGNORECASE)
_DESCRIPTION_RESTRICTION_RE = re.compile(
    # "Offered as required" schedules the course; it restricts no one.
    r"(?:^|(?<=[.!?;])\s*)(?=[^.!?;]*\b(?:permission|consent|(?<!as )required|restricted|must)\b)",
    re.IGNORECASE)


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
    return re.sub(r"^(?:and|or|[,;&])\s+", "", text.strip(), flags=re.IGNORECASE).strip(" ,.;")


# The conjunction that joins a part to the one before it ("..., and BIOL 370").
_JOINER_RE = re.compile(r"[\s,;&]*(?:(and|or)\b)?", re.IGNORECASE)


def _joined(text: str) -> tuple[str, str]:
    match = _JOINER_RE.match(text)
    return (match.group(1) or "").lower(), _trim_separators(text[match.end():])


def _split_inline(kind: str, content: str) -> list[tuple[str, str, str]]:
    """Split inline labels into (kind, text, joiner) parts.

    The joiner is the and/or that connects a part to the previous one, or "".
    """
    matches = list(_INLINE_LABEL_RE.finditer(content))
    if not matches:
        return [(kind, content, "")]
    output: list[tuple[str, str, str]] = []
    start = 0
    for match in matches:
        joiner, chunk = _joined(content[start:match.start()])
        if chunk:
            output.append((_kind_for_label(match.group(1) or match.group(2)), chunk, joiner))
        start = match.end()
    joiner, tail = _joined(content[start:])
    if len(output) == 1 and _TRANSFER_ONLY_RE.fullmatch(tail):
        # "ENVS 240 as co-requisite, or equivalent transfer credit": with one
        # labelled part the alternative modifies it.
        last_kind, last, last_joiner = output[-1]
        output[-1] = (last_kind, f"{last} {joiner or 'or'} equivalent transfer credit",
                      last_joiner)
    elif (len(output) > 1 and joiner == "or" and _TRANSFER_ONLY_RE.fullmatch(tail)
          and all(part[2] == "and" for part in output[1:])):
        # "A as prerequisite and B as co-requisite, or equivalent transfer credit":
        # (A and B) or T equals (A or T) and (B or T), so the alternative joins
        # every part. A condition never satisfies a requirement by itself, so
        # each course-only path is the same under any reading of its scope.
        output = [(part_kind, f"{text} or equivalent transfer credit", part_joiner)
                  for part_kind, text, part_joiner in output]
    elif tail:
        output.append((kind, tail, joiner))
    return output


_TRANSFER_ONLY_RE = re.compile(r"(?:or\s+)?equivalent\s+transfer\s+credits?", re.IGNORECASE)


def split_requisite_segments(
    text: str, *, course_code: str | None = None
) -> tuple[tuple[str, str], ...]:
    """Split a requisites blob into (kind, text) segments.

    Handles both colon labels and the catalog's trailing ``as a corequisite``
    form. Prohibitions, enrollment exclusions, program-only enrollment
    restrictions, and ``Used for:`` block trailers are not requirements and are
    removed before scanning.
    """
    return tuple(dict.fromkeys(
        (kind, content) for kind, content, _joiner in _requisite_parts(text, course_code)))


def _requisite_parts(text: str, course_code: str | None) -> tuple[tuple[str, str, str], ...]:
    stripped = _repair_typing(_REPEATED_SENTENCE_RE.sub(r"\1", text.strip()))
    stripped = _OPENER_RE.sub("", stripped)
    stripped = _MISSPELLED_LABEL_RE.sub(r"prerequisite\1", stripped)
    stripped = _OX_AFTER_NUMBER_RE.sub(r"\1_OX \2", stripped)
    stripped = _SHARED_NUMBER_RE.sub(r"\1 \3/\2 \3", stripped)
    stripped = _FORMER_NUMBER_RE.sub(r"\1 or \2", stripped)
    stripped = re.sub(r"\s+&\s+", " and ", stripped)
    stripped = _RUN_TOGETHER_RE.sub(r"\1 \2 ", stripped)
    stripped = _PRE_CO_SLASH_RE.sub("prerequisite or co-requisite", stripped)
    stripped = _REQUIRED_AS_RE.sub("", stripped)
    self_code = normalize_code(course_code) if course_code else None
    stripped = _ENROLL_TOGETHER_RE.sub(lambda match: _enroll_together(match, self_code),
                                       stripped)
    stripped = _TRANSFER_AS_LABEL_RE.sub(
        lambda match: match.group(1) + (" as corequisite" if match.group(2) else ""),
        stripped,
    )
    stripped = _COMBINED_INLINE_RE.sub(" as corequisite", stripped)
    stripped = _COMBINED_LABEL_RE.sub("co-requisite: ", stripped)
    stripped = _PROHIBITS_RE.sub("", stripped)
    stripped = _EXCLUSION_RE.sub("", stripped)
    stripped = _PROGRAM_ONLY_RE.sub("", stripped)
    stripped = _VARIES_RE.sub("", stripped)
    stripped = _USED_FOR_RE.sub("", stripped)
    stripped = _REPEAT_RE.sub("", stripped)
    stripped = _CONTACT_RE.sub("", stripped)
    stripped = _CROSS_LISTED_RE.sub("", stripped)
    stripped = _ENROLL_FIRST_RE.sub("", stripped)
    stripped = _NO_CREDIT_CLAUSE_RE.sub("", stripped)
    stripped = _REQUIRED_TAIL_RE.sub("", stripped)
    labels = list(_LABEL_RE.finditer(stripped))
    segments: list[tuple[str, str, str]] = []
    if not labels:
        segments.extend(_split_inline("prerequisite", stripped))
    else:
        prefix = _trim_separators(stripped[:labels[0].start()])
        if prefix:
            segments.extend(_split_inline("prerequisite", prefix))
        for index, label in enumerate(labels):
            kind = _kind_for_label(label.group(1) or label.group(2))
            end = labels[index + 1].start() if index + 1 < len(labels) else len(stripped)
            content = _trim_separators(stripped[label.end():end])
            if content:
                segments.extend(_split_inline(kind, content))
    # The same requirement stated twice (a label and an enrollment sentence) is one.
    parts: dict[tuple[str, str], str] = {}
    for kind, content, joiner in segments:
        if content.strip():
            parts.setdefault((kind, content), joiner)
    return tuple((kind, content, joiner) for (kind, content), joiner in parts.items())


# A requirement stated twice back to back (BIOL 260) is stated once.
_REPEATED_SENTENCE_RE = re.compile(r"^(.{20,}?[.!?])\s*\1\s*$", re.DOTALL)
_OPENER_RE = re.compile(r"^(?:This\s+course\s+(?:requires|mandates)|This\s+requisite\s+mandates)\s+",
                        re.IGNORECASE)
_MISSPELLED_LABEL_RE = re.compile(r"\bprerequ(?:iste|site)(s?)\b", re.IGNORECASE)
# Registrar typing that has one reading. Each pair is (wrong, right).
_TYPING_REPAIRS = (
    (re.compile(r"\b([A-Z]{2,7})\s*_\s*OX\b"), r"\1_OX"),
    (re.compile(r"\b([A-Z]{2,7})_(?:X|OC)\b"), r"\1_OX"),
    (re.compile(r"\b([A-Z]{2,7})\?([A-Z]{2,7})\b"), r"\1/\2"),
    (re.compile(r"\b(\d{3}Z)0\]"), r"\1)]"),
    (re.compile(r"\bstudentsor\b"), "students or"),
    (re.compile(r"\btransfer/test\b"), "transfer"),
    (re.compile(r"\bas\s+prereq\b", re.IGNORECASE), "as prerequisite"),
    (re.compile(r"\bPolitical\s+Science\s+(?=\d)"), "POLS "),
)
# "Students must enroll in CHEM 202Z first." orders a corequisite already stated.
_ENROLL_FIRST_RE = re.compile(
    r"\(?\b(?:Students?\s+)?must\s+enroll\s+in\s+[^.)]*\bfirst\b[^.)]*[.)]?", re.IGNORECASE)
# "Cross-listed with GH 562." names the same course, never a requirement on it.
_CROSS_LISTED_RE = re.compile(r"\bCross[- ]?listed\s+with\b[^.]*\.?", re.IGNORECASE)
# "; may not receive credit for NBB 497WR" limits credit, not entry.
_NO_CREDIT_CLAUSE_RE = re.compile(r";\s*(?:may\s+not|cannot)\b[^.!?]*", re.IGNORECASE)
_REQUIRED_TAIL_RE = re.compile(r"\s+required\s*\.?\s*$", re.IGNORECASE)
# What follows the first sentence of a label is course description, unless it
# names a course or says who may enroll. A restriction is never dropped.
_SENTENCE_END_RE = re.compile(r"(?<=[a-z0-9)])\s*[.;]\s*(?=[A-Za-z])")
_RESTRICTION_WORDS_RE = re.compile(
    r"\b(?:open|only|restricted|limited|must|requires?|required|permission|consent|approval"
    r"|eligib\w+|majors?|minors?|candidates?|seniors?|juniors?)\b", re.IGNORECASE)
# "Math 112_OX" puts Oxford's suffix after the number; the code is MATH_OX 112.
_OX_AFTER_NUMBER_RE = re.compile(r"\b([A-Za-z]{2,8})\s+(\d{3}[A-Z]{0,4})_OX\b")
# "ECON _OX 100": a stray space before the campus mark, as twelve Atlas requisites print it.
_SPACED_OX_RE = re.compile(r"\b([A-Za-z]{2,8})\s+_OX\b", re.IGNORECASE)
# "BIOL/ENVS 247" names one cross-listed number under two subjects.
_SHARED_NUMBER_RE = re.compile(
    r"\b([A-Z]{2,7}(?:_OX)?)/([A-Z]{2,7}(?:_OX)?)\s?(\d{3}[A-Z]{0,4})\b")
# "CHEM 221Z (CHEM 171)": a parenthesized code right after a code is its former
# number, an alternative. Anywhere else a parenthesis groups as usual.
_FORMER_NUMBER_RE = re.compile(
    rf"\b([A-Z][A-Z0-9]{{0,5}}[A-Z](?:_OX)?\s?{_DIGITS})\s*"
    rf"\(\s*([A-Z][A-Z0-9]{{0,5}}[A-Z](?:_OX)?\s?{_DIGITS})\s*\)")


# "BIOL 151or", "PHYS 461or": a missing space glues a code to the conjunction.
_RUN_TOGETHER_RE = re.compile(r"(\d{3}[A-Za-z]?)(and|or)(?=\d|\b)", re.IGNORECASE)
_TRANSFER_AS_LABEL_RE = re.compile(
    r"(equivalent\s+transfer\s+credits?)\s+as\s+(?:an?\s+)?"
    r"prerequisites?(\s+or\s+co[ -]?requisites?)?\b",
    re.IGNORECASE,
)
# A single explicit course followed by an inline prerequisite label and an
# OR transfer alternative has an unambiguous scope. Move the label to the end
# before splitting; more complex mixed-kind wording remains unresolved.
_SINGLE_LABELED_TRANSFER_RE = re.compile(
    rf"^\s*(?P<course>{_SUBJECT}\s?{_DIGITS})\s+as\s+(?:an?\s+)?"
    r"prerequisites?\s+or\s+equivalent\s+transfer\s+credit\s*\.?\s*$",
    re.IGNORECASE,
)
# "Prerequisite or co-requisite" is one combined label; corequisite is the
# weaker (concurrent) interpretation.
_COMBINED_LABEL_RE = re.compile(
    r"\bpre[ -]?requisite\s+or\s+co[ -]?requisite\s*:?\s*", re.IGNORECASE)
# The same combined label written inline after its courses ("CHEM 203 as
# prerequisite or co-requisite"); rewriting it to a colon label would orphan them.
_COMBINED_INLINE_RE = re.compile(
    r"\s+as\s+(?:an?\s+)?pre[ -]?requisites?\s+or\s+co[ -]?requisites?\b(?!\s*:)",
    re.IGNORECASE)
_PRE_CO_SLASH_RE = re.compile(
    r"\bpre(?:[ -]?requisites?)?\s*/\s*(?:co[ -]?requisites?|concurrent)\b", re.IGNORECASE)
# "ENGCW 270, 271, or 272 required as prerequisite" is the inline "as prerequisite" form.
_REQUIRED_AS_RE = re.compile(
    r"\s+required(?=\s+as\s+(?:an?\s+)?pre[ -]?requisites?\b(?!\s+for\b))", re.IGNORECASE)
# "Students must enroll in CHEM_OX 202 and CHEM_OX 202L together." states
# concurrent enrollment explicitly.
_CODE_ONLY_RE = re.compile(rf"[A-Z][A-Z0-9]{{0,5}}[A-Z](?:_OX)?\s?{_DIGITS}")
_ENROLL_TOGETHER_RE = re.compile(
    r"\bStudents\s+must\s+enroll\s+in\s+(?P<courses>[^.]+?)\s+together\b\.?"
    r"(?:\s*If\s+not\s+enrolled\s+in\s+(?:both\s+)?(?:the\s+)?lecture\s+and\s+lab\b[^.]*\.?)?",
    re.IGNORECASE)


def _repair_typing(text: str) -> str:
    for wrong, right in _TYPING_REPAIRS:
        text = wrong.sub(right, text)
    # "BIOL 142 and BIOL 142L) or BIOL_OX 142" lost its opening parenthesis.
    return "(" + text if text.count(")") == text.count("(") + 1 else text


def _requirement_sentence(segment: str) -> str:
    """Drop a course description that runs on after the requirement itself."""
    end = _SENTENCE_END_RE.search(segment)
    if end is None:
        return segment
    rest = segment[end.end():]
    if _CODE_ONLY_RE.search(rest) or _RESTRICTION_WORDS_RE.search(rest):
        return segment
    return segment[:end.start()]


def _enroll_together(match: re.Match, self_code: str | None) -> str:
    """Rewrite as a corequisite label naming the other listed courses.

    The course's own code is part of the statement, not a requirement on itself.
    A list that is not purely course codes is left as written.
    """
    parts = [part.strip() for part in re.split(r"\s*(?:,|\band\b)\s*", match["courses"])
             if part.strip()]
    codes = [normalize_code(part) for part in parts]
    if None in codes or any(not _CODE_ONLY_RE.fullmatch(part) for part in parts):
        return match.group()
    others = [code for code in codes if code != self_code]
    return f"co-requisite: {' and '.join(others)}. " if others else ""
# A trailing "... or equivalent transfer credit ..." is a modifier, not an `or`
# operand; keeping it as an operand makes mixed and/or look ambiguous (EMO-028).
# Only a clause that ends the segment qualifies, so a following restriction
# sentence (CHEM 340) is never dropped.
_TRANSFER_TAIL_RE = re.compile(
    r"\s*(?:[,;]\s*)?(?P<connector>or|and)?\s*equivalent\s+transfer\s+credits?"
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


def canonical_codes_in(text: str) -> tuple[str, ...]:
    """Every course identity a passage names, in canonical form.

    The identity gate compares the code an answer names against the codes the
    cited passage contains. Both sides must be read the same way, or a correct
    answer looks unsupported: a passage spells subjects in ways a plain
    subject-word reader cannot resolve ("Math_OX 112", "Psyc_ox 110",
    "Phys_OX 151", "Econ_OX 101"), runs a code into the conjunction
    ("BIOL 151or"), puts Oxford's suffix after the number ("Math 112_OX"), and
    mixes case inside a subject ("BIOl 142L").

    This resolves identities with the requirement parser's own subject
    resolution, but reads prose rather than a requirement: no labels, no
    exclusions, no trees. It never states that a course is required.

    Two rules differ from the requirement parser, both because the question
    differs. A passage that lists codes carries the subject across the
    separator ("CHEM 202Z + 202ZL", "MATH 411-412"), and a number the
    passage prints as part of a level phrase still names that course
    ("GER 300 level courses" names GER 300). The requirement parser treats
    both as structure, because there it must decide what is required.
    """
    prepared = _SPACED_OX_RE.sub(r"\1_OX", text)
    prepared = _OX_AFTER_NUMBER_RE.sub(r"\1_OX \2", prepared)
    prepared = _SHARED_NUMBER_RE.sub(r"\1 \3/\2 \3", prepared)
    prepared = _RUN_TOGETHER_RE.sub(r"\1 \2 ", prepared)
    codes: list[str] = []
    last_subject: str | None = None
    previous_end = 0
    for match in _CODE_RE.finditer(prepared):
        subject_part, digits = match.group(1), match.group(2)
        if subject_part is None:
            # A bare number continues the previous subject when the passage
            # separated them only by a list separator.
            digits = match.group(3)
            if last_subject is None:
                continue
            between = prepared[previous_end:match.start()]
            if _IDENTITY_CARRY_SEP.fullmatch(between) is None:
                continue
            subject = last_subject
        else:
            subject = _resolve_subject(subject_part)
            if subject is None:
                continue
        code = normalize_code(f"{subject} {digits}")
        if code is None:
            continue
        if code not in codes:
            codes.append(code)
        last_subject = subject.upper()
        previous_end = match.end()
    return tuple(codes)


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


def _parse_tokens(tokens: list[tuple[str, str]]) -> Req:
    # A comma never makes a lab an alternative to its own lecture.
    tokens = _group_labs(tokens, separators=(["comma"], ["comma", "and"]))
    try:
        return _Parser(tokens).parse()
    except _Ambiguous:
        grouped = _group_labs(tokens, separators=(["and"],))
        if grouped != tokens:
            try:
                return _Parser(grouped).parse()
            except _Ambiguous:
                pass
        paired = _group_campus_pairs(tokens)
        if paired == tokens:
            raise
        return _Parser(paired).parse()


def _group_campus_pairs(tokens: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Bind "FREN 310 or FREN_OX 310": one course at either campus.

    "FREN 310 or FREN_OX 310 and FREN 314 or FREN_OX 314" has one reading, since
    the two sides of each "or" are the same course.
    """
    output: list[tuple[str, str]] = []
    index = 0
    while index < len(tokens):
        window = tokens[index:index + 3]
        if (len(window) == 3 and window[0][0] == "code" and window[1][0] == "op"
                and window[1][1] in {"or", "comma"}
                and window[2][0] == "code" and window[0][1] != window[2][1]
                and window[0][1].replace("_OX", "") == window[2][1].replace("_OX", "")):
            output.extend([("paren", "("), window[0], ("op", "or"), window[2], ("paren", ")")])
            index += 3
            continue
        output.append(tokens[index])
        index += 1
    return output


def _group_labs(tokens: list[tuple[str, str]], *,
                separators: tuple[list[str], ...]) -> list[tuple[str, str]]:
    """Bind a lecture to its own lab: "BIOL 141 and BIOL 141L", "BIOL 142, BIOL 142L".

    A lab is taken together with its lecture (user decision, 2026-10-29), so
    the pair is one group before any other and/or applies. A comma-joined pair
    is always grouped ("BIOL 142, BIOL 142L or BIOL_OX142" is not three
    alternatives); an and-joined pair only when the ungrouped tokens are
    ambiguous, so a plain AND list keeps its shape. The rest of the expression
    must still parse without choosing a precedence.
    """
    output: list[tuple[str, str]] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token[0] == "code":
            # Separators between the pair: "and", ",", or ", and".
            end = index + 1
            between = []
            while end < len(tokens) and tokens[end][0] == "op" and tokens[end][1] in {"and", "comma"}:
                between.append(tokens[end][1])
                end += 1
            if (between in separators and end < len(tokens)
                    and tokens[end] == ("code", f"{token[1]}L")):
                output.extend([("paren", "("), token, ("op", "and"), tokens[end], ("paren", ")")])
                index = end + 1
                continue
        output.append(token)
        index += 1
    return output


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
        tree = _parse_tokens(tokens)
    except _Ambiguous:
        return None
    return _attach_transfer_condition(tree, tail.group("connector"))


def _parse_whole(segment: str) -> Req | None:
    tokens = _scan(segment)
    if any(ttype == "leftover" for ttype, _ in tokens):
        return None
    try:
        return _parse_tokens(tokens)
    except _Ambiguous:
        return None


def _parse_on_capital_and(segment: str) -> Req | None:
    """Split "ECON 201 AND ECON 220 or MATH 207" where only the AND is capitalized.

    Atlas writes groups this way: a capital AND between lowercase ors joins the
    groups. Any other mix stays ambiguous.
    """
    parts = re.split(r"\s+AND\s+", segment)
    if len(parts) < 2 or re.search(r"\bOR\b|\band\b", segment):
        return None
    trees = [_parse_whole(part) for part in parts]
    return None if any(tree is None for tree in trees) else AllOf(tuple(trees))


def _as_corequisites(mentions: list[Mention], kinds: set[str]) -> list[Mention]:
    output: list[Mention] = []
    codes: set[str] = set()
    for mention in mentions:
        if mention.kind in kinds:
            if mention.code in codes:
                continue
            codes.add(mention.code)
            mention = replace(mention, kind="corequisite")
        output.append(replace(mention, position=len(output)))
    return output


def _combine(joiner: str, left: Req, right: Req) -> Req:
    group = AllOf if joiner == "and" else AnyOf
    items: list[Req] = []
    for tree in (left, right):
        items.extend(tree.items if isinstance(tree, group) else (tree,))
    return group(tuple(items))


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

    single_transfer = _SINGLE_LABELED_TRANSFER_RE.fullmatch(text)
    if single_transfer:
        text = f"{single_transfer.group('course')} or equivalent transfer credit as prerequisite"
    segments = _requisite_parts(text, course_code)
    if not segments:
        return ParsedRequisites("none", (), {}, ())

    mentions: list[Mention] = []
    seen: set[tuple[str, str]] = set()
    trees: dict[str, Req] = {}
    joined_by: dict[str, str] = {}
    leftover: list[str] = []
    ambiguous = False
    unparsed = False
    self_code = normalize_code(course_code) if course_code else None
    previous_kind: str | None = None

    for kind, raw, joiner in segments:
        before, previous_kind = previous_kind, kind
        segment = _requirement_sentence(_strip_grades(raw).strip().strip(".").strip())
        if not segment or segment.casefold() == "none":
            continue
        whole = segment
        transfer_tail = _TRANSFER_TAIL_RE.search(segment)
        if transfer_tail is not None:
            prefix = segment[:transfer_tail.start()].strip()
            if not prefix:
                if joiner == "or" and before in trees:
                    # "... as co-requisite or equivalent transfer credit" closes
                    # the alternatives already read.
                    trees[before] = _combine("or", trees[before],
                                             Condition("equivalent transfer credit"))
                    continue
                # A split-off transfer clause may qualify an earlier prerequisite,
                # corequisite, or the whole statement. Its scope is not known.
                # Keep it unresolved rather than silently publishing a partial tree.
                leftover.append(segment)
                unparsed = True
                continue
            segment = prefix
            if transfer_tail.group("connector") is None:
                # With no stated connector the transfer clause's relationship
                # to the course expression is unknown; do not invent AND or OR.
                leftover.append(transfer_tail.group().strip())
                unparsed = True
                transfer_tail = None
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
            tree = _parse_tokens(tokens)
        except _Ambiguous:
            tree = _parse_on_capital_and(segment) or _parse_with_transfer_tail(segment)
            if tree is None and transfer_tail is not None:
                # "CHEM 221, CHEM 221Z, CHEM_OX 221, or equivalent transfer credit":
                # the clause is the last item of the list, not a modifier of it.
                tree = _parse_whole(whole)
                transfer_tail = None
            if tree is None:
                ambiguous = True
                continue
        if transfer_tail is not None:
            tree = _attach_transfer_condition(tree, transfer_tail.group("connector"))
        if joiner == "or" and before is not None and before != kind:
            if before not in trees or kind in trees:
                ambiguous = True
                continue
            # "A as co-requisite or B as prerequisite": either satisfies it, and a
            # course taken earlier also meets a concurrent requirement, so the
            # whole alternative is the weaker, concurrent form.
            tree = _combine("or", trees.pop(before), tree)
            mentions[:] = _as_corequisites(mentions, {before, kind})
            kind = previous_kind = "corequisite"
        if kind in trees:
            # "MATH 111 as prerequisite AND MATH 112 or ...", "ENVS 371 as a
            # prerequisite OR permission of instructor": the label closes the
            # first part, so the stated conjunction joins two groups. Mixing
            # conjunctions across parts would choose a precedence; never do that.
            if (joiner not in {"and", "or"} or before != kind
                    or joined_by.setdefault(kind, joiner) != joiner):
                ambiguous = True
                continue
            tree = _combine(joiner, trees[kind], tree)
        trees[kind] = tree

    if unparsed:
        status = "unparsed"
    elif ambiguous:
        status = "ambiguous"
    elif not trees:
        # The text stated no requirement at all: only enrollment restrictions,
        # or nothing the requirement scanner can read. That is not a parsed
        # requirement; it is no requirement.
        status = "none"
    else:
        status = "parsed"
    # Mentions stay for every status; the boolean tree is emitted only when the
    # parse is unambiguous. A partial tree would silently drop later clauses.
    return ParsedRequisites(
        status, tuple(mentions), trees if status == "parsed" else {}, tuple(leftover),
    )
