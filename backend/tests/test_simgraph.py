"""Department boundaries, learned topic hierarchy, containment, goal routing."""

import dataclasses

import numpy as np
import pytest

import app.atlas.importer as atlas_importer
import app.simgraph.build as build_mod
from app.atlas.importer import import_atlas
from app.db import queries
from app.retrieval.config import ConfigError, load_retrieval_config
from app.simgraph.build import build_simgraph, cluster_tree, department_tree
from app.simgraph.departments import Departments, parse_departments
from app.simgraph.nudges import LabelNudge, NudgeError, Nudges, load_nudges, parse_nudges
from app.simgraph.view import subgraph_view
from app.simgraph.walk import recommend
from tests.fakes import whitespace_tokens

_MODEL = "planted-model"
_DIM = 8


def _unit(*values: float) -> np.ndarray:
    vec = np.zeros(_DIM, dtype=np.float32)
    vec[: len(values)] = values
    return vec / np.linalg.norm(vec)


def _jitter(base: np.ndarray, k: int, axis: int) -> np.ndarray:
    vec = base.copy()
    vec[axis] += 0.02 * k
    return vec / np.linalg.norm(vec)


_MACRO = _unit(1.0, 0.3)
_MICRO = _unit(0.3, 1.0)
_MATH = _unit(0, 0, 0, 1.0)
_CS = _unit(0, 0, 0, 0, 0, 1.0)

# Seven macro courses: more than cited_set_size (6), so a top-k would cut them.
_PLANTED: dict[str, np.ndarray] = {}
for k, code in enumerate(
    ["ECON 101", "ECON 201", "ECON 301", "ECON 305", "ECON 310", "ECON 320", "ECON 330"]
):
    _PLANTED[code] = _jitter(_MACRO, k, 6)
for k, code in enumerate(["ECON 210", "ECON 211"]):
    _PLANTED[code] = _jitter(_MICRO, k, 7)
for k, code in enumerate(["MATH 111", "MATH 112", "MATH 221"]):
    _PLANTED[code] = _jitter(_MATH, k, 6)
for k, code in enumerate(["CS 170", "CS 171"]):
    _PLANTED[code] = _jitter(_CS, k, 7)

_CODES = sorted(_PLANTED)
_MATRIX = np.vstack([_PLANTED[code] for code in _CODES])


