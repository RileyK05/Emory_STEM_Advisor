"""Create or restore a persistent database + original-file backup.

From backend/: ``python -m scripts.backup create --backup PATH --maintenance-confirmed``
or ``python -m scripts.backup restore --backup PATH --database-url URL --data-dir PATH``.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from pathlib import Path

from app.config import load_settings
from app.db.connection import connect
from app.ops.backup import BackupError, create_backup, restore_backup


@contextmanager
def _backup_connection(settings, database_url: str | None):
    dsn = database_url or settings.database_url
    if dsn:
        conn = connect(dsn)
        try:
            yield conn, dsn
        finally:
            conn.close()
        return
    import pgserver

    pgdata = settings.data_dir / "pgserver"
    pgdata.mkdir(parents=True, exist_ok=True)
    server = pgserver.get_server(pgdata, cleanup_mode="stop")
    try:
        dsn = server.get_uri()
        conn = connect(dsn)
        try:
            from app.db.migrate import apply_migrations

            apply_migrations(conn)
            conn.commit()
            yield conn, dsn
        finally:
            conn.close()
    finally:
        server.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser(description="Back up or restore persistent application data")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create", help="create a consistent backup archive")
    create.add_argument("--backup", type=Path, required=True)
    create.add_argument("--database-url")
    create.add_argument("--data-dir", type=Path)
    create.add_argument(
        "--maintenance-confirmed", action="store_true",
        help="confirm writes and workers have been quiesced for this backup",
    )
    restore = subparsers.add_parser("restore", help="restore into an empty database and new data directory")
    restore.add_argument("--backup", type=Path, required=True)
    restore.add_argument("--database-url", required=True)
    restore.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    settings = load_settings()
    data_dir = (args.data_dir or settings.data_dir).resolve()

    try:
        if args.command == "create":
            if not args.maintenance_confirmed:
                raise BackupError("create requires --maintenance-confirmed after stopping writers and workers")
            with _backup_connection(settings, args.database_url) as (conn, dsn):
                manifest = create_backup(
                    conn, dsn=dsn, data_dir=data_dir, destination=args.backup,
                    maintenance_confirmed=True,
                )
            print(
                f"Backup created: {args.backup.resolve()} "
                f"({len(manifest['files'])} referenced files; "
                f"PostgreSQL {manifest['postgres_server_version_num']})"
            )
        else:
            conn = connect(args.database_url)
            try:
                manifest = restore_backup(
                    conn, dsn=args.database_url, archive_path=args.backup,
                    data_dir=data_dir,
                )
            finally:
                conn.close()
            print(
                f"Restore completed into fresh target: "
                f"{len(manifest['files'])} referenced files restored"
            )
    except BackupError as exc:
        parser.exit(2, f"backup error: {exc}\n")


if __name__ == "__main__":
    main()
