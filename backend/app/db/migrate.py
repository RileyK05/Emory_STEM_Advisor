"""Migration runner. Files are append-only; applied files are never edited."""

import hashlib
import re
from pathlib import Path

from pgvector.psycopg import register_vector
from psycopg import Connection

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_FILE_RE = re.compile(r"^(\d+)_(.+)\.sql$")


class MigrationError(RuntimeError):
    """A malformed migration set (bad filename, gap, or duplicate number)."""


class MigrationChanged(MigrationError):
    """An already-applied migration's contents changed on disk."""


def _discover() -> list[tuple[int, str, Path]]:
    found: list[tuple[int, str, Path]] = []
    seen: dict[int, str] = {}
    for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        match = _FILE_RE.match(path.name)
        if not match:
            raise MigrationError(f"malformed migration filename: {path.name}")
        version = int(match.group(1))
        if version in seen:
            raise MigrationError(f"duplicate migration number {version}")
        seen[version] = path.name
        found.append((version, path.name, path))
    found.sort(key=lambda item: item[0])
    for expected, (version, _, _) in enumerate(found, start=1):
        if version != expected:
            raise MigrationError(
                f"migration gap: expected {expected:03d}, found {version:03d}"
            )
    return found


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ensure_table(conn: Connection) -> None:
    with conn.transaction():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    int PRIMARY KEY,
                name       text NOT NULL,
                sha256     text NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )


def _applied(conn: Connection) -> dict[int, str]:
    rows = conn.execute("SELECT version, sha256 FROM schema_migrations").fetchall()
    return {row["version"]: row["sha256"] for row in rows}


def apply_migrations(conn: Connection) -> list[str]:
    """Apply pending migrations in order. Returns the names applied this call."""
    migrations = _discover()
    _ensure_table(conn)
    applied = _applied(conn)

    applied_now: list[str] = []
    for version, name, path in migrations:
        digest = _sha256(path)
        if version in applied:
            if applied[version] != digest:
                raise MigrationChanged(
                    f"migration {name} changed after it was applied"
                )
            continue
        with conn.transaction():
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations (version, name, sha256)"
                " VALUES (%(version)s, %(name)s, %(sha)s)",
                {"version": version, "name": name, "sha": digest},
            )
        applied_now.append(name)

    if applied_now:
        # The extension may have just been created; register the vector adapter
        # so this same connection can bind vector values afterwards.
        register_vector(conn)
    return applied_now
