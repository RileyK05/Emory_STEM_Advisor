"""Tests for source applicability and freshness banner (Item 7).

Acceptance criteria:
- Historical applicable rules are not marked invalid solely because they are superseded.
- Missing dates stay unknown.
- Conflicting or changed metadata across cited sources is detected.
- Freshness thresholds match source category rules.
- is_current and reviewed_default do not establish that a rule applies to this student.
- Database query retrieves provenance and applicability cleanly.
"""

from datetime import date
from uuid import uuid4

from psycopg.types.json import Jsonb

from app.advising.applicability import (
    build_applicability_notice,
    evaluate_cohort_compatibility,
    evaluate_source_freshness,
    evaluate_sources_applicability,
    extract_declared_cohort,
)
from app.db import queries


def test_extract_declared_cohort_from_queries():
    assert extract_declared_cohort("What are CS requirements for cohort 2024-2025?") == "2024-2025"
    assert extract_declared_cohort("I am in the class of 2028, what math do I take?") == "2028"
    assert extract_declared_cohort("As a student entering fall 2024 what are the GERs?") == "2024"
    assert extract_declared_cohort("According to the 2023-2024 catalog, how many credits?") == "2023-2024"
    assert extract_declared_cohort("Tell me about CS 170") is None


def test_freshness_thresholds_per_source_category():
    as_of = date(2026, 10, 1)

    # institutional_catalog: 365d aging / 730d stale
    assert evaluate_source_freshness("institutional_catalog", date(2026, 5, 1), None, as_of=as_of) == "current"
    assert evaluate_source_freshness("institutional_catalog", date(2025, 8, 1), None, as_of=as_of) == "aging"
    assert evaluate_source_freshness("institutional_catalog", date(2024, 8, 1), None, as_of=as_of) == "stale"

    # institutional_department: 180d aging / 365d stale
    assert evaluate_source_freshness("institutional_department", date(2026, 7, 1), None, as_of=as_of) == "current"
    assert evaluate_source_freshness("institutional_department", date(2026, 1, 1), None, as_of=as_of) == "aging"
    assert evaluate_source_freshness("institutional_department", date(2025, 5, 1), None, as_of=as_of) == "stale"

    # Missing dates stay unknown
    assert evaluate_source_freshness("institutional_catalog", None, None, as_of=as_of) == "unknown"

    # Unclassified or user_supplied stay unknown even if dated
    assert evaluate_source_freshness("unclassified", date(2026, 9, 1), None, as_of=as_of) == "unknown"
    assert evaluate_source_freshness("user_supplied", date(2026, 9, 1), None, as_of=as_of) == "unknown"


def test_cohort_compatibility_and_invariants():
    # Invariant: is_current and reviewed_default do NOT establish that a rule applies to this student.
    assert evaluate_cohort_compatibility(
        declared_cohort="2026-2027",
        applicability={"cohort": ["2024-2025"]},
        edition="2024-2025",
        applicability_status="reviewed_default",
    ) == "mismatch"

    # Matching cohort dimension
    assert evaluate_cohort_compatibility(
        declared_cohort="2024-2025",
        applicability={"cohort": ["2024-2025", "2025-2026"]},
        edition=None,
        applicability_status="reviewed_default",
    ) == "compatible"

    # Fallback to edition year overlap
    assert evaluate_cohort_compatibility(
        declared_cohort="2023",
        applicability={},
        edition="2023-2024",
        applicability_status="historical",
    ) == "compatible"

    # Undeclared cohort stays unknown
    assert evaluate_cohort_compatibility(
        declared_cohort=None,
        applicability={"cohort": ["2024-2025"]},
        edition="2024-2025",
        applicability_status="reviewed_default",
    ) == "unknown"


