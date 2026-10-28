"""Keyword seam: full-text OR-match over indexed chunks. No model."""

import re
from itertools import pairwise
from uuid import UUID

from psycopg import Connection

from app.db import queries
from app.retrieval.config import KeywordSeamConfig
from app.retrieval.policy import has_policy_terms
from app.retrieval.types import Candidate, SeamResult

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_PROGRAM_INTENT_RE = re.compile(
    r"\b(?:major|minor|bs|ba|bba|degree|program|checklist|concentration|track)\b",
    re.IGNORECASE,
)

_NUMERIC_REF_RE = re.compile(r"\b\d+(?:[./+\-]\d+)+\b")
# Match only explicit Atlas-shaped subject/number codes. Keep `_OX` as a
# separate searchable token; it is part of the course identity, not an alias.
_COURSE_CODE_RE = re.compile(
    r"(?<![a-z0-9_])([a-z][a-z0-9]{0,5}[a-z](?:_ox)?)[ \t]{0,3}"
    r"(\d{2,4}(?:[a-z][a-z0-9]{0,7})?)(?![a-z0-9_])",
    re.IGNORECASE,
)
_TEMPORAL_SUBJECTS = frozenset({"spring", "summer", "fall", "winter", "autumn", "year", "ay"})
# Conversational scaffolding otherwise lets repeated catalog boilerplate
# outrank the actual subject. Numbers and department/course codes are retained.
QUERY_SCAFFOLD = frozenset(
    ["course", "courses", "catalog", "current", "take", "taking", "explore", "interested", "want", "wants", "recommend", "recommends", "recommendation", "recommendations", "suggest", "relevant", "help", "helps", "supported", "descriptions", "described", "teaches", "teach", "find", "here", "which", "what", "should", "would", "could", "can", "need", "know", "study", "studying", "career", "provide", "information", "based", "context", "available", "list", "listed"]
)
# Standard English function words, the closed class already ignored by the
# english text search configuration. This is not a list of words from one
# failed question. "work", "first", and "fit" stay searchable.
ENGLISH_STOPS = frozenset(
    ["a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are", "as", "at", "be", "because", "been", "before", "being", "below", "between", "both", "but", "by", "did", "do", "does", "doing", "down", "during", "each", "few", "for", "from", "further", "had", "has", "have", "having", "he", "her", "here", "hers", "herself", "him", "himself", "his", "how", "i", "if", "in", "into", "is", "it", "its", "itself", "just", "me", "more", "most", "my", "myself", "no", "nor", "not", "now", "of", "off", "on", "once", "only", "or", "other", "our", "ours", "ourselves", "out", "over", "own", "same", "she", "so", "some", "such", "than", "that", "the", "their", "theirs", "them", "themselves", "then", "there", "these", "they", "this", "those", "through", "to", "too", "under", "until", "up", "very", "was", "we", "were", "what", "when", "where", "which", "while", "who", "whom", "why", "with", "you", "your", "yours", "yourself", "yourselves"]
)
def _tokens(query: str, min_token_len: int) -> list[str]:
    grading_basis = bool(re.search(r"\b(?:s\s*/\s*u|satisfactory\s*/?\s*unsatisfactory)\b",
                                  query, re.IGNORECASE))
    query = re.sub(r"\bs\s*/\s*u\b", "satisfactory unsatisfactory", query, flags=re.IGNORECASE)
    seen: list[str] = []
    for token in _TOKEN_RE.findall(query.lower()):
        if ((len(token) >= min_token_len or token.isdigit())
                and token not in seen and token not in QUERY_SCAFFOLD
                and token not in ENGLISH_STOPS):
            seen.append(token)
    for code_terms in _course_code_terms(query):
        for token in code_terms:
            if token not in seen:
                seen.append(token)
    if grading_basis:
        # PostgreSQL's parser indexes this slash compound as one lexeme.
        seen.append("satisfactory/unsatisfactory")
    return seen


def _course_code_terms(query: str) -> list[tuple[str, ...]]:
    """Split explicit joined or spaced course codes into their literal parts."""
    groups: list[tuple[str, ...]] = []
    for match in _COURSE_CODE_RE.finditer(query):
        number = match.group(2).lower()
        # Term/year phrases are not course codes; other four-digit numbers
        # remain eligible because course numbering can change over time.
        if len(number) == 4 and match.group(1).lower() in _TEMPORAL_SUBJECTS:
            continue
        terms = (*_TOKEN_RE.findall(match.group(1).lower()), number)
        if terms not in groups:
            groups.append(terms)
    return groups


