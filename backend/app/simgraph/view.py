"""Department/topic containment tree, with each course attached to its leaf.

No weighted similarity edges, and no prerequisite edges mixed into this tree.
"""

from uuid import UUID

from psycopg import Connection

from app.db import queries


def subgraph_view(conn: Connection, *, cluster_id: UUID) -> tuple[list[dict], list[dict]]:
    params = {"cluster_id": cluster_id}
    clusters = conn.execute(queries.get("list_cluster_descendants"), params).fetchall()
    nodes, edges = [], []
    included = {row["cluster_id"] for row in clusters}
    for row in clusters:
        nodes.append({"id": str(row["cluster_id"]), "kind": row["kind"],
                      "label": row["label"], "departmentKey": row["department_key"]})
        if row["parent_cluster_id"] in included:
            edges.append({"parentId": str(row["parent_cluster_id"]),
                          "childId": str(row["cluster_id"]), "kind": "contains"})
    for row in conn.execute(queries.get("list_subtree_leaf_courses"), params).fetchall():
        nodes.append({"id": str(row["course_id"]), "kind": "course",
                      "label": row["title"], "departmentKey": row["department_key"],
                      "courseId": str(row["course_id"]), "code": row["code"],
                      "title": row["title"]})
        edges.append({"parentId": str(row["cluster_id"]),
                      "childId": str(row["course_id"]), "kind": "contains"})
    return nodes, edges
