"""What the student asked for, read from their own words: no model decides any of it.

The file kind and the template code fills, whether a plan was asked for and how
many terms, the credit and course caps per term, and the courses the student
says they finished. Research and the artifact pipeline both read requests here,
so they agree on what a request is (2026-11-20).
"""

from __future__ import annotations

import re

from app.atlas.records import normalize_code
from app.atlas.requisites import canonical_codes_in
from app.generation.artifact_templates import choose_template

_SHEET_ASK = re.compile(r"\b(?:spreadsheet|workbook|excel|csv|worksheet)\b", re.IGNORECASE)
_PAGE_ASK = re.compile(r"\b(?:web ?page|webpage|html|website|interactive)\b", re.IGNORECASE)


def asked_file_kind(text: str) -> str:
    """The file kind a request's words ask for: a sheet, a page, else a document."""
    text = text or ""
    return "workbook" if _SHEET_ASK.search(text) else "html" if _PAGE_ASK.search(text) else "document"


def asks_for_sheet_or_page(text: str) -> bool:
    return bool(_PAGE_ASK.search(text or "") or _SHEET_ASK.search(text or ""))


TERM_PLAN_REQUEST = re.compile(
    r"\b(?:plans?|planning|semesters?|four[- ]year|schedules?)\b", re.IGNORECASE)


CHECKLIST_REQUEST = re.compile(r"\bcheck[- ]?lists?\b", re.IGNORECASE)


# "What if I switched to the Math BS?", "how would my courses count toward Physics".
WHATIF_REQUEST = re.compile(
    r"\bwhat[- ]if\b"
    r"|\bswitch(?:ed|ing)?\b[^.?!]{0,40}\b(?:majors?|minors?|programs?|to)\b"
    r"|\bchang(?:e|ed|ing)\b[^.?!]{0,20}\b(?:majors?|minors?)\b"
    r"|\bhow (?:would|do|does|will|could) my (?:courses|classes|credits)\b[^.?!]{0,50}"
    r"\b(?:count|apply|fit)\b",
    re.IGNORECASE)


COMPARISON_REQUEST = re.compile(r"\bcompar(?:e|es|ed|ing|ison|isons)\b", re.IGNORECASE)


_ANECDOTE_CLAUSE = re.compile(
    r"\b(?:my\s+)?(?:friend|friends|roommate|roommates|brother|sister|cousin|classmate|peer)s?\s+"
    r"(?:took|take|taken|had|did|does|usually|recommend(?:s|ed)?)\b[^.!?\n;]{0,80}",
    re.IGNORECASE,
)


_FLOOR_PREFIX = re.compile(
    r"\b(?:at\s+least|minimum(?:\s+of)?|min(?:\s+of)?|more\s+than|no\s+fewer\s+than|"
    r"no\s+less\s+than|at\s+a\s+minimum|greater\s+than|over|above)\s*$",
    re.IGNORECASE,
)


_DEGREE_LABEL_YEARS = re.compile(
    r"\b(?:one|two|three|four|five|six|\d{1,2})[- ]years?\s+"
    r"(?:bs|ba|b\.s\.|b\.a\.|degree|degrees|program|programs|university|universities|"
    r"college|colleges|transfer|transfers|student|students|school|schools)\b",
    re.IGNORECASE,
)


_REMAINING_TERMS = re.compile(
    r"\b(?:(?:remaining|last|next|final)\s+"
    r"(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d{1,2})"
    r"[- ]?(?:semesters?|terms?)|"
    r"(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d{1,2})"
    r"[- ]?(?:semesters?|terms?)\s+(?:left|remaining))\b",
    re.IGNORECASE,
)


_REMAINING_YEARS = re.compile(
    r"\b(?:(?:remaining|last|next|final)\s+"
    r"(one|two|three|four|five|six|\d{1,2})[- ]?years?|"
    r"(one|two|three|four|five|six|\d{1,2})[- ]?years?\s+(?:left|remaining))\b",
    re.IGNORECASE,
)


