"""Apply the release schema before admitting traffic to a read-only ready probe."""

import argparse

from app.config import load_settings
from app.db.bootstrap import prepare_database
from app.db.connection import open_connection
from app.db.migrate import apply_migrations


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema-only", action="store_true",
                        help="apply migrations without creating the default collection")
    args = parser.parse_args()
    settings = load_settings()
    with open_connection(settings) as conn:
        if args.schema_only:
            apply_migrations(conn)
            conn.commit()
        else:
            prepare_database(conn, settings.default_collection)
    print("Release migrations verified and applied; no model or provider calls were made.")


if __name__ == "__main__":
    main()
