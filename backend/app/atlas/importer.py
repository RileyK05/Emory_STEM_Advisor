"""Import the course atlas and build the prerequisite graph. No LLM."""

import hashlib
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid5

from psycopg import Connection

from app.admin.course_changes import record_baseline_conflict
from app.atlas.course_writer import replace_course_requisites
from app.atlas.records import AtlasCourse, canonical_code_map, load_atlas_lines
from app.atlas.requisites import (
    PARSER_VERSION,
    parse_requisites,
    requisite_text,
)
from app.db import queries
from app.embeddings.base import Embedder
from app.ingest.pipeline import ingest_file
from app.ingest.provenance import SourceProvenance
from app.retrieval.config import RetrievalConfig
from app.util.digest import canonical_json_bytes

ATLAS_CONTENT_TYPE = "application/x-course-atlas+jsonl"
ATLAS_ORIGIN_KEY = "emory:course-atlas"
# This configured importer is the verified classifier for the course atlas: it
# is the official catalog's course records, scraped from the catalog site.
ATLAS_PROVENANCE = SourceProvenance(
    "institutional_catalog", publisher="Emory College of Arts and Sciences",
    source_url="https://college.catalog.emory.edu/")

def _primary_atlas_provenance(term: str) -> SourceProvenance:
    return SourceProvenance(
        "institutional_catalog", publisher="Emory University",
        source_url="https://atlas.emory.edu/",
        title=f"Emory Course Atlas — {term}",
        institution="Emory University", issuing_unit="Office of the Registrar",
        document_roles=("course_catalog",), applicability={"term": (term,)},
        applicability_status="unknown",
        attribution=(f"Atlas course records are primary where an explicitly cited eligible "
                     f"{term} offering exists; other records retain their catalog fallback."),
    )


def _course_id(collection_id: UUID, code: str) -> UUID:
    return uuid5(collection_id, f"course:{code}")


_ADMIN_CONTROLLED = ("admin_correction", "admin_created")


def _queue_baseline_conflict(conn, collection_id, code, existing, incoming) -> None:
    """Preserve a reviewed correction when the baseline disagrees with it."""
    raw = requisite_text(incoming.requisites, incoming.description)
    previous = conn.execute(
        queries.get("get_published_change_for_course"),
        {"collection_id": collection_id, "code": code},
    ).fetchone()
    if previous is not None:
        base = previous["base_value"] or {}
        if (base.get("title") == incoming.title
                and base.get("requisitesRaw") == raw):
            # The baseline is exactly what the correction was based on.
            return
    record_baseline_conflict(
        conn,
        collection_id=collection_id,
        code=code,
        course_id=existing["course_id"],
        base_authority=existing["authority"],
        base_value={
            "title": existing["title"],
            "requisitesRaw": existing["requisites_raw"],
            "lifecycle": existing["lifecycle"],
            "adminRevision": existing["admin_revision"],
        },
        incoming_value={
            "title": incoming.title,
            "requisitesRaw": raw,
            "authority": incoming.authority or "catalog",
            "lifecycle": incoming.lifecycle,
        },
        note="The incoming baseline differs from the published correction.",
    )


def _write_course(conn, collection_id, source_id, locator_id, course, canonical_code) -> bool:
    """Write one baseline course unless a reviewed correction owns the identity."""
    existing = conn.execute(queries.get("get_course_admin_state"), {
        "collection_id": collection_id, "code": course.code,
    }).fetchone()
    if existing is not None and existing["authority"] in _ADMIN_CONTROLLED:
        _queue_baseline_conflict(conn, collection_id, course.code, existing, course)
        return False
    lifecycle = getattr(course, "lifecycle", "current")
    authority = course.authority or "catalog"
    course_id = _course_id(collection_id, course.code)
    raw = requisite_text(course.requisites, course.description)
    parsed = parse_requisites(raw, course_code=course.code)
    conn.execute(queries.get("insert_course"), {
        "course_id": course_id, "collection_id": collection_id,
        "source_id": source_id, "locator_id": locator_id,
        "code": course.code, "subject": course.code.split(" ", 1)[0], "title": course.title,
        "requisites_raw": raw, "parse_status": parsed.status, "parser_version": PARSER_VERSION,
        "canonical_code": canonical_code, "lifecycle": lifecycle, "authority": authority,
    })
    replace_course_requisites(conn, course_id, parsed)
    return True