_CREDIT_CAP_PATTERNS = (
    re.compile(r"\b(?:no more than|at most|maximum(?: of)?|max(?:imum)?|under|"
               r"fewer than|up to)\s+(\d{1,2})\s*(?:credits?|hours?)\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,2})[- ](?:credit|hour)s?\s*(?:cap|limit|maximum|max)\b",
               re.IGNORECASE),
    re.compile(r"\b(\d{1,2})\s*(?:credits?|hours?)\s*(?:per|a|each)\s*(?:term|semester)\b",
               re.IGNORECASE),
    re.compile(r"\b(\d{1,2})\s*(?:credits?|hours?)\s*(?:in any|in a|each)\s*(?:term|semester)\b",
               re.IGNORECASE),
    re.compile(r"\b(?:cap|limit)(?:\s+of)?\s*(\d{1,2})\s*(?:credits?|hours?)\b",
               re.IGNORECASE),
)


_COURSE_CAP_PATTERNS = (
    re.compile(r"\b(?:no more than|at most|maximum(?: of)?|max(?:imum)?|under|"
               r"fewer than|up to)\s+(\d)\s*(?:courses?|classes?)\b", re.IGNORECASE),
    re.compile(r"\b(\d)\s*(?:courses?|classes?)\s*(?:per|a|each)\s*(?:term|semester)\b",
               re.IGNORECASE),
)


_TERM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
               "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}


_YEAR_TERMS = re.compile(
    r"\b(one|two|three|four|five|six|\d{1,2})[- ]years?\b", re.IGNORECASE)


_TERM_COUNT = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d{1,2})"
    r"[- ](?:semesters?|terms?)\b", re.IGNORECASE)


def requested_credit_cap(text: str) -> float:
    cleaned = _ANECDOTE_CLAUSE.sub(" ", text or "")
    for pattern in _CREDIT_CAP_PATTERNS:
        for match in pattern.finditer(cleaned):
            prefix = cleaned[max(0, match.start() - 32):match.start()]
            if _FLOOR_PREFIX.search(prefix):
                continue
            value = int(match.group(1))
            if 3 <= value <= 30:
                return float(value)
    return 16.0


def requested_course_cap(text: str) -> int:
    cleaned = _ANECDOTE_CLAUSE.sub(" ", text or "")
    for pattern in _COURSE_CAP_PATTERNS:
        for match in pattern.finditer(cleaned):
            prefix = cleaned[max(0, match.start() - 32):match.start()]
            if _FLOOR_PREFIX.search(prefix):
                continue
            value = int(match.group(1))
            if 1 <= value <= 8:
                return value
    return 5


def _parse_count(raw: str | None) -> int:
    if not raw:
        return 0
    word = raw.casefold()
    return _TERM_WORDS.get(word, 0) or (int(word) if word.isdigit() else 0)


def requested_terms(text: str) -> int | None:
    cleaned = _DEGREE_LABEL_YEARS.sub(" ", text or "")
    rem_terms = _REMAINING_TERMS.search(cleaned)
    if rem_terms:
        count = _parse_count(rem_terms.group(1) or rem_terms.group(2))
        if 1 <= count <= 12:
            return count
    rem_years = _REMAINING_YEARS.search(cleaned)
    if rem_years:
        count = _parse_count(rem_years.group(1) or rem_years.group(2))
        if 1 <= count <= 6:
            return count * 2
    terms = _TERM_COUNT.search(cleaned)
    if terms:
        count = _parse_count(terms.group(1))
        if 1 <= count <= 12:
            return count
    years = _YEAR_TERMS.search(cleaned)
    if years:
        count = _parse_count(years.group(1))
        if 1 <= count <= 6:
            return count * 2
    return None


def strict_term_limit(text: str) -> bool:
    """A student who says "finish in four semesters" set a hard limit."""
    return bool(re.search(
        r"\b(?:finish|complete|graduate)\b[^.?!]{0,25}\b(?:in|within)\b"
        r"[^.?!]{0,25}\b(?:semesters?|terms?|years?)\b", text or "", re.IGNORECASE))


PREMED_RE = re.compile(
    r"\b(?:pre[- ]?med(?:ical)?|mcat|medical\s+school)\b",
    re.IGNORECASE,
)


NO_CHEM_RE = re.compile(
    r"\b(?:no|without|exclude|excluding|skip|drop|avoid)\s+(?:any\s+)?chem(?:istry)?\b",
    re.IGNORECASE,
)


