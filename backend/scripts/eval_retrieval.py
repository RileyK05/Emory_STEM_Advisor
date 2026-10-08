"""Run the frozen retrieval eval set and print a summary.

Usage (from backend/):
    python -m scripts.eval_retrieval [--cases data/eval/retrieval.jsonl]
"""

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from app.config import load_settings
from app.embeddings import get_embedder
from app.retrieval.config import load_retrieval_config
from app.retrieval.evals import load_cases, run_eval
from scripts._db import open_connection

REPO_ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases", type=Path, default=REPO_ROOT / "data" / "eval" / "retrieval.jsonl"
    )
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()

    settings = load_settings()
    config = load_retrieval_config(settings=settings)
    embedder = get_embedder(settings)
    cases = load_cases(args.cases)

    with open_connection(settings, args.database_url) as conn:
        summary = run_eval(
            conn,
            cases,
            embedder=embedder,
            embed_model=settings.embedding_model_id,
            config=config,
        )

    print(json.dumps(asdict(summary), indent=2))
    runs = REPO_ROOT / "runs" / "retrieval"
    runs.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    payload = {"config_version": config.version, "summary": asdict(summary)}
    (runs / f"{stamp}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
