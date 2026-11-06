"""Exact course-mention comparison between program reads. Pure; no DB, no model.

A shared code means only that each program's raw requirement text mentions it,
possibly as an alternative, an option inside "choose N", or an exclusion. It is
never evidence that both programs require the course, that credits count
twice, or that the combination is permitted. The result says so in its `kind`
and keeps every raw line it rests on, so nothing has to be re-derived.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from app.advising.research_repository import ProgramItem, ProgramRead

MENTION_OVERLAP_ONLY = "mention_overlap_only"


@dataclass(frozen=True)
class MentionRef:
    """One raw requirement line that mentions a code."""

    program_id: UUID
    position: int
    text: str
    choose: int | None
    cited: bool  # False: the line is a discovery lead without matching evidence


@dataclass(frozen=True)
class MentionComparison:
    kind: str
    programs: tuple[UUID, ...]
    # code -> every mentioning line, for codes mentioned by two or more programs
    shared: dict[str, tuple[MentionRef, ...]]
    # program -> code -> its mentioning lines, for codes only that program mentions
    unique: dict[UUID, dict[str, tuple[MentionRef, ...]]]
    # Lines with no matching evidence are compared but flagged, never dropped.
    unresolved_positions: dict[UUID, tuple[int, ...]]


def compare_mentions(programs: Sequence[ProgramRead]) -> MentionComparison:
    """Compare two to four already-read programs by the codes their lines mention."""
    ids = [read.pin.program_id for read in programs]
    if not 2 <= len(ids) <= 4 or len(set(ids)) != len(ids):
        raise ValueError("compare two to four distinct programs")
    mentions: dict[str, dict[UUID, list[MentionRef]]] = {}
    for read in programs:
        for item in read.items:
            for code in item.mentioned_course_codes:
                mentions.setdefault(code, {}).setdefault(read.pin.program_id, []).append(
                    MentionRef(read.pin.program_id, item.position, item.line.text,
                               item.choose, item.line.cited))
    shared: dict[str, tuple[MentionRef, ...]] = {}
    unique: dict[UUID, dict[str, tuple[MentionRef, ...]]] = {pid: {} for pid in ids}
    for code in sorted(mentions):
        by_program = mentions[code]
        if len(by_program) > 1:
            shared[code] = tuple(ref for pid in ids for ref in by_program.get(pid, ()))
        else:
            ((pid, refs),) = by_program.items()
            unique[pid][code] = tuple(refs)
    return MentionComparison(
        kind=MENTION_OVERLAP_ONLY, programs=tuple(ids), shared=shared, unique=unique,
        unresolved_positions={read.pin.program_id: read.unresolved_positions
                              for read in programs},
    )


@dataclass(frozen=True)
class LineMatch:
    """Which reported courses one raw requirement line names. Never that it is met."""

    item: ProgramItem
    reported: tuple[str, ...]
    state: str  # all_named_reported | some_named_reported | none_reported | no_course_named


def match_reported_courses(
    items: Sequence[ProgramItem], completed_codes: Sequence[str],
    equivalents: Mapping[str, frozenset[str]],
) -> tuple[tuple[LineMatch, ...], tuple[str, ...]]:
    """Match reported courses to raw lines, and list the courses no line names.

    Lines can name alternatives and exclusions, and one course can appear on two
    lines, so a match only says where a course is mentioned. A campus's course
    matches the code a line names.
    """
    same = {code: {code, *equivalents.get(code, ())} for code in completed_codes}
    matches, named = [], set()
    for item in items:
        codes = set(item.mentioned_course_codes)
        named |= codes
        reported = tuple(code for code, names in same.items() if names & codes)
        if not codes:
            state = "no_course_named"
        elif not reported:
            state = "none_reported"
        elif len(reported) >= (item.choose or len(codes)):
            state = "all_named_reported"
        else:
            state = "some_named_reported"
        matches.append(LineMatch(item, reported, state))
    return tuple(matches), tuple(code for code, names in same.items() if not names & named)
