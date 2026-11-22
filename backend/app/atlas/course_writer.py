"""Deterministic writes of parsed requisites trees.

Shared by the catalog importer and reviewed admin publication so both paths write
the same mentions, nodes and identities. No LLM and no model-derived edge ever
enters this module.

Writes are batched: one statement per table for however many courses change, so
a full Atlas import costs a handful of round trips rather than several per course.
"""

from collections.abc import Iterable, Mapping
from uuid import UUID, uuid5

from psycopg import Connection, errors

from app.atlas.requisites import AllOf, AnyOf, Condition, CourseRef, ParsedRequisites, Req
from app.db import queries


def course_uuid(collection_id: UUID, code: str) -> UUID:
    """Content-addressed course identity (D3): every writer must derive it here."""
    return uuid5(collection_id, f"course:{code}")


def node_id(course_id: UUID, kind: str, path: str) -> UUID:
    return uuid5(course_id, f"{kind}:{path}")


def node_rows(course_id: UUID, kind: str, tree: Req) -> list[dict]:
    """The tree's rows in pre-order, so every parent precedes its children."""
    rows: list[dict] = []

    def walk(node: Req, parent: UUID | None, path: str, position: int) -> None:
        current = node_id(course_id, kind, path)
        if isinstance(node, CourseRef):
            node_type, target_code, condition_text = "course", node.code, None
        elif isinstance(node, Condition):
            node_type, target_code, condition_text = "condition", None, node.text
        elif isinstance(node, AllOf):
            node_type, target_code, condition_text = "all", None, None
        else:
            node_type, target_code, condition_text = "any", None, None
        rows.append({
            "node_id": current,
            "course_id": course_id,
            "kind": kind,
            "parent_node_id": parent,
            "position": position,
            "node_type": node_type,
            "target_code": target_code,
            "condition_text": condition_text,
        })
        if isinstance(node, (AllOf, AnyOf)):
            for index, child in enumerate(node.items):
                walk(child, current, f"{path}.{index}", index)

    walk(tree, None, "0", 0)
    return rows


def replace_requisites(conn: Connection, parsed_by_course: Mapping[UUID, ParsedRequisites]) -> None:
    """Replace parser-derived rows with exactly what the parser produced.

    Unresolved wording is retained only as raw course text; nothing is invented.
    """
    if not parsed_by_course:
        return
    course_ids = list(parsed_by_course)
    conn.execute(queries.get("delete_prereq_mentions_for_courses"), {"course_ids": course_ids})
    conn.execute(queries.get("delete_prereq_nodes_for_courses"), {"course_ids": course_ids})
    mentions: list[dict] = []
    nodes: list[dict] = []
    for course_id, parsed in parsed_by_course.items():
        mentions.extend({
            "course_id": course_id,
            "kind": mention.kind,
            "target_code": mention.code,
            "position": mention.position,
        } for mention in parsed.mentions)
        if parsed.status == "parsed":
            for kind, tree in parsed.trees.items():
                nodes.extend(node_rows(course_id, kind, tree))
    queries.execute_many(conn, "insert_prereq_mention", mentions)
    queries.execute_many(conn, "insert_prereq_node", nodes)


def replace_course_requisites(
    conn: Connection, course_id: UUID, parsed: ParsedRequisites
) -> None:
    """One course's `replace_requisites`, for reviewed admin publication."""
    replace_requisites(conn, {course_id: parsed})


def link_mention_targets(
    conn: Connection, collection_id: UUID, course_ids: Iterable[UUID]
) -> None:
    """Point these courses' mentions at the course rows their codes name."""
    ids = list(course_ids)
    if ids:
        conn.execute(queries.get("resolve_mention_targets"),
                     {"course_ids": ids, "collection_id": collection_id})


def refresh_graph_statistics(conn: Connection) -> None:
    """Refresh planner statistics after a bulk rewrite; call it after committing.

    Only the tables' owner may ANALYZE them. A role that may not leaves the
    statistics to autovacuum, which is slower to notice but still correct.
    """
    try:
        conn.execute(queries.get("analyze_course_graph"))
        conn.commit()
    except errors.InsufficientPrivilege:
        conn.rollback()