def _course_code_header_pattern(query: str) -> str | None:
    """Match only Atlas course-heading identities, not body-text references."""
    codes = []
    for match in _COURSE_CODE_RE.finditer(query):
        if len(match.group(2)) == 4 and match.group(1).lower() in _TEMPORAL_SUBJECTS:
            continue
        code = re.escape(match.group(1)) + r"[ \t]*" + re.escape(match.group(2))
        if code not in codes:
            codes.append(code)
    if not codes:
        return None
    alternatives = "|".join(codes)
    return r"(^|\n)##[ \t]+(" + alternatives + r")(?![a-z0-9])"


def _specific_tokens(tokens: list[str]) -> list[str]:
    """Longest tokens, when shorter tokens are also present.

    Repeated shorter words such as "work" or "first" must not outrank a
    longer subject token. When every token is the same length there is no
    shorter competitor, so no token gets the bonus.
    """
    if len(tokens) < 2:
        return []
    longest = max(len(token) for token in tokens)
    specific = [token for token in tokens if len(token) == longest]
    if len(specific) == len(tokens):
        return []
    return specific


def keyword_seam(
    conn: Connection,
    collection_id: UUID,
    query: str,
    config: KeywordSeamConfig,
    *,
    source_ids: list[UUID] | None = None,
    workspace_key: str | None = None,
) -> SeamResult:
    if not config.enabled:
        return SeamResult(
            name="keyword", enabled=False, state="disabled", reason="disabled in config"
        )

    if source_ids == []:
        return SeamResult(
            name="keyword", enabled=True, state="dormant", reason="explicitly empty source scope"
        )

    tokens = _tokens(query, config.min_token_len)
    if not tokens:
        return SeamResult(
            name="keyword", enabled=True, state="dormant", reason="no searchable terms"
        )

    # Only normalized tokens and the fixed grading-basis compound reach SQL.
    # Arbitrary user operators never enter the parameterized tsquery.
    q = " | ".join(tokens)
    specific = _specific_tokens(tokens)
    specific_q = " | ".join(specific) if specific else None
    course_code_header_pattern = _course_code_header_pattern(query)
    program_query = re.sub(r"\bAI\s+(minor|major)\b", r"Artificial Intelligence \1", query,
                           flags=re.IGNORECASE)
    program_query = re.sub(r"\bCS\s+(minor|major|BS|BA)\b", r"Computer Science \1",
                           program_query, flags=re.IGNORECASE)
    # A course lookup should not run the program-title lateral once per
    # candidate chunk. Program evidence is only relevant when the question names
    # a program, degree, major/minor, checklist, concentration, or track.
    if not _PROGRAM_INTENT_RE.search(query):
        program_query = None
    # A contiguous named subject in a locator/filename beats an incidental body
    # mention. Single generic words ("minor", "policy") confer no title band.
    title_pairs = [f"({a} <-> {b})" for a, b in pairwise(tokens)
                   if not a.isdigit() and not b.isdigit()]
    numeric_refs = _NUMERIC_REF_RE.findall(query)
    numeric_pattern = (
        r"(^|[^0-9])(" + "|".join(re.escape(ref) for ref in numeric_refs) + r")([^0-9]|$)"
        if numeric_refs else None
    )
    rows = conn.execute(
        queries.get("keyword_search"),
        {"q": q, "collection_id": collection_id, "limit": config.limit,
         "source_ids": source_ids, "workspace_key": workspace_key,
         "numeric_pattern": numeric_pattern, "specific_q": specific_q,
         "title_queries": title_pairs,
         "program_query": program_query,
         "policy_query": has_policy_terms(query),
         "role_query": "checklist" if "checklist" in tokens else None,
         "course_code_header_pattern": course_code_header_pattern},
    ).fetchall()
    candidates = [
        Candidate(
            chunk_id=row["chunk_id"],
            source_id=row["source_id"],
            chunk_index=row["chunk_index"],
            seam="keyword",
            raw_score=float(row["score"]),
            program_evidence=row["program_match"],
        )
        for row in rows
    ]
    return SeamResult(name="keyword", enabled=True, state="active", candidates=candidates)
