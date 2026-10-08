"""Prerequisite seam: a course's own chunks and what it requires / unlocks.

Uses mentions, not trees, so ambiguous or unparsed courses still contribute.
No model is involved anywhere in this module.
"""

import re
from uuid import UUID

from psycopg import Connection

from app.atlas.records import normalize_code
from app.db import queries
from app.retrieval.config import PrereqSeamConfig
from app.retrieval.types import Candidate, SeamResult

# A subject must be a whole word, so "and"/"or" before a number are not read as
# subjects ("MATH 111 and 112" -> MATH 111, MATH 112; "Physics 141" -> nothing).
_SUBJECT_RE = re.compile(r"\b([A-Za-z]{2,5})\s?(\d{3}[A-Za-z]?)\b")
_NUMBER_RE = re.compile(r"\b(\d{3}[A-Za-z]?)\b")
_CARRY_SEP = re.compile(r"\s*(?:(?:,|/|and|or)\s*)*")
_CONJUNCTIONS = frozenset({"and", "or"})


def _find_codes(query: str) -> list[tuple[int, int, str | None, str]]:
    """(start, end, subject, tail) for every code-shaped span. A bare number
    span has subject None. Subjects are whole words and never `and`/`or`."""
    matches = [
        (m.start(), m.end(), m.group(1), m.group(2))
        for m in _SUBJECT_RE.finditer(query)
        if m.group(1).lower() not in _CONJUNCTIONS
    ]
    for m in _NUMBER_RE.finditer(query):
        if not any(start <= m.start() < end for start, end, _, _ in matches):
            matches.append((m.start(), m.end(), None, m.group(1)))
    matches.sort()
    return matches


def _codes(query: str) -> list[str]:
    codes: list[str] = []
    last_subject: str | None = None
    previous_end = -1
    for start, end, subject, tail in _find_codes(query):
        if subject is None:
            if last_subject is None:
                continue
            between = query[previous_end:start]
            if _CARRY_SEP.fullmatch(between) is None:
                continue
            subject = last_subject
        code = normalize_code(f"{subject} {tail}")
        if code is not None and code not in codes:
            codes.append(code)
            last_subject = subject
            previous_end = end
    return codes


def prereq_seam(
    conn: Connection,
    collection_id: UUID,
    query: str,
    config: PrereqSeamConfig,
) -> SeamResult:
    if not config.enabled:
        return SeamResult(
            name="prereq", enabled=False, state="disabled", reason="disabled in config"
        )

    codes = _codes(query)
    if not codes:
        return SeamResult(
            name="prereq", enabled=True, state="dormant", reason="no course codes in query"
        )

    gathered: dict[UUID, Candidate] = {}
    for code in codes:
        row = conn.execute(
            queries.get("get_course_id_by_code"),
            {"collection_id": collection_id, "code": code},
        ).fetchone()
        if row is None:
            continue
        course_id = row["course_id"]

        if "self" in config.directions:
            for chunk in conn.execute(
                queries.get("prereq_self_chunks"),
                {"collection_id": collection_id, "code": code},
            ).fetchall():
                gathered.setdefault(
                    chunk["chunk_id"],
                    Candidate(
                        chunk_id=chunk["chunk_id"],
                        source_id=chunk["source_id"],
                        chunk_index=chunk["chunk_index"],
                        seam="prereq",
                        raw_score=1.0,
                    ),
                )
        if "upstream" in config.directions:
            for chunk in conn.execute(
                queries.get("prereq_upstream"), {"course_id": course_id}
            ).fetchall():
                gathered.setdefault(
                    chunk["chunk_id"],
                    Candidate(
                        chunk_id=chunk["chunk_id"],
                        source_id=chunk["source_id"],
                        chunk_index=chunk["chunk_index"],
                        seam="prereq",
                        raw_score=config.decay,
                    ),
                )
        if "downstream" in config.directions:
            for chunk in conn.execute(
                queries.get("prereq_downstream"), {"course_id": course_id}
            ).fetchall():
                gathered.setdefault(
                    chunk["chunk_id"],
                    Candidate(
                        chunk_id=chunk["chunk_id"],
                        source_id=chunk["source_id"],
                        chunk_index=chunk["chunk_index"],
                        seam="prereq",
                        raw_score=config.decay,
                    ),
                )

    candidates = sorted(
        gathered.values(),
        key=lambda c: (-c.raw_score, str(c.source_id), c.chunk_index),
    )[: config.limit]
    if not candidates:
        return SeamResult(
            name="prereq",
            enabled=True,
            state="dormant",
            reason="no courses matched in collection",
        )
    return SeamResult(name="prereq", enabled=True, state="active", candidates=candidates)
