"""Build fixed departments -> learned topic groups -> course leaves.

Course vectors are temporary clustering inputs and stored build snapshots,
not lateral relationships. Cosine distances are computed only within a fixed
department and never persisted as course-to-course edges. No LLM is called.
"""

from collections import Counter
from dataclasses import dataclass, field
from uuid import UUID, uuid4, uuid5

import numpy as np
from psycopg import Connection
from scipy.cluster.hierarchy import fcluster
from scipy.cluster.hierarchy import linkage as scipy_linkage
from scipy.spatial.distance import pdist

from app.db import queries
from app.retrieval.config import SimgraphConfig
from app.simgraph.departments import Departments, load_departments
from app.simgraph.nudges import Nudges

ROOT_LABEL = "All courses"


@dataclass(frozen=True)
class ClusterSpec:
    parent: int | None  # index of the parent spec in the returned list
    level: int  # 0 = root, n = the n-th distance cut
    members: tuple[int, ...]  # row indices of the input matrix, ascending
    kind: str = "topic"
    department_key: str | None = None


@dataclass
class BuildResult:
    build_id: UUID
    courses: int
    edges: int  # unweighted containment links, including terminal course leaves
    clusters: int
    warnings: list[str] = field(default_factory=list)


def _to_array(value) -> np.ndarray:
    if hasattr(value, "to_numpy"):
        return value.to_numpy().astype(np.float32)
    return np.asarray(value, dtype=np.float32)


