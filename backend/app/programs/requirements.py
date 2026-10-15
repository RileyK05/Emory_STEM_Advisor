"""Program (major/minor) requirements from catalog text. Deterministic; no model.

Input is one program section of the official catalog PDF (see `catalog_pdf`).
Output keeps every requirement line as raw text, plus what can be read off it
without guessing:

- the course codes each line mentions, expanding catalog shorthand: a subject
  carried across numbers ("Math 211, 250, 318"), ranges ("421-422"), shared
  numbers ("Biol/Psych 320"), and spelled-out subjects ("Physics 151");
- an explicit "choose N" count ("One from the following", "select 2 courses").

Requirement logic beyond that (which lines combine how) is not inferred; the
raw lines stay authoritative, like `requisites_raw` for courses.
"""

import re
from dataclasses import dataclass, field

from app.atlas.records import normalize_code

PARSER_VERSION = "1"

# Words the catalog uses in place of subject codes. Only exact (case-sensitive)
# forms listed here are read as subjects when not written in capitals.
SUBJECT_ALIASES = {
    "Math": "MATH", "Mathematics": "MATH", "Physics": "PHYS", "Phys": "PHYS",
    "Biology": "BIOL", "Biol": "BIOL", "Chemistry": "CHEM", "Chem": "CHEM",
    "Econ": "ECON", "Economics": "ECON", "Psych": "PSYC", "Psychology": "PSYC",
    "Arabic": "ARAB", "Chinese": "CHN", "Japanese": "JPN", "Korean": "KRN",
    "French": "FREN", "German": "GER", "Spanish": "SPAN", "Italian": "ITAL",
    "Hebrew": "HEBR", "Hindi": "HNDI", "Persian": "PERS", "Portuguese": "PORT",
    "Latin": "LAT", "Greek": "GRK", "Anthropology": "ANT", "Ant": "ANT",
    "History": "HIST", "Hist": "HIST", "Philosophy": "PHIL", "Phil": "PHIL",
    "Religion": "REL", "Sociology": "SOC", "Linguistics": "LING", "Ling": "LING",
    "English": "ENG", "Music": "MUS", "Theater": "THEA", "Film": "FILM",
    # The catalog's own nonstandard capitals.
    "BIO": "BIOL", "BIOLOGY": "BIOL", "ANTH": "ANT", "Bio": "BIOL",
}
# Capitalized words that precede numbers but are never subjects ("ALL 300-level").
_NOT_SUBJECTS = {"ALL", "AND", "ANY", "OR", "THE", "NOT", "ONLY", "PLUS", "LEVEL", "CU"}
# Suffixes: "371W", "490RW", and the catalog's lowercase "112z" / "202zL".
_NUMBER = r"\d{3}(?:[A-Z]{1,2}|[a-z][A-Z]?)?"
# Lines at least this long were cut by the PDF's page width (see parse_program).
_WRAP_WIDTH = 80
# "ENVS OX 230" is how the catalog sometimes writes ENVS_OX 230.
_SUBJECT_WORD = r"[A-Z]{2,7}(?:_OX|\s+OX(?=\s?\d))?|[A-Z][a-z]{1,10}"
_SUBJECTS = rf"(?P<subjects>(?:{_SUBJECT_WORD})(?:\s*/\s*(?:{_SUBJECT_WORD}))*)"
_CODE_RE = re.compile(rf"\b{_SUBJECTS}\s?(?P<number>{_NUMBER})\b")
_BARE_RE = re.compile(rf"\b(?P<number>{_NUMBER})\b")
# What may sit between a code and a following bare number for the subject to
# carry: commas, slashes, and/or/plus, a range hyphen.
_CARRY_SEP_RE = re.compile(r"^\s*(?:(?:,|/|-|–|&|and|or|plus)\s*)+$", re.IGNORECASE)
_BULLET_RE = re.compile(r"^(?:[-•◦●]\s+|\d{1,2}[.)]\s+|[a-z][.)]\s+)")
_COUNT_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_COUNT = r"(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)"
_CHOOSE_RES = (
    # The count must sit directly before the phrase (optionally with a
    # parenthetical or the word "courses"). A wider window lets a credit value
    # or an unrelated number hijack the count.
    re.compile(rf"\b{_COUNT}\b(?:\s*\(\d+\))?(?:\s+(?:courses?|classes?))?"
               rf"\s+(?:from|of)\s+the\s+following", re.IGNORECASE),
    re.compile(rf"\b(?:select|choose)\s+{_COUNT}\b", re.IGNORECASE),
    re.compile(rf"\b{_COUNT}\s+courses?\s+(?:chosen\s+)?from\b", re.IGNORECASE),
)
# A section heading such as "Mathematics (3 courses required, 11 CU)".
_SECTION_COUNT_RE = re.compile(r"\(\s*(?:select\s+)?\d+[^)]*required", re.IGNORECASE)
_META = (
    # The published PDF misspells these labels ("DEZGREE AWRDED", "HOURS TO
    # COMPLET"), so match them fuzzily; an exact match left hours/degree null
    # and misclassified the program (EMO-019).
    (re.compile(r"^D\w*GREE\s+A\w*RDED(?![A-Z])\s*"), "degree"),
    (re.compile(r"^HOURS\s+TO\s+COMPLET\w*(?![A-Z])\s*"), "hours"),
    (re.compile(r"^COURSES?\s+REQUIRED(?![A-Z])\s*"), "courses_required"),
    (re.compile(r"^DEPARTMENT\s+CONTACTS?(?![A-Z])\s*"), "department"),
)


