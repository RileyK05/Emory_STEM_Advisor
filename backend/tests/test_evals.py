"""Eval runner semantics: exact labels, unresolved excluded. Real Postgres."""



from app.retrieval.config import load_retrieval_config
from app.retrieval.evals import EvalCase, run_eval
from tests.fakes import FakeEmbedder
from tests.helpers import ingest_text

_MODEL = "fake-model"


def test_exact_label_and_unresolved(db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "doc.txt", "alpha beta gamma\n")
    name = db.execute(
        "SELECT name FROM collections WHERE collection_id = %(c)s",
        {"c": collection},
    ).fetchone()["name"]

    cases = [
        EvalCase("hit", name, "alpha", (("doc.txt", "lines 1-2"),)),
        EvalCase("miss", name, "alpha", (("doc.txt", "page 12"),)),
        EvalCase("gone", "no-such-collection", "alpha", (("doc.txt", "lines 1-2"),)),
    ]
    summary = run_eval(
        db,
        cases,
        embedder=FakeEmbedder(dim=8),
        embed_model=_MODEL,
        config=load_retrieval_config(),
    )
    assert summary.total == 3
    assert summary.resolved == 1
    assert "gone" in summary.unresolved
    # "page 12" does not exist, so it is unresolved, not a miss.
    assert "miss" in summary.unresolved


def test_page_1_does_not_match_page_12(db, collection, tmp_path):
    path = tmp_path / "doc.txt"
    path.write_text("alpha beta gamma\n", encoding="utf-8")
    ingest_text(db, collection, tmp_path, "doc.txt", "alpha beta gamma\n")
    name = db.execute(
        "SELECT name FROM collections WHERE collection_id = %(c)s",
        {"c": collection},
    ).fetchone()["name"]
    cases = [EvalCase("x", name, "alpha", (("doc.txt", "page 1"),))]
    summary = run_eval(
        db,
        cases,
        embedder=FakeEmbedder(dim=8),
        embed_model=_MODEL,
        config=load_retrieval_config(),
    )
    assert "x" in summary.unresolved


def test_negative_case_is_scored_separately_from_recall(db, collection, tmp_path):
    from dataclasses import replace

    ingest_text(db, collection, tmp_path, "doc.txt", "alpha beta gamma\n")
    name = db.execute(
        "SELECT name FROM collections WHERE collection_id = %(c)s",
        {"c": collection},
    ).fetchone()["name"]
    base = load_retrieval_config()
    keyword_only = replace(
        base,
        seams=replace(base.seams, embed=replace(base.seams.embed, enabled=False)),
    )
    cases = [
        EvalCase("hit", name, "alpha", (("doc.txt", "lines 1-2"),)),
        EvalCase("unrelated", name, "zzz unrelated query", (), case_type="unrelated_negative"),
    ]
    summary = run_eval(
        db,
        cases,
        embedder=FakeEmbedder(dim=8),
        embed_model=_MODEL,
        config=keyword_only,
    )
    assert summary.positive_resolved == 1
    assert summary.negative_resolved == 1
    # The negative case is excluded from the recall denominator, so the
    # positive case still scores a perfect recall.
    assert summary.fused_recall == 1.0
    assert summary.negative_refusals == 1
    assert summary.negative_false_positives == 0
    assert summary.negative_irrelevant_chunks == 0


def test_load_cases_marks_empty_expectation_as_negative(tmp_path):
    import json

    from app.retrieval.evals import load_cases

    path = tmp_path / "cases.jsonl"
    path.write_text(
        "\n".join(
            json.dumps({
                "id": case_id,
                "collection": "c",
                "query": "q",
                "expected": expected,
                "case_type": case_type,
            })
            for case_id, expected, case_type in [
                ("pos", [{"filename": "a.md", "locator_label": "X"}], "positive"),
                ("neg", [], "unrelated_negative"),
            ]
        ),
        encoding="utf-8",
    )
    cases = {case.id: case for case in load_cases(path)}
    assert cases["pos"].negative is False
    assert cases["neg"].negative is True
    assert cases["neg"].case_type == "unrelated_negative"
