"""Import the course atlas and build the prerequisite graph. No LLM."""

from collections import Counter
from collections.abc import Callable
from pathlib import Path
from uuid import UUID, uuid5

from psycopg import Connection

from app.atlas.records import AtlasCourse, load_atlas
from app.atlas.requisites import (
    PARSER_VERSION,
    AllOf,
    AnyOf,
    Condition,
    CourseRef,
    Req,
    parse_requisites,
)
from app.db import queries
from app.embeddings.base import Embedder
from app.ingest.pipeline import ingest_file
from app.retrieval.config import RetrievalConfig

ATLAS_CONTENT_TYPE = "application/x-course-atlas+jsonl"


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

    # The old atlas source is removed first; its courses / mentions / nodes /
    # chunks cascade away.
    for row in conn.execute(
        queries.get("list_atlas_sources"),
        {"collection_id": collection_id, "content_type": ATLAS_CONTENT_TYPE},
    ).fetchall():
        conn.execute(queries.get("delete_source"), {"source_id": row["source_id"]})
    conn.commit()

    def on_chunk_tx(tx_conn: Connection, source_id: UUID, chunk_ids: list[UUID]) -> None:
        locators = {
            row["label"]: row["locator_id"]
            for row in tx_conn.execute(
                queries.get("list_locators_for_source"), {"source_id": source_id}
            ).fetchall()
        }
        for course in courses:
            course_id = _course_id(collection_id, course.code)
            parsed = parse_requisites(course.requisites)
            subject = course.code.split(" ", 1)[0]
            tx_conn.execute(
                queries.get("insert_course"),
                {
                    "course_id": course_id,
                    "collection_id": collection_id,
                    "source_id": source_id,
                    "locator_id": locators[course.code],
                    "code": course.code,
                    "subject": subject,
                    "title": course.title,
                    "requisites_raw": course.requisites,
                    "parse_status": parsed.status,
                    "parser_version": PARSER_VERSION,
                },
            )
            for mention in parsed.mentions:
                tx_conn.execute(
                    queries.get("insert_prereq_mention"),
                    {
                        "course_id": course_id,
                        "kind": mention.kind,
                        "target_code": mention.code,
                        "position": mention.position,
                    },
                )
            for kind, tree in parsed.trees.items():
                _insert_nodes(tx_conn, course_id, kind, tree)
        for course in courses:
            tx_conn.execute(
                queries.get("resolve_mention_targets"),
                {
                    "course_id": _course_id(collection_id, course.code),
                    "collection_id": collection_id,
                },
            )

    source_id = ingest_file(
        conn,
        collection_id=collection_id,
        path=path,
        content_type=ATLAS_CONTENT_TYPE,
        count_tokens=count_tokens,
        embedder=embedder,
        embed_model=embed_model,
        config=config,
        on_chunk_transaction=on_chunk_tx,
    )

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
        if parse_requisites(course.requisites).status in {"ambiguous", "unparsed"}:
            result.append(course)
    return result
