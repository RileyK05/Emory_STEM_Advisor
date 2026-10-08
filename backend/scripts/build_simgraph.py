"""Build the similarity graph (course clusters + edges) for a collection.

Usage (from backend/):
    python -m scripts.build_simgraph --collection NAME [--nudges FILE]

Run scripts.import_atlas first. Nudges default to configs/simgraph_nudges.toml.
"""

import argparse
from pathlib import Path

from app.config import load_settings
from app.db import queries
from app.retrieval.config import load_retrieval_config
from app.simgraph.build import build_simgraph
from app.simgraph.nudges import load_nudges
from scripts._db import open_connection


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--nudges", type=Path, default=None)
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()

    settings = load_settings()
    config = load_retrieval_config(settings=settings)
    nudges = load_nudges(args.nudges)
    with open_connection(settings, args.database_url) as conn:
        row = conn.execute(
            queries.get("get_collection_by_name"), {"name": args.collection}
        ).fetchone()
        if row is None:
            raise SystemExit(f"unknown collection {args.collection!r}")
        result = build_simgraph(
            conn,
            collection_id=row["collection_id"],
            model=settings.embedding_model_id,
            config_version=config.version,
            simgraph=config.simgraph,
            nudges=nudges,
        )
        conn.commit()
    print(
        f"built simgraph {result.build_id}: {result.courses} courses, "
        f"{result.edges} edges, {result.clusters} clusters"
    )
    for warning in result.warnings:
        print(f"warning: {warning}")


if __name__ == "__main__":
    main()
