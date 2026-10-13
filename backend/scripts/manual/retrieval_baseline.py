"""Manual-only retrieval baseline against isolated official-summary fixtures.

Run from backend with the normal dev environment. This creates a temporary
embedded pgserver and temporary DATA_DIR, uses the configured embedder factory,
and requires cached Granite weights (HF_HUB_OFFLINE is enabled). It never
writes to the project corpus or runs in pytest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from dataclasses import asdict, replace
from pathlib import Path

from app.util.digest import file_sha256

sha256 = file_sha256


REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE = REPO_ROOT / "backend" / "tests" / "fixtures" / "retrieval_eval_2026_10_08"
CASES = FIXTURE / "cases.jsonl"


def source_documents_sha256() -> str:
    digest = hashlib.sha256()
    for path in sorted(FIXTURE.glob("*.md")):
        if path.name == "README.md":
            continue
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--scratch-parent", type=Path, default=None)
    args = parser.parse_args()

    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"

    from app.config import load_settings
    from app.db import queries
    from app.db.connection import open_connection
    from app.embeddings import get_embedder
    from app.ingest.pipeline import ingest_file
    from app.retrieval.config import load_retrieval_config
    from app.retrieval.evals import EvalCase, run_eval
    from app.retrieval.retrieve import retrieve
    from app.retrieval.types import Retrieved

    records = [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines()]
    positives = [r for r in records if r["expected"]]
    negative = [r for r in records if not r["expected"]]

    scratch_parent = args.scratch_parent
    if scratch_parent is not None:
        scratch_parent = scratch_parent.resolve()
        if not scratch_parent.is_relative_to(REPO_ROOT):
            raise ValueError("--scratch-parent must be inside the repository")
        scratch_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="emory-retrieval-eval-", dir=scratch_parent) as temp:
        isolated_data = Path(temp) / "data"
        os.environ["DATA_DIR"] = str(isolated_data)
        os.environ["PGSERVER_DATA"] = str(isolated_data / "pgserver")
        settings = replace(load_settings(), data_dir=isolated_data, database_url=None,
                           embedding_device="cpu")
        embedder = get_embedder(settings)
        config = load_retrieval_config(settings=settings)
        with open_connection(settings) as conn:
            source_dir = FIXTURE
            all_sources = sorted(p for p in source_dir.glob("*.md") if p.name != "README.md")
            coll_name = "emory_eval_2026_10_08"
            conn.execute(
                queries.get("insert_collection"),
                {"collection_id": __import__("uuid").uuid4(), "name": coll_name},
            )
            conn.commit()
            row = conn.execute(queries.get("get_collection_by_name"),
                               {"name": coll_name}).fetchone()
            coll_id = row["collection_id"]
            source_ids = {}
            for source in all_sources:
                source_ids[source.name] = ingest_file(
                    conn,
                    collection_id=coll_id,
                    path=source,
                    content_type="text/markdown",
                    count_tokens=embedder.count_tokens,
                    embedder=embedder,
                    embed_model=settings.embedding_model_id,
                    config=config,
                )
                conn.commit()

            selected = [r for r in positives if r.get("scope_sources")]
            recall_cases = [r for r in positives if not r.get("scope_sources")]
            cases = [
                EvalCase(r["id"], r["collection"], r["query"],
                         tuple((x["filename"], x["locator_label"]) for x in r["expected"]))
                for r in recall_cases
            ]
            summary = run_eval(conn, cases, embedder=embedder,
                               embed_model=settings.embedding_model_id, config=config)
            selected_scope_results = []
            for record in selected:
                allowed = [source_ids[name] for name in record["scope_sources"]]
                outcome = retrieve(conn, collection_id=coll_id, query=record["query"],
                                   embedder=embedder, embed_model=settings.embedding_model_id,
                                   config=config, source_ids=allowed)
                expected = {(x["filename"], x["locator_label"]) for x in record["expected"]}
                found = {(chunk.source_name, chunk.locator_label)
                         for chunk in outcome.chunks} if isinstance(outcome, Retrieved) else set()
                selected_scope_results.append({
                    "id": record["id"], "allowed_source_count": len(allowed),
                    "returned_chunks": len(found), "expected_found": sorted(found & expected),
                    "expected": sorted(expected), "recall": len(found & expected) / len(expected),
                })
            empty_observations = []
            for record in negative:
                scoped_ids = [] if record["case_type"] == "empty_scope" else None
                outcome = retrieve(conn, collection_id=coll_id, query=record["query"],
                                   embedder=embedder, embed_model=settings.embedding_model_id,
                                   config=config, source_ids=scoped_ids)
                count = len(outcome.chunks) if isinstance(outcome, Retrieved) else 0
                empty_observations.append({
                    "id": record["id"], "case_type": record["case_type"],
                    "returned_chunks": count, "refused": not isinstance(outcome, Retrieved),
                })

    result = {
        "status": "manual_local_run",
        "fixture_sha256": sha256(CASES),
        "source_manifest_sha256": sha256(FIXTURE / "sources.json"),
        "source_documents_sha256": source_documents_sha256(),
        "retrieval_config_sha256": sha256(REPO_ROOT / "backend" / "configs" / "retrieval.toml"),
        "positive_case_count": len(positives),
        "negative_case_count": len(negative),
        "selected_scope_cases": selected_scope_results,
        "positive_recall_summary": asdict(summary),
        "empty_scope_observations": empty_observations,
        "constraints": [
            "Uses cached Granite through app.embeddings factory; no network model download.",
            "Runs against a newly created temporary embedded Postgres database and temporary DATA_DIR.",
            "Does not mutate project data, official course corpus, retrieval settings, or prerequisite graph.",
            "Expected evidence labels are pending human review; recall is not answer correctness.",
            "Empty/unrelated cases report retrieval presence only; they do not establish safe abstention.",
            "Source notes are concise manual paraphrases and require current source verification.",
        ],
    }
    rendered = json.dumps(result, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