@dataclass(frozen=True)
class RequirementItem:
    position: int
    section: str | None
    text: str
    courses: tuple[str, ...]
    choose: int | None


@dataclass
class Program:
    title: str
    kind: str  # major | minor | other
    degree: str | None = None
    hours: str | None = None
    courses_required: str | None = None
    department: str | None = None
    items: list[RequirementItem] = field(default_factory=list)

    @property
    def courses(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for item in self.items:
            for code in item.courses:
                seen.setdefault(code, None)
        return tuple(seen)


def _subject(word: str) -> str | None:
    word = re.sub(r"\s+OX$", "_OX", word.strip())
    if word in _NOT_SUBJECTS:
        return None
    if word in SUBJECT_ALIASES:
        return SUBJECT_ALIASES[word]
    if re.fullmatch(r"[A-Z]{2,7}(?:_OX)?", word):
        return word
    return None  # "Any", "Level", ... are not subjects


def _is_level_descriptor(text: str, number_end: int) -> bool:
    """True when a number names a course level ("100-level", "200-300 level")."""
    rest = text[number_end:]
    return bool(
        re.match(r"\s*-?\s*level\b", rest, re.IGNORECASE)
        or re.match(rf"\s*[-–]\s*{_NUMBER}\s*-?\s*level\b", rest, re.IGNORECASE)
    )


def course_mentions(text: str) -> tuple[str, ...]:
    """Every course code a requirement line mentions, in order, de-duplicated."""
    found: list[tuple[int, int, list[str]]] = []
    last_subjects: list[str] | None = None
    previous_end = 0
    spans = sorted(
        [(m.start(), m.end(), m, True) for m in _CODE_RE.finditer(text)]
        + [(m.start(), m.end(), m, False) for m in _BARE_RE.finditer(text)]
    )
    taken_until = -1
    for start, end, match, has_subject in spans:
        if start < taken_until:
            continue
        number = match.group("number")
        if _is_level_descriptor(text, end):
            continue
        subjects = None
        if has_subject:
            parts = [_subject(w) for w in match.group("subjects").split("/")]
            if parts and all(parts):
                subjects = parts
            else:
                # A matched subject word that is not a known subject ("Classics",
                # "Environmental") is not a course code. Do not carry a previous
                # subject onto its number: that fabricates a real code.
                continue
        if subjects is None:
            between = text[previous_end:start]
            if last_subjects is None or not _CARRY_SEP_RE.match(between):
                continue
            subjects = last_subjects
            start = start + match.group(0).index(number)
        codes = [normalize_code(f"{s} {number}") for s in subjects]
        if not all(codes):
            continue
        found.append((start, end, codes))
        last_subjects, previous_end, taken_until = subjects, end, end
    out: dict[str, None] = {}
    for _, _, codes in found:
        for code in codes:
            out.setdefault(code, None)
    return tuple(out)


def choose_count(text: str) -> int | None:
    for pattern in _CHOOSE_RES:
        match = pattern.search(text)
        if match:
            value = match.group(1).lower()
            return int(value) if value.isdigit() else _COUNT_WORDS[value]
    return None


def _kind(title: str, degree: str | None) -> str:
    upper = title.upper()
    if "(MINOR)" in upper:
        return "minor"
    if "MAJOR" in upper or (degree and degree.upper() in {"BA", "BS", "BBA"}):
        return "major"
    return "other"


def _is_heading(line: str) -> bool:
    return (
        len(line) <= 90
        and not _BULLET_RE.match(line)
        and (line.endswith(":") or _SECTION_COUNT_RE.search(line) is not None
             or line in {"Requirements", "Prerequisites", "Overview", "NOTES",
                         "Notes", "Electives", "Core", "Core Courses"})
    )


def parse_program(title: str, text: str) -> Program:
    """Parse one catalog program section (its text starts with the title line)."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and lines[0] == title:
        lines = lines[1:]
    meta: dict[str, str | None] = {}
    body: list[str] = []
    plural_contacts = False
    for line in lines:
        if not body:
            match = next(((m, key) for pattern, key in _META
                          if key not in meta and (m := pattern.match(line))), None)
            if match:
                m, key = match
                meta[key] = line[m.end():].strip() or None
                plural_contacts = key == "department" and m.group(0).startswith("DEPARTMENT CONTACTS")
                continue
            # "DEPARTMENT CONTACTS Human Health" continues with one name per line.
            if (plural_contacts and len(line) <= 60 and not _BULLET_RE.match(line)
                    and not _is_heading(line)):
                meta["department"] = "; ".join(filter(None, (meta.get("department"), line)))
                continue
        body.append(line)

    # Join PDF-wrapped lines. The PDF only wraps a line that reached the page
    # width, so a line continues the previous item only when the previous raw
    # line was long and didn't end a sentence, and this line isn't a new
    # bullet/number or heading. Short lines are items of their own.
    items: list[tuple[str | None, str]] = []
    section: str | None = None
    previous_raw = ""
    for line in body:
        wrapped = (len(previous_raw) >= _WRAP_WIDTH
                   and not re.search(r"[.:;]$", previous_raw))
        previous_raw = line
        if _is_heading(line):
            section = line.rstrip(":").strip()
            items.append((section, line))
            continue
        if (items and wrapped and not _BULLET_RE.match(line)
                and items[-1][1] != items[-1][0]):
            items[-1] = (items[-1][0], f"{items[-1][1]} {line}")
            continue
        items.append((section, _BULLET_RE.sub("", line) if _BULLET_RE.match(line) else line))

    program = Program(title=title, kind=_kind(title, meta.get("degree")),
                      degree=meta.get("degree"), hours=meta.get("hours"),
                      courses_required=meta.get("courses_required"),
                      department=meta.get("department"))
    for position, (item_section, item_text) in enumerate(items):
        program.items.append(RequirementItem(
            position=position,
            section=item_section,
            text=item_text,
            courses=course_mentions(item_text),
            choose=choose_count(item_text),
        ))
    return program


def to_record(program: Program, *, catalog: str, source_url: str, first_page: int,
              last_page: int) -> dict:
    """One JSONL line for `scripts.import_programs`."""
    return {
        "title": program.title, "kind": program.kind, "degree": program.degree,
        "hours": program.hours, "courses_required": program.courses_required,
        "department": program.department, "catalog": catalog, "source_url": source_url,
        "first_page": first_page, "last_page": last_page, "parser_version": PARSER_VERSION,
        "items": [
            {"position": i.position, "section": i.section, "text": i.text,
             "courses": list(i.courses), "choose": i.choose}
            for i in program.items
        ],
    }
