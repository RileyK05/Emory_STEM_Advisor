"""Subgraph view for visuals: the courses under a cluster and the similarity
edges among them. Prerequisite edges are not drawn (open question O1)."""

from uuid import UUID

from psycopg import Connection

from app.db import queries


def subgraph_view(conn: Connection, *, cluster_id: UUID) -> tuple[list[dict], list[dict]]:
    nodes = [
        {"course_id": row["course_id"], "code": row["code"], "title": row["title"]}
        for row in conn.execute(
            queries.get("list_cluster_courses"), {"cluster_id": cluster_id}
        ).fetchall()
    ]
    edges = [
        {"course_a": row["course_a"], "course_b": row["course_b"], "weight": row["weight"]}
        for row in conn.execute(
            queries.get("list_cluster_edges"), {"cluster_id": cluster_id}
        ).fetchall()
    ]
    return nodes, edges