def refresh_atlas_requisites(conn: Connection, *, collection_id: UUID, path: Path) -> int:
    """Reparse an already indexed, byte-identical atlas without re-embedding.

    Updates parser-derived records atomically; source/chunk/locator IDs and the
    department topic graph remain intact. A different file is an import, not a
    metadata repair, and is rejected here.
    """
    raw_bytes = path.read_bytes()
    courses = load_atlas_lines(raw_bytes.decode("utf-8").splitlines())
    with conn.transaction():
        source = conn.execute(queries.get("lock_atlas_source_for_reparse"), {
            "collection_id": collection_id,
            "file_hash": hashlib.sha256(raw_bytes).hexdigest(),
            "content_type": ATLAS_CONTENT_TYPE,
        }).fetchone()
        if source is None:
            raise ValueError("reparse requires the exact current indexed atlas file")
        rows = conn.execute(queries.get("list_courses_for_source"), {
            "source_id": source["source_id"],
        }).fetchall()
        stored = {row["code"]: row for row in rows}
        baseline = {
            row["code"] for row in rows
            if row["authority"] not in _ADMIN_CONTROLLED
        }
        if baseline != {course.code for course in courses}:
            raise ValueError("stored atlas course set differs; re-import the atlas")
        for course in courses:
            row = stored[course.code]
            if row["authority"] in _ADMIN_CONTROLLED:
                # A reviewed correction owns this identity. Never re-apply the
                # baseline parser over it; queue a conflict when the incoming
                # evidence disagrees with what the correction was based on.
                _queue_baseline_conflict(conn, collection_id, course.code, row, course)
                continue
            allowed_raw = (course.requisites, requisite_text(course.requisites, course.description))
            if (row["requisites_raw"] not in allowed_raw or row["title"] != course.title
                    or not str(row["parser_version"]).isdigit()):
                raise ValueError("stored atlas has manual changes; review before reparsing")
        changed = []
        canonical = canonical_code_map(courses)
        for course in courses:
            row = stored[course.code]
            if (row["parser_version"] != PARSER_VERSION
                    or row["requisites_raw"] != requisite_text(course.requisites, course.description)):
                _write_course(conn, collection_id, source["source_id"], row["locator_id"],
                              course, canonical[course.code])
                changed.append(course.code)
            elif row["canonical_code"] != canonical[course.code]:
                # Cross-list identity is not a parser fact; update it without
                # advancing the parser revision or forcing a topic rebuild.
                conn.execute(queries.get("set_course_canonical_code"), {
                    "course_id": row["course_id"],
                    "canonical_code": canonical[course.code],
                })
        for code in changed:
            conn.execute(queries.get("resolve_mention_targets"), {
                "course_id": _course_id(collection_id, code), "collection_id": collection_id,
            })
        if changed:
            # Durable jobs must not reuse a snapshot of the old requisite facts.
            conn.execute(queries.get("advance_atlas_parser_revision"), {
                "source_id": source["source_id"],
            })
    return len(changed)


