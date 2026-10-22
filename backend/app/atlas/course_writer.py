"""Deterministic writes of a parsed requisites tree.

Shared by the catalog importer and reviewed admin publication so both paths write
the same mentions, nodes and identities. No LLM and no model-derived edge ever
enters this module.
"""

from uuid import UUID, uuid5

from psycopg import Connection

from app.atlas.requisites import AllOf, AnyOf, Condition, CourseRef, ParsedRequisites, Req
from app.db import queries


def node_id(course_id: UUID, kind: str, path: str) -> UUID:
    return uuid5(course_id, f"{kind}:{path}")


def insert_nodes(conn: Connection, course_id: UUID, kind: str, tree: Req) -> None:
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
        conn.execute(
            queries.get("insert_prereq_node"),
            {
                "node_id": current,
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
                walk(child, current, f"{path}.{index}", index)

    walk(tree, None, "0", 0)


def replace_course_requisites(
    conn: Connection, course_id: UUID, parsed: ParsedRequisites
) -> None:
    """Replace parser-derived rows with exactly what the parser produced.

    Unresolved wording is retained only as raw course text; nothing is invented.
    """
    conn.execute(queries.get("delete_prereq_mentions_for_course"), {"course_id": course_id})
    conn.execute(queries.get("delete_prereq_nodes_for_course"), {"course_id": course_id})
    for mention in parsed.mentions:
        conn.execute(
            queries.get("insert_prereq_mention"),
            {
                "course_id": course_id,
                "kind": mention.kind,
                "target_code": mention.code,
                "position": mention.position,
            },
        )
    if parsed.status == "parsed":
        for kind, tree in parsed.trees.items():
            insert_nodes(conn, course_id, kind, tree)
