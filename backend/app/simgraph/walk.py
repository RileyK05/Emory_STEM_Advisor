"""Recommend: walk the cluster tree toward the query, then return the whole
subtree. No top-k anywhere: every course under the chosen node is returned,
sorted for display but never truncated, so niche courses are not dropped."""

from dataclasses import dataclass
from uuid import UUID

import numpy as np
from psycopg import Connection

from app.db import queries
from app.retrieval.config import SimgraphConfig


@dataclass(frozen=True)
class Recommendation:
    cluster_id: UUID
    path: tuple[str, ...]  # labels from the top split down to the chosen node
    courses: list[tuple[UUID, str, str]]  # (course_id, code, title)
    scores: list[float]


def _to_array(value) -> np.ndarray:
    if hasattr(value, "to_numpy"):
        return value.to_numpy().astype(np.float32)
    return np.asarray(value, dtype=np.float32)


def recommend(
    conn: Connection,
    *,
    collection_id: UUID,
    query_vec: np.ndarray,
    simgraph: SimgraphConfig,
) -> list[Recommendation]:
    rows = conn.execute(
        queries.get("list_current_clusters"), {"collection_id": collection_id}
    ).fetchall()
    if not rows:
        return []
    nodes = {row["cluster_id"]: row for row in rows}
    score = {
        cid: float(np.dot(query_vec, _to_array(row["centroid"]))) for cid, row in nodes.items()
    }
    children: dict[UUID | None, list[UUID]] = {}
    for cid, row in nodes.items():
        children.setdefault(row["parent_cluster_id"], []).append(cid)
    for kids in children.values():
        kids.sort(key=lambda cid: (-score[cid], str(cid)))

    (root,) = children[None]
    top = children.get(root, [])
    if not top:
        return []
    best = score[top[0]]
    branches = [
        cid
        for cid in top
        if score[cid] >= best - simgraph.walk_top_margin
        and score[cid] >= simgraph.walk_min_similarity
    ]

    recommendations: list[Recommendation] = []
    seen: set[UUID] = set()
    for branch in branches:
        node, path = branch, [nodes[branch]["label"]]
        while True:
            eligible = [
                cid
                for cid in children.get(node, [])
                if nodes[cid]["size"] >= simgraph.walk_min_subtree
            ]
            # Children are sorted by score, so the first eligible one is the argmax.
            if not eligible or score[eligible[0]] <= score[node]:
                break
            node = eligible[0]
            path.append(nodes[node]["label"])

        members = conn.execute(queries.get("list_cluster_courses"), {"cluster_id": node}).fetchall()
        scored = sorted(
            (
                (float(np.dot(query_vec, _to_array(row["embedding"]))), row)
                for row in members
                if row["course_id"] not in seen
            ),
            key=lambda item: (-item[0], item[1]["code"]),
        )
        if not scored:
            continue
        seen.update(row["course_id"] for _, row in scored)
        recommendations.append(
            Recommendation(
                cluster_id=node,
                path=tuple(path),
                courses=[(row["course_id"], row["code"], row["title"]) for _, row in scored],
                scores=[value for value, _ in scored],
            )
        )
    return recommendations
