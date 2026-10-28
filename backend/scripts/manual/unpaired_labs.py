"""List labs whose same-number lecture exists but no stated corequisite pairs them.

Read-only. A lab pairs with its lecture only where the parsed requisite text names
one as the other's corequisite; everything printed here needs a parser fix or a
curator decision, never an inferred pairing. Run from backend with
``python -m scripts.manual.unpaired_labs --collection NAME``.
"""

from __future__ import annotations

import argparse

from app.config import load_settings
from app.db import queries
from scripts._db import open_connection


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", default="default")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()
    with open_connection(load_settings(), args.database_url) as conn:
        collection = conn.execute(queries.get("get_collection_by_name"), {
            "name": args.collection,
        }).fetchone()
        if collection is None:
            parser.error(f"no collection named {args.collection!r}")
        rows = conn.execute(queries.get("list_unpaired_labs"), {
            "collection_id": collection["collection_id"],
        }).fetchall()
    for row in rows:
        print(f"{row['lab_code']} (lecture {row['lecture_code']}): {row['requisites_raw'] or '(none)'}")
    print(f"{len(rows)} unpaired labs")


if __name__ == "__main__":
    main()
