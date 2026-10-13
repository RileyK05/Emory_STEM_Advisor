"""Route a goal toward department/topic centroids; return whole course groups.

This is probabilistic topic exploration, not course-to-course recommendations
or a prerequisite sequence. Semantic chunk retrieval remains a separate seam.
"""

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
        # A mixed parent's centroid can score lower than the department even
        # when it contains an excellent deeper topic. Consider all descendants
        # so that this centroid barrier cannot hide a relevant small group.
        descendants, pending = [], list(children.get(branch, []))
        while pending:
            cid = pending.pop()
            pending.extend(children.get(cid, []))
            if nodes[cid]["size"] >= simgraph.walk_min_subtree:
                descendants.append(cid)
        node = min([branch, *descendants], key=lambda cid: (
            -score[cid], nodes[cid]["level"], nodes[cid]["label"], str(cid),
        ))
        ancestors = [node]
        while ancestors[-1] != branch:
            ancestors.append(nodes[ancestors[-1]]["parent_cluster_id"])
        path = [nodes[cid]["label"] for cid in reversed(ancestors)]

        members = conn.execute(queries.get("list_cluster_courses"), {"cluster_id": node}).fetchall()
        members = [row for row in members if row["course_id"] not in seen]
        if not members:
            continue
        seen.update(row["course_id"] for row in members)
        recommendations.append(
            Recommendation(
                cluster_id=node,
                path=tuple(path),
                courses=[(row["course_id"], row["code"], row["title"]) for row in members],
            )
        )
    return recommendations
