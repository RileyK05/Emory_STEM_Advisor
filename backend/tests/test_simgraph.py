"""Similarity graph: clustered hierarchy, nudges, edges, subtree walk."""

import dataclasses

import numpy as np
import pytest

import app.simgraph.build as build_mod
from app.atlas.importer import import_atlas
from app.db import queries
from app.retrieval.config import ConfigError, load_retrieval_config
from app.simgraph.build import build_simgraph, cluster_tree
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
    return cluster_tree(_MATRIX, cuts=config.level_cut_distances, method=config.linkage, **kwargs)


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


def test_apart_nudge_splits_a_close_pair():
    matrix = np.vstack([_unit(1.0), _unit(1.0), _unit(0, 1.0)])
    plain = cluster_tree(matrix, cuts=(1.4, 0.5), method="average")
    assert _shared_levels(plain, 0, 1) - {0}
    nudged = cluster_tree(matrix, cuts=(1.4, 0.5), method="average", apart=[(0, 1)])
    assert _shared_levels(nudged, 0, 1) == {0}


# --- nudges file ----------------------------------------------------------


def test_missing_nudges_file_means_no_nudges(tmp_path):
    assert load_nudges(tmp_path / "absent.toml") == Nudges()


def test_shipped_nudges_file_parses():
    assert load_nudges().together == ()


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
        text.replace("level_cut_distances = [0.7, 0.5, 0.35]", "level_cut_distances = [0.5, 0.7]"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="decreasing"):
        load_retrieval_config(bad)
    bad.write_text(text.replace('linkage = "average"', 'linkage = "ward"'), encoding="utf-8")
    with pytest.raises(ConfigError, match="linkage"):
        load_retrieval_config(bad)


# --- database -------------------------------------------------------------


def _import(db, collection, tmp_path):
    lines = [
        f'{{"code": "{code}", "title": "Title {code}", "description": "about {code}"}}'
        for code in _CODES
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


@pytest.mark.db
def test_edges_keep_every_pair_above_floor(db, collection, tmp_path):
    _import(db, collection, tmp_path)
    simgraph = dataclasses.replace(_config(), edge_floor=0.5, block_size=4)
    result = _build(db, collection, simgraph)
    stored = {
        frozenset((row["course_a"], row["course_b"]))
        for row in db.execute(
            queries.get("list_current_edges"), {"collection_id": collection}
        ).fetchall()
    }
    ids = {
        row["code"]: row["course_id"]
        for row in db.execute(
            "SELECT code, course_id FROM courses WHERE collection_id = %(c)s",
            {"c": collection},
        ).fetchall()
    }
    similarity = _MATRIX @ _MATRIX.T
    expected = {
        frozenset((ids[_CODES[i]], ids[_CODES[j]]))
        for i in range(len(_CODES))
        for j in range(i + 1, len(_CODES))
        if similarity[i, j] >= 0.5
    }
    assert stored == expected and result.edges == len(expected)
    degree = max(sum(1 for pair in stored if ids[code] in pair) for code in _CODES)
    assert degree > load_retrieval_config().fusion.cited_set_size


@pytest.mark.db
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
    assert recs[0].scores == sorted(recs[0].scores, reverse=True)
    assert len(recs[0].path) == 2  # econ, then down into macro


@pytest.mark.db
def test_walk_stops_above_children_smaller_than_min_subtree(db, collection, tmp_path):
    _import(db, collection, tmp_path)
    _build(db, collection)
    recs = _recommend(db, collection, query=_MICRO)
    # The micro cluster has 2 courses (< walk_min_subtree), so the walk stays
    # at economics and returns all of it.
    codes = {code for _, code, _ in recs[0].courses}
    assert codes == {c for c in _CODES if c.startswith("ECON")}
    assert len(recs[0].path) == 1


@pytest.mark.db
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


@pytest.mark.db
def test_failed_build_keeps_previous_recommendations(db, collection, tmp_path, monkeypatch):
    _import(db, collection, tmp_path)
    _build(db, collection)
    db.commit()
    before = _recommend(db, collection)

    def boom(*args, **kwargs):
        raise RuntimeError("build exploded")

    monkeypatch.setattr(build_mod, "_write_edges", boom)
    with pytest.raises(RuntimeError, match="build exploded"):
        _build(db, collection)
    db.rollback()

    after = _recommend(db, collection)
    assert [(r.path, r.courses) for r in after] == [(r.path, r.courses) for r in before]
    builds = db.execute(
        "SELECT COUNT(*) AS n FROM sim_builds WHERE collection_id = %(c)s", {"c": collection}
    ).fetchone()
    assert builds["n"] == 1


@pytest.mark.db
def test_nudges_apply_and_report(db, collection, tmp_path):
    _import(db, collection, tmp_path)
    nudges = Nudges(
        together=(("MATH 111", "CS 170"),),
        apart=(("ECON 101", "ECON 201"),),
        labels=(
            LabelNudge(1, "ECON 101", "Economics"),
            LabelNudge(1, "ECON 330", "Also economics"),
            LabelNudge(1, "HIST 100", "History"),
        ),
        file_hash="abc",
    )
    result = _build(db, collection, nudges=nudges)
    clusters = _clusters(db, result.build_id)
    assert any(label == "Economics" for _, label, _ in clusters)
    assert all(("MATH 111" in codes) == ("CS 170" in codes) for _, _, codes in clusters)
    joined = "\n".join(result.warnings)
    assert "HIST 100 is not in the graph" in joined
    assert "name the same cluster" in joined
    # Two courses inside a tight group of seven cannot be pushed apart; the
    # build says so instead of failing silently.
    assert "ECON 101 / ECON 201 still share a cluster" in joined
    build = db.execute(queries.get("get_current_build"), {"collection_id": collection}).fetchone()
    assert build["nudges_hash"] == "abc"


@pytest.mark.db
def test_subgraph_view_is_one_cluster_and_its_edges(db, collection, tmp_path):
    _import(db, collection, tmp_path)
    _build(db, collection, dataclasses.replace(_config(), edge_floor=0.0))
    recs = _recommend(db, collection)
    nodes, edges = subgraph_view(db, cluster_id=recs[0].cluster_id)
    ids = {node["course_id"] for node in nodes}
    assert {node["code"] for node in nodes} == {code for _, code, _ in recs[0].courses}
    assert len(edges) == len(ids) * (len(ids) - 1) // 2
    assert all(e["course_a"] in ids and e["course_b"] in ids for e in edges)


@pytest.mark.db
def test_no_current_build_recommends_nothing(db, collection):
    assert _recommend(db, collection) == []
