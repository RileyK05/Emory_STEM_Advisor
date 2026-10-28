"""Deterministic merge of exact term-scoped Atlas offerings into course records."""

from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Iterable

from app.atlas.offerings import ValidatedOfferings
from app.atlas.records import AtlasCourse, normalize_code
from app.atlas.requisites import requisite_text

ATLAS_URL = "https://atlas.emory.edu/"


def shared_title(titles: Iterable[str]) -> str:
    """The course title shared by every offering of one code.

    Atlas titles a topics offering "Base: Topic", so one offering's title names
    a single topic, not the course. The shared base is the longest common prefix
    that ends at a ": " boundary (or is itself one of the titles).
    """
    distinct = sorted(set(titles))
    if len(distinct) == 1:
        return distinct[0]
    prefix = os.path.commonprefix(distinct)
    if all(title == prefix or title.startswith(prefix + ": ") for title in distinct):
        return prefix
    cut = prefix.rfind(": ")
    if cut <= 0:
        raise ValueError(f"offerings share no course title: {distinct!r}")
    return prefix[:cut]


def prepare_authoritative_courses(
    base: list[AtlasCourse], snapshots: Iterable[ValidatedOfferings], *, term: str
) -> list[AtlasCourse]:
    """Overlay eligible exact-term Atlas offerings on catalog fallback records.

    Atlas course identity remains the displayed identity. No aliases or graph facts
    are manufactured. Incomplete or conflicting section restrictions fail closed.
    """
    snapshots = tuple(snapshots)
    if not term or any(snapshot.term != term for snapshot in snapshots):
        raise ValueError("all offering snapshots must have the requested exact term")

    result: dict[str, AtlasCourse] = {}
    for course in base:
        code = normalize_code(course.code)
        if code is None:
            raise ValueError(f"invalid course code: {course.code!r}")
        if code in result:
            raise ValueError(f"duplicate base course code: {code!r}")
        result[code] = course.model_copy(update={
            "code": code, "authority": "catalog", "lifecycle": "current",
            "term": None, "careers": [], "evidence": [], "registration_notes": [],
        })

    # Scope by exact code and career. A shared identity appearing in different
    # careers is combined only when the requisite text agrees.
    rows: dict[str, list[tuple[ValidatedOfferings, object]]] = defaultdict(list)
    for snapshot in snapshots:
        for item in snapshot.records:
            if item.review_status != "eligible":
                continue
            code = normalize_code(item.raw_course_code)
            if code is None:
                raise ValueError(f"invalid Atlas offering code: {item.raw_course_code!r}")
            rows[code].append((snapshot, item))

    for code, scoped_rows in rows.items():
        variants: dict[str | None, list[tuple[ValidatedOfferings, object]]] = defaultdict(list)
        for snapshot, item in scoped_rows:
            text = requisite_text(item.restrictions, item.description)
            variants[text].append((snapshot, item))

        _first_snapshot, first = min(
            scoped_rows,
            key=lambda pair: (pair[0].career, pair[1].group_identity,
                              pair[1].class_number or "", pair[1].evidence_hash),
        )
        evidence = []
        notes = set()
        for snapshot, item in sorted(scoped_rows, key=lambda pair: (
            pair[0].career, pair[1].group_identity, pair[1].class_number or "",
            pair[1].evidence_hash,
        )):
            scope = (f"{snapshot.term}; {snapshot.career}; "
                     + (f"class {item.class_number}" if item.class_number else "grouped result"))
            notice_map = dict(item.notices)
            evidence.append({
                "source_url": ATLAS_URL,
                "capture_set_hash": snapshot.capture_set_hash,
                "capture_envelope_hash": item.capture_envelope_hash,
                "evidence_hash": item.evidence_hash,
                "career": snapshot.career,
                "term": snapshot.term,
                "class_number": item.class_number,
                "group_identity": item.group_identity,
                "restrictions": item.restrictions,
                "notes": item.notes,
                "notices": dict(sorted(notice_map.items())),
                "context": item.raw_metadata,
            })
            if item.notes:
                notes.add(f"{scope}: {item.notes}")
            if notice_map.get("consent"):
                notes.add(f"{scope}: Consent required: {notice_map['consent']}")
        title = shared_title(item.title for _snapshot, item in scoped_rows)
        # A term may offer one topic only; the catalog's course title, when it
        # prefixes the Atlas title at a ": " boundary, marks where the topic starts.
        catalog = result.get(code)
        if catalog is not None and title.startswith(catalog.title + ": "):
            title = catalog.title
        topics: dict[str, set[str]] = defaultdict(set)
        for _snapshot, item in scoped_rows:
            if item.title != title:
                topics[item.title[len(title) + 2:]].update(
                    [item.class_number] if item.class_number else
                    (str(section["crn"]) for section in item.raw_metadata.get("sections", ())
                     if section.get("crn"))
                )
        if len(variants) > 1:
            descriptions = sorted(value or "(no restriction text captured)" for value in variants)
            requisite = (
                "Atlas section restrictions differ; prerequisite status is unresolved. "
                + "; ".join(descriptions)
            )
        else:
            requisite = next(iter(variants))
        # Catalog cross-list declarations do not transfer to Atlas identities.
        result[code] = AtlasCourse(
            code=code,
            title=title,
            description=first.description,
            credits=first.credits,
            requisites=requisite,
            crosslisted=[],
            authority="atlas",
            lifecycle="current",
            term=term,
            careers=sorted({snapshot.career for snapshot, _item in scoped_rows}),
            evidence=evidence,
            registration_notes=sorted(notes),
            topics=[{"topic": topic, "class_numbers": sorted(numbers)}
                    for topic, numbers in sorted(topics.items())],
        )
    atlas_codes = set(rows)
    # A catalog cross-list edge must not pull an Atlas identity back into a
    # catalog equivalence group through the fallback endpoint.
    for code, course in tuple(result.items()):
        if code in atlas_codes:
            continue
        remaining = [alias for alias in course.crosslisted if alias not in atlas_codes]
        if remaining != course.crosslisted:
            result[code] = course.model_copy(update={"crosslisted": remaining})
    return [result[code] for code in sorted(result)]
