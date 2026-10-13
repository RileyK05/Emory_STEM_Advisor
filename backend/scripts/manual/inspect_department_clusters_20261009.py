"""Inspect stored course vectors within fixed subjects; no models or database writes."""

from collections import Counter
from pathlib import Path

import numpy as np

from app.db import queries
from app.db.connection import connect
from app.simgraph.build import _normalize, _to_array, cluster_tree


def main():
    import pgserver

    target = Path(__file__).resolve().parents[3] / "data/schema-consolidated-20261009-verified"
    server = pgserver.get_server(target / "pgserver", cleanup_mode="stop")
    conn = connect(server.get_uri())
    try:
        collection = conn.execute(queries.get("list_collections")).fetchone()
        build = conn.execute(queries.get("get_current_build"), {
            "collection_id": collection["collection_id"],
        }).fetchone()
        rows = conn.execute(queries.get("list_courses_with_vectors"), {
            "collection_id": collection["collection_id"], "model": build["model"],
        }).fetchall()
        subjects = sorted({r["subject"] for r in rows})
        print("Fixed subject boundaries:", ", ".join(subjects))
        for cuts in [(0.2, 0.15, 0.1), (0.18, 0.12, 0.08)]:
            print("Cuts:", cuts)
            for subject in ["ECON", "MATH", "QTM", "DSCI", "CS"]:
                sub = [r for r in rows if r["subject"] == subject]
                if len(sub) < 2:
                    continue
                matrix = np.vstack([_normalize(_to_array(r["mean_vector"])) for r in sub])
                specs = cluster_tree(matrix, cuts=cuts, method="average")
                print(subject, "courses", len(sub), "clusters by level",
                      dict(Counter(s.level for s in specs)))
                for spec in [s for s in specs if s.parent == 0][:8]:
                    vectors = matrix[list(spec.members)]
                    medoid = spec.members[int(np.argmax(vectors @ vectors.sum(axis=0)))]
                    print(" ", len(spec.members), sub[medoid]["title"],
                          [sub[i]["code"] for i in spec.members][:15])
    finally:
        conn.close()
        server.cleanup()


if __name__ == "__main__":
    main()
