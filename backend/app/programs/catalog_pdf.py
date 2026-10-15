"""Split the official catalog PDF's text into one document per program. No network.

The college catalog website shows no program requirements, but its generated
PDF (the catalog of record) does. Each program there starts with a title line
followed by "DEGREE AWARDED ..."; academic policies come before the first one.
"""

import re
from dataclasses import dataclass

_FOOTER_RE = re.compile(r"^\s*(Emory University|\d+\s*/\s*\d+)\s*$")
# The catalog's "DEGREE AWARDED" label carries typos in the published PDF
# ("DEGREE AWRDED", "DEZGREE AWRDED"), so match it fuzzily instead of requiring
# the exact string (EMO-003). A missed typo merges one program into the next.
_PROGRAM_START_RE = re.compile(r"^\s*D[A-Z]*GREE\s+A[A-Z]*RDED\b", re.IGNORECASE)
# A program title line ends in a short degree marker, e.g. "Computer Science (BS)".
_PROGRAM_TITLE_RE = re.compile(
    r"^\s*[A-Z0-9][^()]{0,90}\([A-Za-z/&+ ]{1,14}\)\s*$"
)
_POLICIES_START = re.compile(r"^\s*ABOUT EMORY COLLEGE\s*$", re.IGNORECASE)
# A department front-matter heading ("African American Studies Department",
# "Department of History"). It is title case, never the all-caps body line.
_DEPARTMENT_RE = re.compile(
    r"^(?:Department of [A-Z][\w &,'./()\-]{1,80}|[A-Z][\w &,'./()\-]{0,80}\sDepartment)$"
)


@dataclass(frozen=True)
class CatalogSection:
    title: str
    text: str
    first_page: int  # 1-based PDF page numbers
    last_page: int


def _lines(pages: list[str]) -> list[tuple[int, str]]:
    out = []
    for number, page in enumerate(pages, 1):
        for line in page.splitlines():
            line = line.rstrip()
            if line.strip() and not _FOOTER_RE.match(line):
                out.append((number, line))
    return out


_CAPS_HEADING_RE = re.compile(r"^[A-Z][A-Z0-9&,'()/:+\- ]{3,88}[A-Z)]$")


def mark_headings(text: str) -> str:
    """Turn the PDF's ALL-CAPS heading lines into Markdown headings, so a long
    section (the policies) is indexed as many small sections, not one."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if _CAPS_HEADING_RE.match(stripped) and sum(c.isalpha() for c in stripped) >= 6:
            lines.append(f"## {stripped}")
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def _is_department_heading(line: str) -> bool:
    stripped = line.strip()
    # Require at least one lowercase letter so all-caps body lines such as
    # "AFRICAN AMERICAN STUDIES" are not headings, and reject a sentence that
    # merely ends in "Department" ("Department of Classics is ... participant.").
    return (
        len(stripped) <= 90
        and not stripped.endswith(".")
        and any(char.islower() for char in stripped)
        and _DEPARTMENT_RE.match(stripped) is not None
    )


def _title_index_for(lines: list[tuple[int, str]], degree_index: int) -> int:
    """The program title line that belongs to a degree-awarded line (EMO-003)."""
    for back in (1, 2):
        candidate = degree_index - back
        if candidate >= 0 and _PROGRAM_TITLE_RE.match(lines[candidate][1]):
            return candidate
    return degree_index - 1


def split_catalog(
    pages: list[str],
) -> tuple[CatalogSection | None, list[CatalogSection], list[CatalogSection]]:
    """Returns (policies, programs, departments).

    A program ends at the next program, at a department front-matter heading, or
    at the end of the input. Department front matter (which the catalog appends
    after the last program, and places before each department's programs) is
    returned as its own titled sections instead of being swallowed by the
    preceding program.
    """
    lines = _lines(pages)
    starts = [_title_index_for(lines, i) for i, (_, line) in enumerate(lines)
              if i > 0 and _PROGRAM_START_RE.match(line)]

    # The table of contents repeats every heading, so policies start at the
    # last "ABOUT EMORY COLLEGE" heading before the first program (or the
    # first department heading when there are no programs).
    headings = [i for i, (_, line) in enumerate(lines) if _is_department_heading(line)]
    first = starts[0] if starts else (headings[0] if headings else len(lines))
    heads = [i for i, (_, line) in enumerate(lines[:first]) if _POLICIES_START.match(line)]
    policies_range = None
    if heads:
        policies_end = first
        for i in range(heads[-1], first):
            if lines[i][1].strip().upper().startswith("EMORY COLLEGE OF ARTS AND SCIENCES PROGRAMS"):
                policies_end = i
                break
        policies_range = (heads[-1], policies_end)
    # A policy heading can look like a department ("Department of Veterans
    # Affairs Pending Payment Rights"); department front matter never starts
    # inside the policies.
    department_starts = [i for i in headings if policies_range is None
                         or not policies_range[0] <= i < policies_range[1]]

    def section(begin: int, end: int, title: str) -> CatalogSection:
        chunk = lines[begin:end]
        return CatalogSection(
            title=title,
            text="\n".join(line.strip() for _, line in chunk) + "\n",
            first_page=chunk[0][0],
            last_page=chunk[-1][0],
        )

    def first_after(index: int, candidates: list[int]) -> int | None:
        later = [value for value in candidates if value > index]
        return min(later) if later else None

    programs = []
    for k, start in enumerate(starts):
        boundaries = [end for end in (
            starts[k + 1] if k + 1 < len(starts) else None,
            first_after(start, department_starts),
        ) if end is not None]
        end = min(boundaries) if boundaries else len(lines)
        programs.append(section(start, end, lines[start][1].strip()))

    departments = []
    for index, start in enumerate(department_starts):
        boundaries = [end for end in (
            department_starts[index + 1] if index + 1 < len(department_starts) else None,
            first_after(start, starts),
        ) if end is not None]
        end = min(boundaries) if boundaries else len(lines)
        departments.append(section(start, end, lines[start][1].strip()))

    policies = (section(*policies_range, "Academic policies and requirements")
                if policies_range else None)
    return policies, programs, departments
