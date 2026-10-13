"""Import the course atlas and build the prerequisite graph. No LLM."""

import hashlib
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from uuid import UUID, uuid5

from psycopg import Connection

from app.atlas.records import AtlasCourse, canonical_code_map, load_atlas, load_atlas_lines
from app.atlas.requisites import (
    PARSER_VERSION,
    AllOf,
    AnyOf,
    Condition,
    CourseRef,
    Req,
    parse_requisites,
    requisite_text,
)
from app.db import queries
from app.embeddings.base import Embedder
from app.ingest.pipeline import ingest_file
from app.ingest.provenance import SourceProvenance
from app.retrieval.config import RetrievalConfig

ATLAS_CONTENT_TYPE = "application/x-course-atlas+jsonl"
ATLAS_ORIGIN_KEY = "emory:course-atlas"
# This configured importer is the verified classifier for the course atlas: it
# is the official catalog's course records, scraped from the catalog site.
ATLAS_PROVENANCE = SourceProvenance(
    "institutional_catalog", publisher="Emory College of Arts and Sciences",
    source_url="https://college.catalog.emory.edu/")


def _course_id(collection_id: UUID, code: str) -> UUID:
    return uuid5(collection_id, f"course:{code}")


def _node_id(course_id: UUID, kind: str, path: str) -> UUID:
    return uuid5(course_id, f"{kind}:{path}")


def _insert_nodes(
    conn: Connection, course_id: UUID, kind: str, tree: Req
) -> None:
    def walk(node: Req, parent: UUID | None, path: str, position: int) -> None:
        node_id = _node_id(course_id, kind, path)
        if isinstance(node, CourseRef):
            node_type, target_code, condition_text = "course", node.code, None
        elif isinstance(node, Condition):
            node_type, target_code, condition_text = "condition", None, node.text
        elif isinstance(node, AllOf):
            node_type, target_code, condition_text = "all", None, None
        else:
            node_type, target_code, condition_text = "any", None, None
        conn.execute(
            queries.get("insert_prereq_node"),
            {
                "node_id": node_id,
                "course_id": course_id,
                "kind": kind,
                "parent_node_id": parent,
                "position": position,
                "node_type": node_type,
                "target_code": target_code,
                "condition_text": condition_text,
            },
        )
        if isinstance(node, (AllOf, AnyOf)):
            for index, child in enumerate(node.items):
                walk(child, node_id, f"{path}.{index}", index)

    walk(tree, None, "0", 0)


def _write_course(conn, collection_id, source_id, locator_id, course, canonical_code):
    course_id = _course_id(collection_id, course.code)
    conn.execute(queries.get("delete_prereq_mentions_for_course"), {"course_id": course_id})
    conn.execute(queries.get("delete_prereq_nodes_for_course"), {"course_id": course_id})
    raw = requisite_text(course.requisites, course.description)
    parsed = parse_requisites(raw)
    conn.execute(queries.get("insert_course"), {
        "course_id": course_id, "collection_id": collection_id,
        "source_id": source_id, "locator_id": locator_id,
        "code": course.code, "subject": course.code.split(" ", 1)[0], "title": course.title,
        "requisites_raw": raw, "parse_status": parsed.status, "parser_version": PARSER_VERSION,
        "canonical_code": canonical_code,
    })
    for mention in parsed.mentions:
        conn.execute(queries.get("insert_prereq_mention"), {
            "course_id": course_id, "kind": mention.kind,
            "target_code": mention.code, "position": mention.position,
        })
    if parsed.status == "parsed":
        for kind, tree in parsed.trees.items():
            _insert_nodes(conn, course_id, kind, tree)


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
        if set(stored) != {course.code for course in courses}:
            raise ValueError("stored atlas course set differs; re-import the atlas")
        for course in courses:
            row = stored[course.code]
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
) -> UUID:
    courses = load_atlas(path)
    if not courses:
        # An empty atlas would make delete_courses_not_in_atlas remove every
        # course (its `NOT code = ANY('{}')` predicate matches all rows).
        raise ValueError("atlas import requires at least one course")
    canonical = canonical_code_map(courses)

    def on_chunk_tx(tx_conn: Connection, source_id: UUID, chunk_ids: list[UUID]) -> None:
        locators = {
            row["label"]: row["locator_id"]
            for row in tx_conn.execute(
                queries.get("list_locators_for_source"), {"source_id": source_id}
            ).fetchall()
        }
        for course in courses:
            _write_course(tx_conn, collection_id, source_id, locators[course.code],
                          course, canonical[course.code])
        tx_conn.execute(
            queries.get("delete_courses_not_in_atlas"),
            {"collection_id": collection_id, "codes": [course.code for course in courses]},
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
        provenance=ATLAS_PROVENANCE,
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


def unresolved_courses(courses: list[AtlasCourse]) -> list[AtlasCourse]:
    """Courses whose requisites did not fully parse, for human review."""
    result = []
    for course in courses:
        if parse_requisites(requisite_text(course.requisites, course.description)).status in {
            "ambiguous", "unparsed",
        }:
            result.append(course)
    return result
