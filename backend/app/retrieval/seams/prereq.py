"""Retrieve course chunks and parsed prerequisite/corequisite neighbors.

Neighbor chunks are retrieval candidates, not standalone claims that a course
is unconditionally required: sourced prose retains any alternatives and
conditions. Ambiguous/unparsed requisite mentions do not create graph neighbors,
but the course's own chunks remain available through the ``self`` direction.
No model is involved anywhere in this module.
"""

import re
from uuid import UUID

from psycopg import Connection

from app.atlas.records import normalize_code
from app.db import queries
from app.programs.requirements import SUBJECT_ALIASES
from app.retrieval.config import PrereqSeamConfig
from app.retrieval.types import Candidate, SeamResult

# A subject must be a whole word, so "and"/"or" before a number are not read as
# subjects ("MATH 111 and 112" -> MATH 111, MATH 112). The catalog also spells
# out subjects ("Physics 151", "Chemistry 150", "Biology 141"); those map through
# SUBJECT_ALIASES so the seam does not go dormant on a spelled-out subject
# (EMO-015).
_TAIL = r"\d{2,4}(?:[A-Z][A-Z0-9]{0,7}|[a-z][A-Z]?)?"
_SUBJECT_RE = re.compile(rf"\b([A-Za-z][A-Za-z0-9]{{0,10}}[A-Za-z](?:_OX)?)\s*({_TAIL})\b")
_NUMBER_RE = re.compile(rf"\b({_TAIL})\b")
_CARRY_SEP = re.compile(r"\s*(?:(?:,|/|\+|&|-|–|and|or|plus)\s*)*", re.IGNORECASE)
_CONJUNCTIONS = frozenset({"and", "or"})
# Reverse-intent questions ask what a completed course opens, not what it needs.
# Keep downstream graph hits out of ordinary forward prerequisite questions.
REVERSE_CITED_SET_SIZE = 12


def is_reverse_query(query: str) -> bool:
    return bool(_REVERSE_RE.search(query))


_REVERSE_RE = re.compile(
    r"\b(?:unlock|opens?\s+(?:up)?|what\s+courses?\s+can\s+I\s+take\s+after|"
    r"which\s+courses\s+(?:require|need|have|list|name)|"
    r"what\s+courses\s+(?:require|need|have|list|name)|"
    r"after\s+(?:I\s+)?(?:complet|finish|tak)(?:e|ed|es|ing)|downstream|reverse\s+prereq)\b",
    re.IGNORECASE,
)
_SUBJECT_ALIASES = {word.lower(): code for word, code in SUBJECT_ALIASES.items()}


def _resolve_subject(word: str) -> str | None:
    alias = _SUBJECT_ALIASES.get(word.lower())
    if alias is not None:
        return alias
    if re.fullmatch(r"[A-Z][A-Z0-9]{0,5}[A-Z](?:_OX)?", word):
        return word
    return None


def _find_codes(query: str) -> list[tuple[int, int, str | None, str]]:
    """(start, end, subject, tail) for every code-shaped span. A bare number
    span has subject None. Subjects are whole words and never `and`/`or`."""
    matches = []
    for m in _SUBJECT_RE.finditer(query):
        if m.group(1).lower() in _CONJUNCTIONS:
            continue
        subject = _resolve_subject(m.group(1))
        if subject is None:
            continue
        matches.append((m.start(), m.end(), subject, m.group(2)))
    for m in _NUMBER_RE.finditer(query):
        if not any(start <= m.start() < end for start, end, _, _ in matches):
            matches.append((m.start(), m.end(), None, m.group(1)))
    matches.sort()
    return matches


def course_codes_in_query(query: str) -> list[str]:
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
    *,
    source_ids: list[UUID] | None = None,
    workspace_key: str | None = None,
) -> SeamResult:
    if not config.enabled:
        return SeamResult(
            name="prereq", enabled=False, state="disabled", reason="disabled in config"
        )

    if source_ids == []:
        return SeamResult(
            name="prereq", enabled=True, state="dormant", reason="explicitly empty source scope"
        )

    codes = course_codes_in_query(query)
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
                {"collection_id": collection_id, "code": code, "source_ids": source_ids,
                 "workspace_key": workspace_key},
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
                queries.get("prereq_upstream"), {"course_id": course_id,
                    "collection_id": collection_id, "source_ids": source_ids,
                    "workspace_key": workspace_key}
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
        if "downstream" in config.directions and _REVERSE_RE.search(query):
            for chunk in conn.execute(
                queries.get("prereq_downstream"), {"course_id": course_id,
                    "collection_id": collection_id, "source_ids": source_ids,
                    "workspace_key": workspace_key}
            ).fetchall():
                gathered.setdefault(
                    chunk["chunk_id"],
                    Candidate(
                        chunk_id=chunk["chunk_id"],
                        source_id=chunk["source_id"],
                        chunk_index=chunk["chunk_index"],
                        seam="prereq",
                        raw_score=config.decay,
                        prereq_downstream=True,
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
