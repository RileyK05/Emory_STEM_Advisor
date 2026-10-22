"""Source freshness and applicability evaluation (Item 7).

Deterministic evaluation over actually cited or supplied sources, their pinned
provenance metadata, and the declared student cohort. Distinguishes:
1. Freshness thresholds per source class.
2. Superseded vs current status without invalidating historical applicable rules.
3. Cohort compatibility without assuming reviewed_default or is_current applies to everyone.
4. Conflicting editions or scopes across cited sources.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any, Literal
from uuid import UUID

from app.api.schemas import ApplicabilityNotice, SourceApplicabilityItem
from app.db import queries

#: Thresholds in days: (aging_days, stale_days)
FRESHNESS_THRESHOLDS: dict[str, tuple[int, int]] = {
    "institutional_catalog": (365, 730),
    "institutional_department": (180, 365),
    "institutional_policy": (365, 730),
    "institutional_publication": (180, 365),
    "career_reference": (365, 730),
}

_YEAR_RE = re.compile(r"\b(20\d\d)\b")
_RANGE_RE = re.compile(r"\b(20\d\d)[-/](20\d\d|\d\d)\b")
_COHORT_PATTERNS = [
    re.compile(r"\b(?:cohort|catalog year|matriculation year)\s*(?:of\s*)?([0-9]{4}(?:[-/][0-9]{2,4})?)\b", re.IGNORECASE),
    re.compile(r"\b(?:class of|entering in|entering fall|entering spring)\s*([0-9]{4})\b", re.IGNORECASE),
    re.compile(r"\b([0-9]{4}[-/][0-9]{2,4})\s*(?:catalog|cohort)\b", re.IGNORECASE),
]


def extract_declared_cohort(query: str | None) -> str | None:
    """Extract student cohort or catalog year mention from query text if declared."""
    if not query:
        return None
    for pattern in _COHORT_PATTERNS:
        match = pattern.search(query)
        if match:
            return match.group(1).strip()
    return None


def evaluate_source_freshness(
    source_category: str | None,
    published_at: date | None,
    acquired_at: date | None,
    *,
    as_of: date | None = None,
) -> Literal["current", "aging", "stale", "unknown"]:
    """Evaluate freshness against category thresholds. Missing dates stay unknown."""
    if not source_category or source_category not in FRESHNESS_THRESHOLDS:
        return "unknown"
    effective_date = published_at or acquired_at
    if effective_date is None:
        return "unknown"
    today = as_of or datetime.now(UTC).date()
    age_days = (today - effective_date).days
    if age_days < 0:
        return "current"
    aging_threshold, stale_threshold = FRESHNESS_THRESHOLDS[source_category]
    if age_days <= aging_threshold:
        return "current"
    if age_days <= stale_threshold:
        return "aging"
    return "stale"


def _extract_years(text: str) -> set[str]:
    return set(_YEAR_RE.findall(text))


def _cohort_matches(declared: str, target: str) -> bool:
    d = declared.strip().lower()
    t = target.strip().lower()
    if d == t or d in t or t in d:
        return True
    dy = _extract_years(d)
    ty = _extract_years(t)
    return bool(dy and ty and (dy & ty))


def evaluate_cohort_compatibility(
    declared_cohort: str | None,
    applicability: dict[str, Any] | None,
    edition: str | None,
    applicability_status: str | None,
) -> Literal["compatible", "mismatch", "unknown"]:
    """Evaluate cohort compatibility.

    Crucial invariant: is_current and reviewed_default do NOT establish that
    a rule applies to this student.
    """
    if not declared_cohort or not declared_cohort.strip():
        return "unknown"
    dc = declared_cohort.strip()

    if isinstance(applicability, dict):
        cohort_labels: list[str] = []
        for key in ("cohort", "catalog_year", "matriculation_year", "academic_year", "class_year"):
            val = applicability.get(key)
            if isinstance(val, (list, tuple)):
                cohort_labels.extend(str(item) for item in val)
            elif isinstance(val, str):
                cohort_labels.append(val)
        if cohort_labels:
            if any(_cohort_matches(dc, label) for label in cohort_labels):
                return "compatible"
            return "mismatch"

    if edition and edition.strip():
        ed = edition.strip()
        if _cohort_matches(dc, ed):
            return "compatible"
        ed_years = _extract_years(ed)
        dc_years = _extract_years(dc)
        if ed_years and dc_years:
            if ed_years & dc_years:
                return "compatible"
            return "mismatch"

    return "unknown"


def build_source_applicability_item(
    row: dict[str, Any],
    declared_cohort: str | None = None,
    *,
    as_of: date | None = None,
) -> SourceApplicabilityItem:
    """Build a typed applicability item for one cited source."""
    source_id = str(row["source_id"])
    title = row.get("title") or row.get("filename") or "Untitled Source"
    source_category = row.get("source_category") or "unclassified"
    edition = row.get("edition")
    pub_date = row.get("published_at")
    published_at_str = pub_date.isoformat() if pub_date else None
    acq_date = row.get("acquired_at")

    is_current = bool(row.get("is_current", True))
    origin_key = row.get("origin_key")
    is_superseded = (not is_current) and bool(origin_key)

    freshness = evaluate_source_freshness(source_category, pub_date, acq_date, as_of=as_of)
    applicability_status = row.get("applicability_status") or "unknown"
    cohort_compatibility = evaluate_cohort_compatibility(
        declared_cohort, row.get("applicability"), edition, applicability_status
    )

    parts = [title]
    if edition:
        parts.append(f"edition {edition}")
    if is_superseded:
        parts.append("superseded by newer publication")
    elif not is_current:
        parts.append("archived source")
    else:
        parts.append("current edition")

    if cohort_compatibility == "compatible":
        parts.append(f"applies to cohort {declared_cohort}")
    elif cohort_compatibility == "mismatch":
        parts.append(f"scope mismatch for cohort {declared_cohort}")
    elif declared_cohort:
        parts.append("unknown cohort applicability")

    if freshness in {"aging", "stale"}:
        parts.append(f"{freshness} review status")

    summary = "; ".join(parts)

    return SourceApplicabilityItem(
        sourceId=source_id,
        sourceName=title,
        sourceCategory=source_category,
        edition=edition,
        publishedAt=published_at_str,
        isCurrent=is_current,
        isSuperseded=is_superseded,
        freshness=freshness,
        applicabilityStatus=applicability_status,
        cohortCompatibility=cohort_compatibility,
        summary=summary,
    )


def build_applicability_notice(
    source_rows: list[dict[str, Any]],
    declared_cohort: str | None = None,
    *,
    as_of: date | None = None,
) -> ApplicabilityNotice | None:
    """Build a deterministic banner over evidence actually cited."""
    if not source_rows:
        return None

    items = [
        build_source_applicability_item(r, declared_cohort=declared_cohort, as_of=as_of)
        for r in source_rows
    ]

    conflicts: list[str] = []
    editions = sorted({item.edition for item in items if item.edition})
    if len(editions) > 1:
        conflicts.append(f"Multiple conflicting editions cited: {', '.join(editions)}")

    compatibilities = {item.cohortCompatibility for item in items if item.cohortCompatibility != "unknown"}
    if "compatible" in compatibilities and "mismatch" in compatibilities:
        conflicts.append("Cited sources have conflicting cohort applicability (some match declared cohort, others do not)")

    superseded_count = sum(1 for item in items if item.isSuperseded)
    current_count = sum(1 for item in items if item.isCurrent)
    if superseded_count > 0 and current_count > 0:
        conflicts.append("Cited sources mix current and superseded documents")

    has_conflicts = len(conflicts) > 0

    if has_conflicts:
        level: Literal["info", "warning", "conflict"] = "conflict"
        headline = "Caution: Cited sources contain conflicting editions or cohort scopes."
    elif any(item.cohortCompatibility == "mismatch" for item in items):
        level = "warning"
        headline = f"Notice: Cited documentation may not match your declared cohort ({declared_cohort})."
    elif any(item.isSuperseded for item in items):
        level = "warning"
        headline = "Notice: One or more cited sources have been superseded by newer publications."
    elif any(item.freshness == "stale" for item in items):
        level = "warning"
        headline = "Notice: Cited documentation has passed its recommended review period."
    elif any(item.freshness == "aging" for item in items):
        level = "info"
        headline = "Notice: Cited documentation is aging and may soon need review."
    elif any(item.cohortCompatibility == "compatible" for item in items):
        level = "info"
        headline = f"Applicable documentation verified for cohort {declared_cohort}."
    else:
        level = "info"
        headline = "Cited sources reviewed for applicability and freshness."

    return ApplicabilityNotice(
        level=level,
        headline=headline,
        hasConflicts=has_conflicts,
        conflicts=conflicts,
        sources=items,
        declaredCohort=declared_cohort,
    )


def evaluate_sources_applicability(
    conn,
    source_ids: list[UUID],
    declared_cohort: str | None = None,
    query: str | None = None,
    *,
    as_of: date | None = None,
) -> ApplicabilityNotice | None:
    """Fetch metadata and evaluate applicability for cited sources."""
    if not source_ids:
        return None
    effective_cohort = declared_cohort or extract_declared_cohort(query)
    rows = conn.execute(queries.get("get_sources_applicability_metadata"), {
        "source_ids": [UUID(str(s)) for s in source_ids],
    }).fetchall()
    if not rows:
        return None
    return build_applicability_notice(rows, declared_cohort=effective_cohort, as_of=as_of)