def import_atlas(
    conn: Connection,
    *,
    collection_id: UUID,
    path: Path,
    embedder: Embedder,
    embed_model: str,
    count_tokens: Callable[[str], int],
    config: RetrievalConfig,
    allow_course_removal: bool = False,
    _trusted_provenance: SourceProvenance | None = None,
    _before_course_write: Callable[[Connection, UUID, list[AtlasCourse]], None] | None = None,
) -> UUID:
    raw_bytes = path.read_bytes()
    courses = load_atlas_lines(raw_bytes.decode("utf-8").splitlines())
    if _trusted_provenance is None and any(course.authority == "atlas" for course in courses):
        raise ValueError("Atlas-primary course labels require the verified authoritative importer")
    if not courses:
        # An empty atlas would retire every current course (its
        # `NOT code = ANY('{}')` predicate matches all rows).
        raise ValueError("atlas import requires at least one course")
    canonical = canonical_code_map(courses)
    codes = [course.code for course in courses]
    code_set = set(codes)
    file_hash = hashlib.sha256(raw_bytes).hexdigest()

    def check_primary_authority(tx_conn: Connection) -> None:
        if _trusted_provenance is not None:
            return
        existing_primary = tx_conn.execute(queries.get("get_current_course_atlas_source"), {
            "collection_id": collection_id,
        }).fetchone()
        if existing_primary is not None and existing_primary.get("source_url") == "https://atlas.emory.edu/":
            raise ValueError(
                "the current course atlas is Atlas-primary; use the verified authoritative importer"
            )

    check_primary_authority(conn)

    def check_removals(tx_conn: Connection) -> None:
        if allow_course_removal:
            return
        previous = tx_conn.execute(queries.get("list_collection_course_codes"), {
            "collection_id": collection_id,
        }).fetchall()
        removed = sorted(row["code"] for row in previous if row["code"] not in code_set)
        if removed:
            sample = ", ".join(removed[:8])
            raise ValueError(
                f"atlas replacement would retire {len(removed)} existing current courses "
                f"({sample}); review snapshot completeness and explicitly allow course removal"
            )

    # Reject partial/nonmatching files before staging or calling the embedder.
    check_removals(conn)

    def on_chunk_tx(tx_conn: Connection, source_id: UUID, chunk_ids: list[UUID]) -> None:
        prepared = tx_conn.execute(queries.get("get_source_by_hash"), {
            "collection_id": collection_id, "file_hash": file_hash, "workspace_key": None,
        }).fetchone()
        if prepared is None or prepared["source_id"] != source_id:
            raise ValueError("atlas file changed during import; refusing mismatched course metadata")
        # Recheck after acquiring the origin publication lock: a different atlas
        # may have published while this snapshot was being prepared.
        check_primary_authority(tx_conn)
        check_removals(tx_conn)
        if _before_course_write is not None:
            _before_course_write(tx_conn, source_id, courses)
        locators = {
            row["label"]: row["locator_id"]
            for row in tx_conn.execute(
                queries.get("list_locators_for_source"), {"source_id": source_id}
            ).fetchall()
        }
        for course in courses:
            _write_course(tx_conn, collection_id, source_id, locators[course.code],
                          course, canonical[course.code])
        # A reviewed correction whose underlying baseline row disappeared stays
        # active and becomes a conflict for review; it is never auto-retired.
        for row in tx_conn.execute(queries.get("list_admin_controlled_courses"), {
            "collection_id": collection_id,
        }).fetchall():
            if row["code"] not in code_set and row["authority"] == "admin_correction":
                record_baseline_conflict(
                    tx_conn,
                    collection_id=collection_id,
                    code=row["code"],
                    course_id=row["course_id"],
                    base_authority=row["authority"],
                    base_value={
                        "title": row["title"],
                        "requisitesRaw": row["requisites_raw"],
                        "lifecycle": row["lifecycle"],
                        "adminRevision": row["admin_revision"],
                    },
                    incoming_value={"removedFromBaseline": True},
                    note=(
                        "The baseline no longer offers this course; the reviewed "
                        "correction stays active."
                    ),
                )
        tx_conn.execute(
            queries.get("retire_courses_not_in_atlas"),
            {"collection_id": collection_id, "codes": codes},
        )
        for course in courses:
            tx_conn.execute(
                queries.get("resolve_mention_targets"),
                {
                    "course_id": _course_id(collection_id, course.code),
                    "collection_id": collection_id,
                },
            )
        # Program requirement mentions lost their course links when the old
        # atlas source cascaded away; re-link them to the new course rows.
        tx_conn.execute(queries.get("resolve_program_mentions"),
                        {"collection_id": collection_id})
        # Publication and topic retirement must commit together. The new source
        # is not current yet, so compare against its prepared hash directly.
        tx_conn.execute(queries.get("retire_topic_build_for_atlas"), {
            "collection_id": collection_id, "file_hash": file_hash,
        })

    source_id = ingest_file(
        conn,
        collection_id=collection_id,
        path=path,
        content_type=ATLAS_CONTENT_TYPE,
        count_tokens=count_tokens,
        embedder=embedder,
        embed_model=embed_model,
        config=config,
        origin_key=ATLAS_ORIGIN_KEY,
        on_chunk_transaction=on_chunk_tx,
        provenance=_trusted_provenance or ATLAS_PROVENANCE,
    )

    # ingest_file skips unchanged bytes; parser upgrades still need to refresh
    # metadata. This path preserves the existing vectors and citation IDs.
    refresh_atlas_requisites(conn, collection_id=collection_id, path=path)
    # A changed catalog retires the collection's topic build; it is not served
    # as current until rebuilt from the new data. A byte-identical re-import
    # leaves a matching build current.
    conn.execute(queries.get("retire_stale_topic_build"), {"collection_id": collection_id})
    conn.commit()

    counts = Counter(
        row["parse_status"]
        for row in conn.execute(
            queries.get("list_courses_for_source"), {"source_id": source_id}
        ).fetchall()
    )
    print(
        "atlas import: "
        + ", ".join(f"{status}={counts.get(status, 0)}" for status in
                    ("none", "parsed", "ambiguous", "unparsed"))
    )
    return source_id


