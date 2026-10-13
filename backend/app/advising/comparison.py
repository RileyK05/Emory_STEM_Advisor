"""Exact course-mention comparison between program reads. Pure; no DB, no model.

A shared code means only that each program's raw requirement text mentions it,
possibly as an alternative, an option inside "choose N", or an exclusion. It is
never evidence that both programs require the course, that credits count
twice, or that the combination is permitted. The result says so in its `kind`
and keeps every raw line it rests on, so nothing has to be re-derived.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from app.advising.research_repository import ProgramRead

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
