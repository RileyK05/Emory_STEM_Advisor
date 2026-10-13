"""Measure exact-course retrieval on the recorded public Emory catalog statements.

Manual check only: loads real embedding weights and starts temporary Postgres.
These direct lookup questions are a narrow locator baseline, not semantic answer
evaluation, live Course Atlas verification, or evidence of routing quality.
"""

import argparse
import hashlib
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.config import load_settings
from app.db import queries
from app.db.connection import open_connection
from app.embeddings import get_embedder
from app.ingest.pipeline import ingest_file
from app.retrieval.config import load_retrieval_config
from app.retrieval.evals import EvalCase, run_eval

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "backend/tests/fixtures/emory_prerequisites_2026_2027.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; choose a new evidence file")
    work = ROOT / "data" / "manual-checks" / ("catalog-" + uuid4().hex)
    work.mkdir(parents=True)
    os.environ["DATA_DIR"] = str(work)
    # Always isolated, never a user's development/production database.
    os.environ.pop("DATABASE_URL", None)
    os.environ["PGSERVER_DATA"] = str(work / "pgserver")
    settings = load_settings()
    config = load_retrieval_config(settings=settings)
    embedder = get_embedder(settings)
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw_records = [{"code": row["code"], "requisites": row["requisites"], "url": row["url"]}
                   for row in data["courses"]]
    cases = []
    collection_id = uuid4()
    collection = "catalog-locator-baseline"
    with open_connection(settings) as conn:
        conn.execute(queries.get("insert_collection"), {
            "collection_id": collection_id, "name": collection})
        conn.commit()
        for row in raw_records:
            name = row["code"].replace(" ", "-") + ".md"
            path = work / name
            path.write_text(
                f"# {row['code']}\n\nPrerequisite field (verbatim):\n{row['requisites']}\n\n"
                f"Catalog term: {data['term']}\nSource: {row['url']}\n", encoding="utf-8")
            ingest_file(
                conn, collection_id=collection_id, path=path, content_type="text/markdown",
                count_tokens=embedder.count_tokens, embedder=embedder,
                embed_model=settings.embedding_model_id, config=config,
            )
            cases.append(EvalCase(id=row["code"], collection=collection,
                                  query=f"What prerequisites are listed for {row['code']}?",
                                  expected=((name, row["code"]),)))
        summary = run_eval(conn, cases, embedder=embedder,
                           embed_model=settings.embedding_model_id, config=config)
    payload = {
        "check": "direct course locator retrieval, not semantic advising evaluation",
        "timestamp": datetime.now(UTC).isoformat(), "catalog_term": data["term"],
        "source_retrieved_at": data["retrieved_at"],
        "raw_records_sha256": hashlib.sha256(json.dumps(
            raw_records, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest(),
        "embedding_model_id": settings.embedding_model_id,
        "config_version": config.version,
        "config_sha256": hashlib.sha256((ROOT / "backend/configs/retrieval.toml").read_bytes()).hexdigest(),
        "summary": asdict(summary),
        "queries": [{"id": case.id, "query": case.query, "expected": case.expected} for case in cases],
        "limits": ["One template and only prerequisite statements; no varied student questions.",
                   "No generated answers, semantic support review, similarity tuning, or paid provider calls.",
                   "The public catalog snapshot is distinct from live Course Atlas offerings."],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(asdict(summary), indent=2))


if __name__ == "__main__":
    main()
