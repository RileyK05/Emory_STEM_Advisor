"""Courses whose title marks them as a special kind, with the caveat each needs.

A special topics course changes its topic by term; directed research, honors,
internship and capstone courses are arranged, admitted or timed differently
from a regular class. Whether a course is one of these is read from its Course
Atlas title alone: deterministic, and never a model's guess. The caveats are
written to hold for every course of the kind ("check", "usually"), because no
course record states the arrangement itself.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Designation:
    kind: str
    label: str
    caveat: str
    #: Words that show an answer already gave the caveat in its own words.
    said: re.Pattern


# First match wins: "Honors Research" is honors, "Directed Study: Current Legal
# Topics" is directed study, "Special Topics Seminar" is topics.
_KINDS: tuple[tuple[re.Pattern, Designation], ...] = (
    # "CS 495A: Honors", "Honors Seminar in French"; "Honor Council" is not one.
    (re.compile(r"\bhonors\b", re.IGNORECASE),
     Designation("honors", "honors course",
                 "it is for students admitted to the department's honors program",
                 re.compile(r"\bhonors (?:program|track)|\badmi(?:tted|ssion)\b", re.IGNORECASE))),
    (re.compile(r"\b(?:directed|independent|individual|undergraduate|mentored|supervised)\s+"
                r"(?:\w+\s+)?(?:research|study|studies|readings?|writing|project)\b|"
                # "Research in Health Economics" is a class; "Research" alone is not.
                r"^(?:senior\s+)?research(?:\s+tutorial\b|$)|\bspecial project\b|"
                r"\b(?:thesis|dissertation)\b", re.IGNORECASE),
     Designation("independent", "independent research or study course",
                 "you arrange it with a faculty mentor, usually with department permission",
                 re.compile(r"\b(?:mentor|sponsor|faculty (?:member|advisor)|permission|"
                            r"approv(?:al|ed))\b", re.IGNORECASE))),
    # The Atlas abbreviates ("Internshp in Political Science").
    (re.compile(r"\bintern(?:ship|shp)s?\b|\bco-?op\b", re.IGNORECASE),
     Designation("internship", "internship course",
                 "you usually secure the placement and get department approval before "
                 "enrolling, and how its credit counts varies, so check with the department",
                 re.compile(r"\b(?:placement|approv(?:al|ed)|arrange[ds]?)\b", re.IGNORECASE))),
    # Some practicums are ordinary classes (CS 370), others need a placement or
    # selection (a clinical or tutoring practicum): the caveat says only "check".
    (re.compile(r"\bpracticum\b", re.IGNORECASE),
     Designation("practicum", "practicum course",
                 "check whether it needs a placement, selection or department approval "
                 "before planning around it",
                 re.compile(r"\b(?:placement|selection|approv(?:al|ed))\b", re.IGNORECASE))),
    (re.compile(r"\bcapstone\b|\bsenior\s+(?:seminar|sem|project)\b", re.IGNORECASE),
     Designation("capstone", "capstone course",
                 "it is meant for late in the program, so check its standing and "
                 "prerequisite rules",
                 re.compile(r"\b(?:senior|final year|last year|standing)\b", re.IGNORECASE))),
    (re.compile(r"\b(?:special|spec|selected|advanced|adv|current|intro|inter)\.?\s*topics?\b|"
                r"\btopics?\s*(?:in\b|on\b|seminar\b|:)|^topics?\b|\btopics?\s*$",
                re.IGNORECASE),
     Designation("topics", "special topics course",
                 "its topic changes by term, so check that the section you take counts "
                 "toward your requirement",
                 re.compile(r"\btopics? (?:changes?|var(?:y|ies)|rotates?)|\bby term\b",
                            re.IGNORECASE))),
)


def course_designation(title: str | None) -> Designation | None:
    """The special kind an Atlas title names, if any."""
    text = " ".join((title or "").split())
    return next((kind for pattern, kind in _KINDS if pattern.search(text)), None)


def caveat_sentence(codes: str | Sequence[str], designation: Designation) -> str:
    """'SOC 499R is an independent research or study course: ...' as one sentence.

    Several codes of one kind share the sentence: "MUS 270 and MUS 370 are each
    a special topics course: ...".
    """
    codes = [codes] if isinstance(codes, str) else list(codes)
    article = "an" if designation.label[0] in "aeiou" else "a"
    named = codes[0] if len(codes) == 1 else f"{', '.join(codes[:-1])} and {codes[-1]}"
    verb = "is" if len(codes) == 1 else "are each"
    return f"{named} {verb} {article} {designation.label}: {designation.caveat}."


def designated_courses(codes: Iterable[str], titles: Mapping[str, str]) -> list[tuple]:
    """(designation, codes) for each special kind among ``codes``, in first-seen order."""
    groups: dict[str, tuple[Designation, list[str]]] = {}
    for code in dict.fromkeys(codes):
        if designation := course_designation(titles.get(code)):
            groups.setdefault(designation.kind, (designation, []))[1].append(code)
    return list(groups.values())