def import_authoritative_atlas(
    conn: Connection, *, collection_id: UUID, path: Path, embedder: Embedder,
    embed_model: str, count_tokens: Callable[[str], int], config: RetrievalConfig,
    snapshots: list,
) -> UUID:
    """Publish a prepared catalog fallback merged with validated Atlas evidence."""
    from app.atlas.offerings import ValidatedOfferings

    if not snapshots or any(not isinstance(value, ValidatedOfferings) for value in snapshots):
        raise ValueError("authoritative publication requires validated offering snapshots")
    terms = {snapshot.term for snapshot in snapshots}
    if len(terms) != 1:
        raise ValueError("authoritative publication requires one exact offering term")
    term = next(iter(terms))
    from app.atlas.authority import prepare_authoritative_courses

    courses = load_atlas_lines(path.read_text(encoding="utf-8").splitlines())
    expected_atlas = {
        course.code: course
        for course in prepare_authoritative_courses([], snapshots, term=term)
    }
    incoming_by_code = {course.code: course for course in courses if course.authority == "atlas"}
    if set(incoming_by_code) != set(expected_atlas):
        raise ValueError("Atlas-primary course set differs from the validated offering snapshots")
    for code, expected in expected_atlas.items():
        if incoming_by_code[code].model_dump(mode="json") != expected.model_dump(mode="json"):
            raise ValueError(f"Atlas course record for {code} differs from validated offering evidence")

    def check_pinned_snapshots(tx_conn: Connection) -> None:
        for snapshot in snapshots:
            pinned = tx_conn.execute(queries.get("get_indexed_atlas_offering_snapshot"), {
                "collection_id": collection_id, "career": snapshot.career,
                "term": snapshot.term, "capture_set_hash": snapshot.capture_set_hash,
            }).fetchone()
            if pinned is None:
                raise ValueError(
                    f"{snapshot.career} / {snapshot.term} captures are not an indexed snapshot "
                    "in this collection"
                )
            from app.atlas.offerings import read_offering_snapshot

            published = read_offering_snapshot(
                tx_conn, source_id=pinned["source_id"],
                index_revision=pinned["index_revision"], collection_id=collection_id,
                include_held=True, capture_set_hash=snapshot.capture_set_hash,
            )
            if published is None or not published["is_current"]:
                raise ValueError("Atlas evidence must match a current indexed offering snapshot")
            header = {
                "career": snapshot.career, "term": snapshot.term,
                "capture_set_hash": snapshot.capture_set_hash,
                "index_evidence_hash": snapshot.index_evidence_hash,
                "announced_count": snapshot.announced_count,
                "result_row_count": len(snapshot.records),
                "eligible_row_count": snapshot.eligible_count,
                "held_row_count": snapshot.held_count,
                "search_criteria": snapshot.search_criteria,
                "capture_hashes": list(snapshot.capture_hashes),
            }
            if any(published[key] != value for key, value in header.items()):
                raise ValueError("validated Atlas snapshot header differs from its indexed record")
            if len(published["rows"]) != len(snapshot.records):
                raise ValueError("validated Atlas snapshot rows differ from its indexed record")
            for ordinal, (stored, item) in enumerate(zip(
                published["rows"], snapshot.records, strict=True,
            )):
                expected = asdict(item)
                expected["notices"] = [list(pair) for pair in item.notices]
                expected_observed = datetime.fromisoformat(item.observed_at).astimezone(UTC)
                stored_value = dict(stored)
                stored_observed = stored_value.pop("observed_at").astimezone(UTC)
                expected.pop("observed_at")
                stored_value.pop("row_ordinal", None)
                if (stored["row_ordinal"] != ordinal
                        or canonical_json_bytes(stored_value) != canonical_json_bytes(expected)
                        or stored_observed != expected_observed):
                    raise ValueError(
                        "validated Atlas offering row differs from its immutable indexed evidence"
                    )

    check_pinned_snapshots(conn)
    for course in courses:
        if course.authority not in ("atlas", "catalog"):
            raise ValueError(f"authoritative course {course.code} must have an explicit authority")

    capture_hashes = {
        code: {item["capture_envelope_hash"] for item in course.evidence}
        for code, course in expected_atlas.items()
    }

    def release_proven_holds(tx_conn: Connection, _source_id: UUID,
                             incoming: list[AtlasCourse]) -> None:
        if incoming != courses:
            raise ValueError("Atlas-primary input changed after evidence validation")
        check_pinned_snapshots(tx_conn)
        atlas_codes = {course.code for course in incoming if course.authority == "atlas"}
        if not atlas_codes:
            return
        old = tx_conn.execute(queries.get("get_current_atlas_requirement_holds"), {
            "collection_id": collection_id,
        }).fetchall()
        from app.advising.requirement_holds import release_requirement_hold

        evidence_hashes = {code: capture_hashes.get(code, set()) for code in atlas_codes}
        for hold in old:
            if hold["course_code"] not in atlas_codes or hold["evidence_capture_sha256"] not in evidence_hashes[hold["course_code"]]:
                continue
            release_requirement_hold(
                tx_conn, hold_id=hold["hold_id"], source_id=hold["source_id"],
                course_code=hold["course_code"], master_file_hash=hold["master_file_hash"],
                curator="verified_atlas_import",
                release_note=(f"Explicit {term} Atlas authority supersedes the cited "
                              "conflicting requirement."),
            )

    return import_atlas(
        conn, collection_id=collection_id, path=path, embedder=embedder,
        embed_model=embed_model, count_tokens=count_tokens, config=config,
        allow_course_removal=False, _trusted_provenance=_primary_atlas_provenance(term),
        _before_course_write=release_proven_holds,
    )


def unresolved_courses(courses: list[AtlasCourse]) -> list[AtlasCourse]:
    """Courses whose requisites did not fully parse, for human review."""
    result = []
    for course in courses:
        if parse_requisites(
            requisite_text(course.requisites, course.description),
            course_code=course.code,
        ).status in {"ambiguous", "unparsed"}:
            result.append(course)
    return result