class PlantedEmbedder:
    """Each course's chunks get that course's planted vector."""

    def _vector(self, text: str) -> np.ndarray:
        matches = [code for code in _PLANTED if code in text]
        assert len(matches) == 1, text
        return _PLANTED[matches[0]]

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return np.vstack([self._vector(text) for text in texts]).astype(np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self._vector(text)


def _config():
    return load_retrieval_config().simgraph


def _tree(**kwargs):
    config = _config()
    return cluster_tree(_MATRIX, cuts=(0.7, 0.5, 0.35), method=config.linkage, **kwargs)


def _rows(*codes: str) -> list[int]:
    return [_CODES.index(code) for code in codes]


def _shared_levels(specs, a: int, b: int) -> set[int]:
    return {s.level for s in specs if a in s.members and b in s.members}


# --- pure clustering ------------------------------------------------------


def test_cuts_nest_and_identical_children_fold():
    specs = _tree()
    assert specs[0].parent is None and len(specs[0].members) == len(_CODES)
    for spec in specs[1:]:
        parent = specs[spec.parent]
        assert set(spec.members) < set(parent.members)
        assert spec.level > parent.level


def test_groups_land_where_planted():
    specs = _tree()
    top = [s for s in specs if s.parent == 0]
    names = sorted(sorted(_CODES[r] for r in s.members) for s in top)
    econ = sorted(c for c in _CODES if c.startswith("ECON"))
    assert econ in names
    assert ["MATH 111", "MATH 112", "MATH 221"] in names
    assert ["CS 170", "CS 171"] in names
    macro = sorted(c for c in econ if c not in {"ECON 210", "ECON 211"})
    assert any(sorted(_CODES[r] for r in s.members) == macro for s in specs)


def test_cluster_tree_is_deterministic():
    assert _tree() == _tree()


def test_together_nudge_shares_a_cluster_at_every_level():
    a, b = _rows("MATH 111", "CS 170")
    assert _shared_levels(_tree(), a, b) == {0}
    nudged = _tree(together=[[a, b]])
    assert _shared_levels(nudged, a, b) == {s.level for s in nudged if a in s.members}
    assert len(_shared_levels(nudged, a, b)) > 1


def test_identical_vectors_in_different_departments_never_merge():
    matrix = np.vstack([_MACRO, _MACRO, _MACRO, _MACRO])
    specs = department_tree(matrix, ["ECON", "MATH", "ECON", "CS"],
                            departments=Departments(), cuts=(0.18, 0.12, 0.08), method="average")
    departments = [s for s in specs if s.kind == "department"]
    assert {s.department_key: s.members for s in departments} == {
        "ECON": (0, 2), "MATH": (1,), "CS": (3,),
    }
    assert all(s.parent == 0 for s in departments)
    assert _shared_levels(specs, 0, 1) == {0}


def test_explicit_department_membership_can_group_subjects():
    departments = parse_departments({"version": 1, "departments": {
        "quantitative": {"label": "Quantitative studies", "subjects": ["MATH", "CS"]},
    }})
    specs = department_tree(np.vstack([_MATH, _CS, _MACRO]), ["MATH", "CS", "ECON"],
                            departments=departments, cuts=(0.18,), method="average")
    assert {s.department_key: s.members for s in specs if s.kind == "department"} == {
        "quantitative": (0, 1), "ECON": (2,),
    }


def test_department_config_rejects_ambiguous_membership():
    with pytest.raises(ValueError, match="exactly one"):
        parse_departments({"version": 1, "departments": {
            "a": {"label": "A", "subjects": ["ECON"]},
            "b": {"label": "B", "subjects": ["ECON"]},
        }})


def test_apart_nudge_splits_a_close_pair():
    matrix = np.vstack([_unit(1.0), _unit(1.0), _unit(0, 1.0)])
    plain = cluster_tree(matrix, cuts=(1.4, 0.5), method="average")
    assert _shared_levels(plain, 0, 1) - {0}
    nudged = cluster_tree(matrix, cuts=(1.4, 0.5), method="average", apart=[(0, 1)])
    assert _shared_levels(nudged, 0, 1) == {0}


# --- nudges file ----------------------------------------------------------


def test_missing_nudges_file_means_no_nudges(tmp_path):
    assert load_nudges(tmp_path / "absent.toml") == Nudges()


def test_nudges_reject_bad_input():
    with pytest.raises(NudgeError):
        parse_nudges({"together": [{"courses": ["ECON 101", "not a code"]}]})
    with pytest.raises(NudgeError):
        parse_nudges({"apart": [{"courses": ["ECON 101", "ECON 201", "ECON 301"]}]})
    with pytest.raises(NudgeError):
        parse_nudges({"label": [{"level": 0, "course": "ECON 101", "text": "x"}]})
    with pytest.raises(NudgeError):
        parse_nudges({"pin": []})
    parsed = parse_nudges({"together": [{"courses": ["econ101", "Econ 201"]}]})
    assert parsed.together == (("ECON 101", "ECON 201"),)


def test_config_rejects_non_nesting_cuts(tmp_path):
    from app.retrieval import config as config_mod

    text = config_mod._CONFIG_PATH.read_text(encoding="utf-8")
    bad = tmp_path / "retrieval.toml"
    bad.write_text(
        text.replace("level_cut_distances = [0.18, 0.12, 0.08]", "level_cut_distances = [0.5, 0.7]"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="decreasing"):
        load_retrieval_config(bad)
    bad.write_text(text.replace('linkage = "average"', 'linkage = "ward"'), encoding="utf-8")
    with pytest.raises(ConfigError, match="linkage"):
        load_retrieval_config(bad)


# --- database -------------------------------------------------------------


def _import(db, collection, tmp_path, codes=_CODES, *, allow_course_removal=False):
    lines = [
        f'{{"code": "{code}", "title": "Title {code}", "description": "about {code}"}}'
        for code in codes
    ]
    path = tmp_path / "atlas.jsonl"
    path.write_text("\n".join(lines), encoding="utf-8")
    import_atlas(
        db,
        collection_id=collection,
        path=path,
        embedder=PlantedEmbedder(),
        embed_model=_MODEL,
        count_tokens=whitespace_tokens,
        config=load_retrieval_config(),
        allow_course_removal=allow_course_removal,
    )


def _build(db, collection, simgraph=None, nudges=None):
    return build_simgraph(
        db,
        collection_id=collection,
        model=_MODEL,
        config_version=1,
        simgraph=simgraph or _config(),
        nudges=nudges,
    )


def _clusters(db, build_id):
    rows = db.execute(
        "SELECT c.level, c.label, array_agg(co.code ORDER BY co.code) AS codes"
        " FROM sim_clusters c"
        " JOIN sim_cluster_members m ON m.cluster_id = c.cluster_id"
        " JOIN courses co ON co.course_id = m.course_id"
        " WHERE c.build_id = %(b)s GROUP BY c.cluster_id, c.level, c.label",
        {"b": build_id},
    ).fetchall()
    return sorted((row["level"], row["label"], tuple(row["codes"])) for row in rows)


def _recommend(db, collection, query=_MACRO):
    return recommend(db, collection_id=collection, query_vec=query, simgraph=_config())


def test_no_course_similarity_table_and_departments_are_fixed(db, collection, tmp_path):
    _import(db, collection, tmp_path)
    result = _build(db, collection)
    assert db.execute("SELECT to_regclass('public.sim_edges') AS t").fetchone()["t"] is None
    clusters = db.execute(queries.get("list_current_clusters"), {
        "collection_id": collection,
    }).fetchall()
    assert {r["department_key"] for r in clusters if r["kind"] == "department"} == {
        "ECON", "MATH", "CS",
    }
    for cluster in clusters:
        if cluster["kind"] == "root":
            continue
        courses = db.execute(queries.get("list_cluster_courses"), {
            "cluster_id": cluster["cluster_id"],
        }).fetchall()
        assert all(r["code"].split()[0] == cluster["department_key"] for r in courses)
    assert result.edges == result.clusters - 1 + result.courses


def test_recommend_returns_whole_subtree_not_top_k(db, collection, tmp_path):
    _import(db, collection, tmp_path)
    _build(db, collection)
    recs = _recommend(db, collection)
    assert len(recs) == 1
    codes = [code for _, code, _ in recs[0].courses]
    macro = {c for c in _CODES if c.startswith("ECON")} - {"ECON 210", "ECON 211"}
    assert set(codes) == macro
    assert len(codes) > load_retrieval_config().fusion.cited_set_size
    assert codes[0] == "ECON 101"  # closest to the query first
    assert codes == sorted(codes)
    assert len(recs[0].path) == 2  # econ, then down into macro


def test_walk_stops_above_children_smaller_than_min_subtree(db, collection, tmp_path):
    _import(db, collection, tmp_path)
    _build(db, collection)
    recs = recommend(db, collection_id=collection, query_vec=_MICRO,
                     simgraph=dataclasses.replace(_config(), walk_min_subtree=5))
    # The micro cluster has 2 courses (< walk_min_subtree), so the walk stays
    # at economics and returns all of it.
    codes = {code for _, code, _ in recs[0].courses}
    assert codes == {c for c in _CODES if c.startswith("ECON")}
    assert len(recs[0].path) == 1


def test_building_twice_gives_identical_clusters(db, collection, tmp_path):
    _import(db, collection, tmp_path)
    first = _build(db, collection)
    second = _build(db, collection)
    assert _clusters(db, first.build_id) == _clusters(db, second.build_id)
    current = db.execute(
        "SELECT COUNT(*) AS n FROM sim_builds WHERE collection_id = %(c)s AND is_current",
        {"c": collection},
    ).fetchone()
    assert current["n"] == 1


def test_failed_build_keeps_previous_recommendations(db, collection, tmp_path, monkeypatch):
    _import(db, collection, tmp_path)
    _build(db, collection)
    db.commit()
    before = _recommend(db, collection)

    def boom(*args, **kwargs):
        raise RuntimeError("build exploded")

    original_query = queries.get

    def failing_query(name):
        if name == "insert_sim_cluster":
            return "SELECT 1/0"
        return original_query(name)

    monkeypatch.setattr(queries, "get", failing_query)
    import psycopg

    with pytest.raises(psycopg.errors.DivisionByZero):
        _build(db, collection)
    db.rollback()
    monkeypatch.setattr(queries, "get", original_query)

    monkeypatch.setattr(build_mod, "department_tree", boom)
    with pytest.raises(RuntimeError, match="build exploded"):
        _build(db, collection)
    db.rollback()

    after = _recommend(db, collection)
    assert [(r.path, r.courses) for r in after] == [(r.path, r.courses) for r in before]
    builds = db.execute(
        "SELECT COUNT(*) AS n FROM sim_builds WHERE collection_id = %(c)s", {"c": collection}
    ).fetchone()
    assert builds["n"] == 1


def test_nudges_apply_and_report(db, collection, tmp_path):
    _import(db, collection, tmp_path)
    nudges = Nudges(
        together=(("MATH 111", "CS 170"),),
        apart=(("ECON 101", "ECON 201"),),
        labels=(
            LabelNudge(4, "ECON 101", "Macro topics"),
            LabelNudge(4, "ECON 330", "Also macro"),
            LabelNudge(4, "HIST 100", "History"),
        ),
        file_hash="abc",
    )
    result = _build(db, collection, dataclasses.replace(
        _config(), level_cut_distances=(0.7, 0.5, 0.35)), nudges=nudges)
    clusters = _clusters(db, result.build_id)
    assert any(label == "Macro topics" for _, label, _ in clusters)
    assert all(not ("MATH 111" in codes and "CS 170" in codes) for level, _, codes in clusters
               if level > 0)
    joined = "\n".join(result.warnings)
    assert "HIST 100 is not in the graph" in joined
    assert "name the same cluster" in joined
    assert "crosses fixed department boundaries" in joined
    # Two courses inside a tight group of seven cannot be pushed apart; the
    # build says so instead of failing silently.
    assert "ECON 101 / ECON 201 still share a cluster" in joined
    build = db.execute(queries.get("get_current_build"), {"collection_id": collection}).fetchone()
    assert build["nudges_hash"] == "abc"


def test_subgraph_view_is_a_containment_tree_with_one_leaf_per_course(db, collection, tmp_path):
    _import(db, collection, tmp_path)
    _build(db, collection)
    recs = _recommend(db, collection)
    nodes, edges = subgraph_view(db, cluster_id=recs[0].cluster_id)
    course_nodes = [n for n in nodes if n["kind"] == "course"]
    assert {n["code"] for n in course_nodes} == {code for _, code, _ in recs[0].courses}
    ids = {n["id"] for n in nodes}
    assert len(ids) == len(nodes)
    assert len(edges) == len(nodes) - 1
    assert all(e["parentId"] in ids and e["childId"] in ids for e in edges)
    assert all(e["kind"] == "contains" and "weight" not in e for e in edges)
    assert all(sum(e["childId"] == n["id"] for e in edges) == 1 for n in course_nodes)


def test_root_view_contains_departments_topics_and_course_leaves(db, collection, tmp_path):
    _import(db, collection, tmp_path)
    result = _build(db, collection)
    root = next(r for r in db.execute(queries.get("list_current_clusters"), {
        "collection_id": collection,
    }).fetchall() if r["kind"] == "root")
    nodes, edges = subgraph_view(db, cluster_id=root["cluster_id"])
    assert {n["kind"] for n in nodes} == {"root", "department", "topic", "course"}
    assert len([n for n in nodes if n["kind"] == "course"]) == len(_CODES)
    assert len(edges) == result.edges == len(nodes) - 1


def test_small_topic_can_be_selected_without_scoring_course_pairs(db, collection, tmp_path):
    _import(db, collection, tmp_path)
    _build(db, collection)
    recs = _recommend(db, collection, query=_MICRO)
    assert {c for _, c, _ in recs[0].courses} == {"ECON 210", "ECON 211"}
    assert recs[0].path[0] == "Economics"


def test_single_course_department_remains_visible(db, collection, tmp_path):
    _import(db, collection, tmp_path, codes=["CS 170"], allow_course_removal=True)
    result = _build(db, collection)
    recs = _recommend(db, collection, query=_CS)
    assert result.courses == 1 and result.clusters == 2 and result.edges == 2
    assert recs[0].courses[0][1] == "CS 170"
    assert recs[0].path == ("Computer Science",)


def test_http_graph_returns_typed_containment_and_whole_topic_groups(db, collection, tmp_path):
    from tests.helpers import api_client, collection_name

    _import(db, collection, tmp_path)
    _build(db, collection)

    class GoalEmbedder:
        def embed_query(self, text):
            return _MACRO

    client = api_client(db, collection, embedder=GoalEmbedder())
    response = client.get("/api/recommendations", params={
        "query": "a career in macroeconomics", "collection": collection_name(db, collection),
    })
    assert response.status_code == 200
    group = response.json()[0]
    assert group["path"][0] == "Economics" and len(group["courses"]) == 7
    view = client.get(f"/api/subgraph/{group['clusterId']}")
    assert view.status_code == 200
    tree = view.json()
    assert {n["code"] for n in tree["nodes"] if n["kind"] == "course"} == {
        c["code"] for c in group["courses"]
    }
    assert all(e["kind"] == "contains" and "weight" not in e for e in tree["edges"])
    assert len(tree["edges"]) == len(tree["nodes"]) - 1


def test_retired_build_does_not_serve_its_tree(db, collection, tmp_path):
    _import(db, collection, tmp_path)
    first = _build(db, collection)
    root = next(r for r in db.execute(queries.get("list_current_clusters"), {
        "collection_id": collection,
    }).fetchall() if r["kind"] == "root")
    second = _build(db, collection)
    assert first.build_id != second.build_id
    assert subgraph_view(db, cluster_id=root["cluster_id"]) == ([], [])


def test_relevant_deep_topic_is_not_hidden_by_a_mixed_parent_centroid(db, collection, tmp_path):
    from uuid import uuid4

    _import(db, collection, tmp_path)
    build = _build(db, collection)
    clusters = db.execute(queries.get("list_current_clusters"), {
        "collection_id": collection,
    }).fetchall()
    department = next(r for r in clusters if r["kind"] == "department"
                      and r["department_key"] == "ECON")
    barrier = uuid4()
    db.execute(queries.get("insert_sim_cluster"), {
        "cluster_id": barrier, "build_id": build.build_id,
        "parent_cluster_id": department["cluster_id"], "level": 2,
        "label": "Mixed topic parent", "medoid_course_id": department["medoid_course_id"],
        "medoid_code": department["medoid_code"], "medoid_title": department["medoid_title"],
        "centroid": _MICRO, "size": 9, "kind": "topic", "department_key": "ECON",
    })
    db.execute("UPDATE sim_clusters SET parent_cluster_id=%s, level=3 WHERE parent_cluster_id=%s"
               " AND cluster_id<>%s", (barrier, department["cluster_id"], barrier))
    recs = _recommend(db, collection)
    assert len(recs[0].courses) == 7
    assert recs[0].path[:2] == ("Economics", "Mixed topic parent")


def test_no_current_build_recommends_nothing(db, collection):
    assert _recommend(db, collection) == []


def _build_counts(db, build_id):
    clusters = db.execute("SELECT count(*) AS n FROM sim_clusters WHERE build_id = %(b)s",
                          {"b": build_id}).fetchone()["n"]
    members = db.execute(
        "SELECT count(*) AS n FROM sim_cluster_members m"
        " JOIN sim_clusters c ON c.cluster_id = m.cluster_id WHERE c.build_id = %(b)s",
        {"b": build_id},
    ).fetchone()["n"]
    return clusters, members


def test_deleting_a_live_course_does_not_cascade_into_the_saved_build(db, collection, tmp_path):
    # DA-06: removing the root representative (medoid) must not erase the build.
    _import(db, collection, tmp_path)
    result = _build(db, collection)
    before = _build_counts(db, result.build_id)
    db.execute(
        "DELETE FROM courses WHERE course_id = ("
        " SELECT medoid_course_id FROM sim_clusters"
        " WHERE build_id = %(b)s AND parent_cluster_id IS NULL)",
        {"b": result.build_id},
    )
    db.commit()
    assert _build_counts(db, result.build_id) == before


def test_changed_catalog_retires_the_topic_build_and_keeps_history(db, collection, tmp_path):
    _import(db, collection, tmp_path)
    result = _build(db, collection)
    clusters_before, members_before = _build_counts(db, result.build_id)
    assert clusters_before > 0 and members_before > 0

    _import(db, collection, tmp_path, codes=_CODES[:5], allow_course_removal=True)

    assert db.execute(queries.get("list_current_clusters"), {
        "collection_id": collection,
    }).fetchall() == []
    assert _recommend(db, collection) == []
    # The retired build keeps its rows (and the snapshotted courses).
    assert _build_counts(db, result.build_id) == (clusters_before, members_before)


def test_topic_retirement_commits_with_publication_before_post_publish_failure(
    db, collection, tmp_path, monkeypatch,
):
    _import(db, collection, tmp_path)
    result = _build(db, collection)
    before = _build_counts(db, result.build_id)
    assert before[0] > 0 and before[1] > 0

    def fail_after_publication(*args, **kwargs):
        raise RuntimeError("injected post-publication parser refresh failure")

    monkeypatch.setattr(atlas_importer, "refresh_atlas_requisites", fail_after_publication)
    with pytest.raises(RuntimeError, match="post-publication"):
        _import(db, collection, tmp_path, codes=_CODES[:5], allow_course_removal=True)

    assert db.execute(queries.get("list_current_clusters"), {
        "collection_id": collection,
    }).fetchall() == []
    assert _recommend(db, collection) == []
    assert _build_counts(db, result.build_id) == before
