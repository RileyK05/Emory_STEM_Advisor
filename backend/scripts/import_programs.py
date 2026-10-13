"""Import parsed program requirements into a collection.

Usage (from backend/):
    python -m scripts.import_programs --collection NAME [FILE]

FILE defaults to DATA_DIR/programs/programs.jsonl, written by
`scripts.scrape_programs`. Re-running replaces the collection's programs.
Course mentions link to courses imported by `scripts.import_atlas`; importing
the atlas afterwards re-links them.
"""

import argparse
from pathlib import Path
from uuid import uuid4

from app.config import load_settings
from app.db import queries
from app.programs.importer import import_programs
from scripts._db import open_connection


def _collection_id(conn, name: str):
    row = conn.execute(queries.get("get_collection_by_name"), {"name": name}).fetchone()
    if row is not None:
        return row["collection_id"]
    collection_id = uuid4()
    conn.execute(queries.get("insert_collection"),
                 {"collection_id": collection_id, "name": name})
    conn.commit()
    return collection_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("file", type=Path, nargs="?", default=None)
    args = parser.parse_args()

    settings = load_settings()
    path = args.file or settings.data_dir / "programs" / "programs.jsonl"
    with open_connection(settings, args.database_url) as conn:
        counts = import_programs(conn, collection_id=_collection_id(conn, args.collection),
                                 path=path)
    print(f"programs: {counts['programs']} (bound {counts['bound']}, unresolved "
          f"{counts['evidence_unresolved']}, unbound {counts['unbound']}), "
          f"course mentions: {counts['mentions']} "
          f"({counts['resolved']} linked to imported courses)")


if __name__ == "__main__":
    main()
