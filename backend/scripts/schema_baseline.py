"""Check the consolidated schema against its immutable legacy history, without models.

Run from backend/: python -m scripts.schema_baseline
Use --write only while establishing a new, unapplied baseline checkpoint.
"""

import argparse
import tempfile
from pathlib import Path

from app.config import load_settings
from app.db import migrate, queries
from app.db.connection import connect
from app.ops.backup import _pg_bin, _pg_env, _run


def export_schema(dsn: str) -> str:
    dumped = _run([
        str(_pg_bin("pg_dump")), "--schema-only", "--no-owner", "--no-acl",
        "--schema=public", "--exclude-table=public.schema_migrations",
    ], _pg_env(dsn))
    lines = []
    for line in dumped.splitlines():
        if (line.startswith(("\\restrict ", "\\unrestrict ", "SET ",
                             "SELECT pg_catalog.set_config('search_path'", "-- Dumped "))
                or line in {"CREATE SCHEMA public;", "COMMENT ON SCHEMA public IS 'standard public schema';"}
                ):
            continue
        lines.append(line)
    header = (
        "-- Consolidated schema checkpoint 032, generated from immutable legacy migrations.\n"
        "-- Fresh installs apply this file once; existing databases retain their legacy history.\n"
        "-- Future schema changes append migration 033 and higher.\n"
        "CREATE EXTENSION IF NOT EXISTS vector;\n"
    )
    return header + "\n".join(lines).strip() + "\n"


def catalog(conn) -> list[dict]:
    return conn.execute(queries.get("schema_catalog")).fetchall()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    import pgserver

    root = (load_settings().data_dir / "schema-checks").resolve()
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="baseline-", dir=root) as temp_name:
        temp = Path(temp_name).resolve()
        if not temp.is_relative_to(root):
            raise RuntimeError("baseline scratch directory escaped its configured root")
        server = pgserver.get_server(temp / "pgserver", cleanup_mode="stop")
        legacy = baseline = None
        try:
            legacy = connect(server.get_uri())
            migrate.apply_legacy_migrations(legacy)
            legacy.commit()
            candidate = export_schema(server.get_uri())
            if args.write:
                if migrate._BASELINE_PATH.exists():
                    raise RuntimeError("baseline already exists; an applied baseline must remain immutable")
                migrate._BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
                migrate._BASELINE_PATH.write_text(candidate, encoding="utf-8", newline="\n")
            if not migrate._BASELINE_PATH.is_file():
                raise RuntimeError("baseline is missing; establish it once with --write")
            # A second database on this disposable server validates the actual
            # fresh-install path and all final tables, indexes, checks and triggers.
            legacy.autocommit = True
            legacy.execute(queries.get("schema_create_baseline_check_database"))
            legacy.autocommit = False
            baseline = connect(server.get_uri(database="baseline_check"))
            migrate.apply_migrations(baseline)
            baseline.commit()
            if catalog(legacy) != catalog(baseline):
                raise RuntimeError("consolidated schema differs from the legacy upgrade path")
            print("Baseline and legacy schema catalogs match; no models or live provider calls.")
        finally:
            if baseline is not None:
                baseline.close()
            if legacy is not None:
                legacy.close()
            server.cleanup()


if __name__ == "__main__":
    main()