def _normalize(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    return vector if norm == 0.0 else (vector / norm).astype(np.float32)


def _condensed_index(n: int, i: int, j: int) -> int:
    if i > j:
        i, j = j, i
    return n * i - i * (i + 1) // 2 + (j - i - 1)


def _components(n: int, groups: list[list[int]]) -> list[int]:
    """Union-find over `together` groups. Returns each row's component root."""
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for group in groups:
        for other in group[1:]:
            a, b = find(group[0]), find(other)
            if a != b:
                parent[max(a, b)] = min(a, b)
    return [find(x) for x in range(n)]


def cluster_tree(
    matrix: np.ndarray,
    *,
    cuts: tuple[float, ...],
    method: str,
    together: list[list[int]] = (),
    apart: list[tuple[int, int]] = (),
) -> list[ClusterSpec]:
    """Cluster unit-norm rows into a nested tree, root first, parents before
    children. `together` groups merge at distance 0 (so they share a cluster
    at every level); `apart` pairs get the maximum distance (best effort).
    A cluster with the same members as its parent is folded into the parent.
    """
    n = matrix.shape[0]
    if n == 0:
        raise ValueError("clustering needs at least one course")
    if n == 1:
        return [ClusterSpec(parent=None, level=0, members=(0,), kind="root")]
    distances = pdist(matrix.astype(np.float64), "cosine")
    np.clip(distances, 0.0, 2.0, out=distances)

    component = _components(n, [list(group) for group in together])
    for i, j in apart:
        if component[i] != component[j]:
            distances[_condensed_index(n, i, j)] = 2.0
    by_component: dict[int, list[int]] = {}
    for row, root in enumerate(component):
        by_component.setdefault(root, []).append(row)
    for rows in by_component.values():
        for a in range(len(rows)):
            for b in range(a + 1, len(rows)):
                distances[_condensed_index(n, rows[a], rows[b])] = 0.0

    tree = scipy_linkage(distances, method=method)
    specs = [ClusterSpec(parent=None, level=0, members=tuple(range(n)), kind="root")]
    # node_of[row] = index of the deepest stored cluster containing the row.
    node_of = [0] * n
    for level, cut in enumerate(cuts, start=1):
        labels = fcluster(tree, t=cut, criterion="distance")
        groups: dict[int, list[int]] = {}
        for row in range(n):
            groups.setdefault(int(labels[row]), []).append(row)
        next_node_of = list(node_of)
        for members in sorted(groups.values(), key=lambda rows: rows[0]):
            parent = node_of[members[0]]
            if any(node_of[row] != parent for row in members):
                raise AssertionError("cluster cuts are not nested")
            if len(members) == len(specs[parent].members):
                continue  # same members as the parent: fold into it
            specs.append(ClusterSpec(parent=parent, level=level, members=tuple(members)))
            for row in members:
                next_node_of[row] = len(specs) - 1
        node_of = next_node_of
    return specs


def department_tree(
    matrix: np.ndarray, subjects: list[str], *, departments: Departments,
    cuts: tuple[float, ...], method: str,
    together: list[list[int]] = (), apart: list[tuple[int, int]] = (),
) -> list[ClusterSpec]:
    """Clustering cannot move a course across its fixed department boundary."""
    units: dict[str, list[int]] = {}
    for row, subject in enumerate(subjects):
        units.setdefault(departments.for_subject(subject).key, []).append(row)
    specs = [ClusterSpec(None, 0, tuple(range(len(subjects))), "root")]
    for key, members in sorted(units.items()):
        local_of = {row: local for local, row in enumerate(members)}
        local_together = [
            [local_of[row] for row in group] for group in together
            if all(row in local_of for row in group)
        ]
        local_apart = [
            (local_of[a], local_of[b]) for a, b in apart if a in local_of and b in local_of
        ]
        local = cluster_tree(matrix[members], cuts=cuts, method=method,
                             together=local_together, apart=local_apart)
        offset = len(specs)
        for spec in local:
            specs.append(ClusterSpec(
                0 if spec.parent is None else offset + spec.parent,
                spec.level + 1, tuple(members[row] for row in spec.members),
                "department" if spec.parent is None else "topic", key,
            ))
    return specs


def _course_rows(conn: Connection, collection_id: UUID, model: str) -> list:
    return conn.execute(
        queries.get("list_courses_with_vectors"),
        {"collection_id": collection_id, "model": model},
    ).fetchall()


def _resolve_nudges(
    nudges: Nudges, row_of: dict[str, int], warnings: list[str], department_of: list[str]
) -> tuple[list[list[int]], list[tuple[int, int]]]:
    def rows(codes, kind: str) -> list[int]:
        found = []
        for code in codes:
            if code in row_of:
                found.append(row_of[code])
            else:
                warnings.append(f"{kind}: {code} is not in the graph; skipped")
        return found

    together = []
    for group in nudges.together:
        found = rows(group, "together")
        if len(found) < 2:
            continue
        if len({department_of[row] for row in found}) != 1:
            warnings.append("together: crosses fixed department boundaries; ignored")
        else:
            together.append(found)
    component = _components(len(row_of), together)
    apart: list[tuple[int, int]] = []
    for a, b in nudges.apart:
        found = rows((a, b), "apart")
        if len(found) != 2:
            continue
        if department_of[found[0]] != department_of[found[1]]:
            continue  # already separated by a fixed boundary
        if component[found[0]] == component[found[1]]:
            warnings.append(f"apart: {a} / {b} conflicts with a together nudge; ignored")
            continue
        apart.append((found[0], found[1]))
    return together, apart


def _label(members: tuple[int, ...], subjects: list[str], title: str) -> str:
    counts = Counter(subjects[row] for row in members)
    subject = min(counts, key=lambda s: (-counts[s], s))
    return f"{subject} · {title}"


def build_simgraph(
    conn: Connection,
    *,
    collection_id: UUID,
    model: str,
    config_version: int,
    simgraph: SimgraphConfig,
    nudges: Nudges | None = None,
    departments: Departments | None = None,
) -> BuildResult:
    nudges = nudges or Nudges()
    departments = departments if departments is not None else load_departments()
    rows = _course_rows(conn, collection_id, model)
    if not rows:
        raise ValueError(
            f"need at least one course with {model!r} embeddings; import the atlas first"
        )
    ids = [row["course_id"] for row in rows]
    codes = [row["code"] for row in rows]
    titles = [row["title"] for row in rows]
    subjects = [row["subject"] for row in rows]
    matrix = np.vstack([_normalize(_to_array(row["mean_vector"])) for row in rows])
    if not np.isfinite(matrix).all() or np.any(np.linalg.norm(matrix, axis=1) == 0):
        raise ValueError("course clustering requires finite, nonzero vectors")
    row_of = {code: row for row, code in enumerate(codes)}

    # Everything is computed before the first write, then written in one
    # transaction: a failed build leaves the previous build current.
    warnings: list[str] = []
    department_of = [departments.for_subject(subject).key for subject in subjects]
    together, apart = _resolve_nudges(nudges, row_of, warnings, department_of)
    specs = department_tree(
        matrix,
        subjects,
        departments=departments,
        cuts=simgraph.level_cut_distances,
        method=simgraph.linkage,
        together=together,
        apart=apart,
    )

    build_id = uuid4()
    atlas_row = conn.execute(
        queries.get("get_current_atlas_source_hash"), {"collection_id": collection_id}
    ).fetchone()
    content_hash = atlas_row["file_hash"] if atlas_row else None
    clusters = []
    for spec in specs:
        sub = matrix[list(spec.members)]
        total = sub.sum(axis=0)
        # Summed similarity to the other members; ties go to the lowest code
        # because rows are in code order and argmax takes the first maximum.
        medoid = spec.members[0] if spec.kind == "root" else spec.members[
            int(np.argmax(sub @ total))]
        clusters.append(
            {
                "cluster_id": uuid5(build_id,
                                    f"{spec.kind}:{spec.level}:{codes[spec.members[0]]}"),
                "label": ROOT_LABEL
                if spec.parent is None
                else departments.for_subject(subjects[spec.members[0]]).label
                if spec.kind == "department"
                else _label(spec.members, subjects, titles[medoid]),
                "medoid": medoid,
                "medoid_code": codes[medoid],
                "medoid_title": titles[medoid],
                "centroid": _normalize(total),
            }
        )
    _apply_label_nudges(nudges, specs, clusters, row_of, warnings)
    _check_apart(apart, specs, codes, warnings)

    with conn.transaction():
        conn.execute(
            queries.get("insert_sim_build"),
            {
                "build_id": build_id,
                "collection_id": collection_id,
                "model": model,
                "config_version": config_version,
                "nudges_hash": nudges.file_hash,
                "content_hash": content_hash,
            },
        )
        conn.execute(queries.get("set_sim_departments_hash"), {
            "build_id": build_id, "departments_hash": departments.digest,
        })
        with conn.cursor() as cur:
            cur.executemany(
                queries.get("insert_sim_course"),
                [
                    {"build_id": build_id, "course_id": ids[row], "embedding": matrix[row]}
                    for row in range(len(ids))
                ],
            )
            for spec, cluster in zip(specs, clusters, strict=True):
                cur.execute(
                    queries.get("insert_sim_cluster"),
                    {
                        "cluster_id": cluster["cluster_id"],
                        "build_id": build_id,
                        "parent_cluster_id": None
                        if spec.parent is None
                        else clusters[spec.parent]["cluster_id"],
                        "level": spec.level,
                        "label": cluster["label"],
                        "medoid_course_id": ids[cluster["medoid"]],
                        "medoid_code": cluster["medoid_code"],
                        "medoid_title": cluster["medoid_title"],
                        "centroid": cluster["centroid"],
                        "size": len(spec.members),
                        "kind": spec.kind,
                        "department_key": spec.department_key,
                    },
                )
                cur.executemany(
                    queries.get("insert_sim_cluster_member"),
                    [
                        {"cluster_id": cluster["cluster_id"], "course_id": ids[row],
                         "code": codes[row], "title": titles[row]}
                        for row in spec.members
                    ],
                )
        conn.execute(queries.get("clear_current_build"), {"collection_id": collection_id})
        conn.execute(queries.get("set_current_build"), {"build_id": build_id})
    return BuildResult(build_id, len(ids), len(specs) - 1 + len(ids), len(specs), warnings)


def _deepest(specs: list[ClusterSpec], row: int, max_level: int) -> int:
    """The deepest cluster at or above `max_level` holding `row`. Specs are in
    level order, so the last match is the deepest."""
    node = 0
    for index, spec in enumerate(specs):
        if spec.level <= max_level and row in spec.members:
            node = index
    return node


def _apply_label_nudges(nudges, specs, clusters, row_of, warnings) -> None:
    claimed: dict[int, str] = {}
    for nudge in nudges.labels:
        if nudge.course not in row_of:
            warnings.append(f"label: {nudge.course} is not in the graph; skipped")
            continue
        node = _deepest(specs, row_of[nudge.course], nudge.level)
        if specs[node].kind != "topic":
            warnings.append(f"label: no level-{nudge.level} cluster holds {nudge.course}")
            continue
        if node in claimed:
            warnings.append(
                f"label: {nudge.text!r} and {claimed[node]!r} name the same cluster; "
                f"kept {claimed[node]!r}"
            )
            continue
        claimed[node] = nudge.text
        clusters[node]["label"] = nudge.text


def _check_apart(apart, specs, codes, warnings) -> None:
    for i, j in apart:
        shared = [
            spec.level
            for spec in specs
            if spec.kind == "topic" and i in spec.members and j in spec.members
        ]
        if shared:
            warnings.append(
                f"apart: {codes[i]} / {codes[j]} still share a cluster down to level {max(shared)}"
            )