_PREMED_SPINE = (
    "BIOL 141", "BIOL 141L", "BIOL 142", "BIOL 142L",
    "CHEM 150", "CHEM 150L", "CHEM 202", "CHEM 202L",
    "CHEM 203", "CHEM 203L", "CHEM 204", "CHEM 204L",
    "PHYS 141", "PHYS 142",
)


_COMPLETED_CUE_RE = re.compile(
    r"(?:(?:already|i(?:'ve| have)?|we(?:'ve| have)?|student(?:\s+has)?)\s+)?"
    r"(?:completed|took|taken|finished|passed|did|earned\s+(?:ap|ib|transfer)?\s*credit\s+(?:for|in)|"
    r"have\s+(?:ap\s+|ib\s+|transfer\s+)?credit\s+(?:for|in)|"
    r"have\s+(?:already\s+)?(?:completed|taken|finished|passed|done)|"
    r"(?:already|i(?:'ve| have)|we(?:'ve| have))\s+done(?:\s+with)?|"
    r"done\s+with|ap\s+(?:credit\s+)?(?:for|in)|exempt(?:ed)?\s+from|placed\s+out\s+of|"
    r"completed\s+equivalent\s+of)\b(?P<span>[^.!?\n]+)",
    re.IGNORECASE,
)


_WITH_DONE_RE = re.compile(
    r"\bwith\s+(?P<span>[^.!?\n]{1,100}?)\s+(?:done|completed|finished|taken|out\s+of\s+the\s+way)\b",
    re.IGNORECASE,
)


_HAVE_AP_RE = re.compile(
    r"\b(?:have|with|got)\s+(?:ap|ib)\s+(?P<span>[^.!?\n]{1,60})",
    re.IGNORECASE,
)


_PLAN_STOP_RE = re.compile(
    r"\b(?:plan|build|make|create|design|schedule|what\s+should|which|how|"
    r"need\s+to\s+take|remaining)\b",
    re.IGNORECASE,
)


_NEG_BEFORE_CUE_RE = re.compile(
    r"(?:\b(?:not|never|no|without|neither|nor|haven['’]?t|hasn['’]?t|didn['’]?t|"
    r"hadn['’]?t|don['’]?t|won['’]?t)\s+(?:already\s+|yet\s+)?)$",
    re.IGNORECASE,
)


_NEG_STOP_RE = re.compile(
    r"\b(?:not|never|haven['’]?t|hasn['’]?t|didn['’]?t|need(?:s)?|still|"
    r"plan(?:s|ning)?\s+(?:to|on)|without|will|won['’]?t|['’]ll|want(?:s|ed)?|"
    r"would\s+like|hope|going\s+to|gonna|intend(?:s|ing)?|next|later|then|"
    r"upcoming|future|currently|enrolled|registered|signed\s+up)\b",
    re.IGNORECASE,
)


_TITLE_COURSE_ALIASES: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (re.compile(r"\bintro(?:ductory)?\s+psych(?:ology)?\b", re.IGNORECASE),
     ("PSYC 110", "PSYC 111")),
    (re.compile(r"\bgen(?:eral)?\s+chem(?:istry)?\s*(?:ii|2)\b", re.IGNORECASE),
     ("CHEM 202", "CHEM 142")),
    (re.compile(r"\bgen(?:eral)?\s+chem(?:istry)?(?:\s*(?:i|1))?\b", re.IGNORECASE),
     ("CHEM 150", "CHEM 141")),
    (re.compile(r"\borg(?:anic|o)\s*(?:chem(?:istry)?\s*)?(?:ii|2)\b", re.IGNORECASE),
     ("CHEM 204", "CHEM 222")),
    (re.compile(r"\borg(?:anic|o)\s*(?:chem(?:istry)?\s*)?(?:i|1)\b", re.IGNORECASE),
     ("CHEM 203", "CHEM 221")),
    (re.compile(r"\bcalc(?:ulus)?\s*(?:iii|3)|multivariable(?:\s+calc(?:ulus)?)?\b",
                re.IGNORECASE),
     ("MATH 211",)),
    (re.compile(r"\b(?:calc(?:ulus)?\s*(?:ii|2|bc)|ap\s+calc(?:ulus)?\s+bc)\b", re.IGNORECASE),
     ("MATH 112",)),
    (re.compile(r"\b(?:calc(?:ulus)?\s*(?:i|1|ab)|ap\s+calc(?:ulus)?\s+ab)\b", re.IGNORECASE),
     ("MATH 111",)),
    (re.compile(r"\blinear\s+algebra\b", re.IGNORECASE),
     ("MATH 221",)),
    (re.compile(r"\bintro(?:ductory)?\s+bio(?:logy)?\b|\bgen(?:eral)?\s+bio(?:logy)?\s*(?:i|1)?\b|\bap\s+bio(?:logy)?\b",
                re.IGNORECASE),
     ("BIOL 141",)),
    (re.compile(r"\bintro(?:ductory)?\s+cs\b|\bap\s+cs(?:\s*a)?\b", re.IGNORECASE),
     ("CS 170",)),
)


