"""Check retrieval on every program and a sample of courses, not on hand-picked questions.

The retrieval eval set is small and gets tuned against. This asks a plain question
about each current program and sampled undergraduate course and reports whether the
thing asked about comes back. Read-only; no model is called.

From backend/, with the database running:
    python -m scripts.manual.retrieval_census --courses 300
"""

import argparse
import random
import re
import time
from collections import Counter
from dataclasses import replace

from app.config import load_settings
from app.db import queries
from app.embeddings import get_embedder
from app.retrieval.config import load_retrieval_config
from app.retrieval.retrieve import retrieve
from scripts._db import open_connection


def _clean(title: str) -> str:
    return re.sub(r"\s*\(.*$", "", title).strip().lower()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", default="default")
    parser.add_argument("--courses", type=int, default=300)
    parser.add_argument("--seed", type=int, default=20261106)
    parser.add_argument("--show", type=int, default=25)
    args = parser.parse_args()
    settings = load_settings()
    config = load_retrieval_config(settings=settings)
    wide = replace(config, fusion=replace(config.fusion, cited_set_size=24))
    embedder = get_embedder(settings)
    with open_connection(settings) as conn:
        collection = conn.execute(queries.get("get_collection_by_name"),
                                  {"name": args.collection}).fetchone()["collection_id"]
        programs = conn.execute(
            "SELECT p.title, p.kind, s.filename FROM programs p JOIN sources s USING (source_id) "
            "WHERE p.collection_id = %s AND p.is_active ORDER BY p.title", (collection,)).fetchall()
        courses = conn.execute(
            "SELECT co.code, co.title FROM courses co "
            "WHERE co.collection_id = %s AND co.lifecycle = 'current' "
            "AND co.code !~ '_OX' AND co.code ~ ' [1-4][0-9][0-9]' AND co.code !~ ' 999' "
            "ORDER BY co.code", (collection,)).fetchall()
        sample = random.Random(args.seed).sample(list(courses), min(args.courses, len(courses)))

        def search(query):
            top = retrieve(conn, collection_id=collection, query=query, embedder=embedder,
                           embed_model=settings.embedding_model_id, config=wide)
            conn.commit()
            return getattr(top, "chunks", [])

        started = time.perf_counter()
        results = Counter()
        misses = []
        for row in programs:
            query = f"{_clean(row['title'])} {row['kind']} requirements"
            found = [i for i, chunk in enumerate(search(query)) if chunk.source_name == row["filename"]]
            results["program_top6" if found and found[0] < 6 else
                    "program_top24" if found else "program_missing"] += 1
            if not found or found[0] >= 6:
                misses.append((query, found[0] + 1 if found else None))
        for row in sample:
            query = row["title"].lower()
            names = [chunk.locator_label for chunk in search(query)]
            found = [i for i, label in enumerate(names) if row["code"] in label]
            results["course_top6" if found and found[0] < 6 else
                    "course_top24" if found else "course_missing"] += 1
            if not found or found[0] >= 6:
                misses.append((f"{query} [{row['code']}]", found[0] + 1 if found else None))
    print(dict(sorted(results.items())))
    print(f"{len(programs)} programs, {len(sample)} courses, "
          f"{(time.perf_counter() - started) / (len(programs) + len(sample)):.2f}s per query")
    for query, rank in misses[:args.show]:
        print(f"  rank {rank}: {query}")


if __name__ == "__main__":
    main()
