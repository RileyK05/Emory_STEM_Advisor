"""One-shot: ingest all program sources, then bind programs.jsonl.

Majors/minors only become citable after both steps. Running `import_programs`
alone leaves every program `unbound`, so advising research cannot ground
degree-requirement answers.

Usage (from backend/):

    python -m scripts.bootstrap_programs --collection default

Optional:

    python -m scripts.bootstrap_programs --collection default --skip-ingest
"""

from __future__ import annotations

import argparse
import subprocess
import sys
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
    parser.add_argument("--programs-root", type=Path, default=None,
                        help="Defaults to <repo>/data/programs")
    parser.add_argument("--skip-ingest", action="store_true")
    args = parser.parse_args()

    settings = load_settings()
    root = args.programs_root or (Path(__file__).resolve().parents[2] / "data" / "programs")
    manifest = root / "ingest-manifest.json"
    programs_jsonl = root / "programs.jsonl"
    for path in (manifest, programs_jsonl):
        if not path.is_file():
            raise SystemExit(f"missing {path}")

    files = sorted(
        p for sub in ("catalog", "departments")
        for p in (root / sub).glob("*")
        if p.is_file()
    )
    if not args.skip_ingest:
        if not files:
            raise SystemExit(f"no files under {root}/catalog or {root}/departments")
        cmd = [
            sys.executable, "-m", "scripts.ingest",
            "--collection", args.collection,
            "--origin-manifest", str(manifest),
            *[str(p) for p in files],
        ]
        if args.database_url:
            cmd[3:3] = ["--database-url", args.database_url]
        print("bootstrap_programs: ingesting", len(files), "files…", flush=True)
        subprocess.run(cmd, check=True)

    with open_connection(settings, args.database_url) as conn:
        counts = import_programs(
            conn, collection_id=_collection_id(conn, args.collection), path=programs_jsonl,
        )
    print(
        "bootstrap_programs: "
        f"{counts['programs']} programs "
        f"(bound {counts['bound']}, unresolved {counts['evidence_unresolved']}, "
        f"unbound {counts['unbound']}); "
        f"mentions {counts['mentions']} ({counts['resolved']} linked)"
    )
    if counts["unbound"]:
        print(
            f"warning: {counts['unbound']} programs remain unbound "
            "(missing source file or manifest SHA mismatch).",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