def test_superseded_source_retains_historical_applicability():
    # Acceptance: Historical applicable rules are not marked invalid solely because they are superseded.
    source_row = {
        "source_id": uuid4(),
        "title": "Emory College Catalog 2022-2023",
        "source_category": "institutional_catalog",
        "edition": "2022-2023",
        "published_at": date(2022, 8, 15),
        "acquired_at": date(2022, 8, 15),
        "is_current": False,
        "origin_key": "emory_college_catalog",
        "applicability": {"cohort": ["2022-2023"]},
        "applicability_status": "historical",
    }
    notice = build_applicability_notice([source_row], declared_cohort="2022-2023", as_of=date(2026, 10, 1))
    assert notice is not None
    item = notice.sources[0]
    assert item.isSuperseded is True
    assert item.cohortCompatibility == "compatible"
    # Even though superseded, it's compatible for this cohort
    assert "applies to cohort 2022-2023" in item.summary
    assert "superseded" in item.summary


def test_conflicting_editions_and_cohort_scopes_detected():
    source_a = {
        "source_id": uuid4(),
        "title": "Catalog 2023-2024",
        "source_category": "institutional_catalog",
        "edition": "2023-2024",
        "published_at": date(2023, 8, 15),
        "is_current": False,
        "origin_key": "emory_college_catalog",
        "applicability": {"cohort": ["2023-2024"]},
        "applicability_status": "historical",
    }
    source_b = {
        "source_id": uuid4(),
        "title": "Catalog 2025-2026",
        "source_category": "institutional_catalog",
        "edition": "2025-2026",
        "published_at": date(2025, 8, 15),
        "is_current": True,
        "origin_key": "emory_college_catalog",
        "applicability": {"cohort": ["2025-2026"]},
        "applicability_status": "reviewed_default",
    }
    notice = build_applicability_notice([source_a, source_b], declared_cohort="2025-2026", as_of=date(2026, 10, 1))
    assert notice is not None
    assert notice.hasConflicts is True
    assert notice.level == "conflict"
    assert any("Multiple conflicting editions" in c for c in notice.conflicts)
    assert any("conflicting cohort applicability" in c for c in notice.conflicts)


def test_evaluate_sources_applicability_db_integration(db):
    collection_id = uuid4()
    db.execute("INSERT INTO collections (collection_id, name) VALUES (%s, %s)",
               (collection_id, "test_collection"))

    source_id = uuid4()
    db.execute(
        """INSERT INTO sources (source_id, collection_id, filename, content_type,
                                file_hash, raw_path, status, is_current, origin_key)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (source_id, collection_id, "catalog_2024.pdf", "application/pdf",
         "a" * 64, "/tmp/catalog", "indexed", True, "catalog_origin"),
    )

    db.execute(
        queries.get("upsert_source_provenance"),
        {
            "source_id": source_id,
            "source_category": "institutional_catalog",
            "classification_origin": "verified_import",
            "publisher": "Emory University",
            "source_url": "https://college.emory.edu",
            "edition": "2024-2025",
            "geography": "Atlanta, GA",
            "occupation_id": None,
            "published_at": date(2024, 8, 1),
            "acquired_at": date(2024, 8, 1),
            "attribution": "Emory College",
            "content_hash": "a" * 64,
            "title": "Emory College Catalog 2024-2025",
            "institution": "Emory University",
            "issuing_unit": "Emory College",
            "document_roles": Jsonb(["catalog_of_record"]),
            "applicability": Jsonb({"cohort": ["2024-2025"]}),
            "applicability_status": "reviewed_default",
            "lifecycle_status": "active",
        },
    )

    notice = evaluate_sources_applicability(db, [source_id], declared_cohort="2024-2025", as_of=date(2024, 10, 1))
    assert notice is not None
    assert notice.hasConflicts is False
    assert len(notice.sources) == 1
    item = notice.sources[0]
    assert item.sourceId == str(source_id)
    assert item.sourceName == "Emory College Catalog 2024-2025"
    assert item.cohortCompatibility == "compatible"
    assert item.freshness == "current"
    assert item.isCurrent is True
    assert item.isSuperseded is False