def premed_courses(course_exists, campus_equivalents) -> list[str]:
    out: list[str] = []
    for code in _PREMED_SPINE:
        resolved = code if course_exists(code) else next(
            (alt for alt in sorted(campus_equivalents.get(code, ())) if course_exists(alt)),
            None,
        )
        if resolved and resolved not in out:
            out.append(resolved)
    return out


def completed_from_text(
    text: str,
    known_courses: set[str],
    campus_equivalents: dict[str, frozenset[str]],
) -> list[str]:
    spans: list[str] = []
    source_text = text or ""
    for regex in (_COMPLETED_CUE_RE, _WITH_DONE_RE, _HAVE_AP_RE):
        for match in regex.finditer(source_text):
            prefix = re.split(r"[.!?\n;]", source_text[max(0, match.start() - 28):match.start()])[-1]
            if _NEG_BEFORE_CUE_RE.search(prefix):
                continue
            span = match.group("span")
            if regex is _COMPLETED_CUE_RE:
                if match.group(0).split()[0].lower() == "did" and re.match(
                        r"^\s*(?:not|n['’]t)\b", span, re.IGNORECASE):
                    continue
                stops = [s.start() for pattern in (_PLAN_STOP_RE, _NEG_STOP_RE)
                         if (s := pattern.search(span)) is not None]
                if stops:
                    span = span[:min(stops)]
            elif regex is _WITH_DONE_RE and re.search(
                    r"\b(?:not|never|no|without|yet)\s*$", span, re.IGNORECASE):
                continue
            spans.append(span)
    found: list[str] = []
    for span in spans:
        for code in canonical_codes_in(span):
            canonical = normalize_code(code) or code
            if canonical in known_courses and canonical not in found:
                found.append(canonical)
                continue
            for alt in sorted(campus_equivalents.get(canonical, ())):
                if alt in known_courses and alt not in found:
                    found.append(alt)
                    break
        for pattern, candidates in _TITLE_COURSE_ALIASES:
            if not pattern.search(span):
                continue
            resolved = next((c for c in candidates if c in known_courses), None)
            if resolved is None:
                for c in candidates:
                    resolved = next(
                        (alt for alt in sorted(campus_equivalents.get(c, ()))
                         if alt in known_courses),
                        None,
                    )
                    if resolved:
                        break
            if resolved and resolved not in found:
                found.append(resolved)
    return found


def code_built_template(kind: str, request_text: str) -> str | None:
    """The template code fills for a grounded request of this kind, if any.

    The choice the pipeline makes when it starts (plan, then what-if, then the
    templates). Research reads it to know the file comes from program records,
    so the turn before it need not research the program (2026-11-20).
    """
    text = request_text or ""
    if (kind in {"document", "workbook"} and not CHECKLIST_REQUEST.search(text)
            and TERM_PLAN_REQUEST.search(text)):
        return "plan"
    if kind == "document" and WHATIF_REQUEST.search(text):
        return None
    template = choose_template(kind, text)
    return builder_for(kind, template.id if template else None)


def builder_for(kind: str, template_id: str | None) -> str | None:
    """Which code-built template fills this request, if any."""
    if kind == "workbook":
        return "tracker" if template_id == "tracker" else None
    if template_id in {"checklist", "comparison"}:
        return template_id
    # A guide, report or page about a program; a narrower question about one
    # ("describe its degree") is still written from the slots.
    return "overview" if template_id in {"report", "page"} else None
