"""A consolidated fresh-install baseline with immutable legacy upgrade history."""

import hashlib
import re
from pathlib import Path

from psycopg import Connection

from app.db import queries
from app.db.connection import register_vector_adapter

_DEFAULT_MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_MIGRATIONS_DIR = _DEFAULT_MIGRATIONS_DIR
_LEGACY_MIGRATIONS_DIR = Path(__file__).parent / "legacy_migrations"
_BASELINE_VERSION = 32
_BASELINE_PATH = Path(__file__).parent / "baseline" / "032_schema.sql"
_FILE_RE = re.compile(r"^(\d+)_(.+)\.sql$")


class MigrationError(RuntimeError):
    """A malformed migration set (bad filename, gap, or duplicate number)."""


class MigrationChanged(MigrationError):
    """An already-applied migration's contents changed on disk."""


def _discover() -> list[tuple[int, str, Path]]:
    if _MIGRATIONS_DIR == _DEFAULT_MIGRATIONS_DIR:
        if not _LEGACY_MIGRATIONS_DIR.is_dir():
            raise MigrationError("legacy migration archive is missing")
        legacy = _discover_directory(_LEGACY_MIGRATIONS_DIR)
        if len(legacy) != _BASELINE_VERSION:
            raise MigrationError("legacy migration archive is incomplete")
        return legacy + _discover_directory(_MIGRATIONS_DIR, start=_BASELINE_VERSION + 1)
    return _discover_directory(_MIGRATIONS_DIR)


def _discover_directory(directory: Path, *, start: int = 1) -> list[tuple[int, str, Path]]:
    found: list[tuple[int, str, Path]] = []
    seen: dict[int, str] = {}
    for path in sorted(directory.glob("*.sql")):
        match = _FILE_RE.match(path.name)
        if not match:
            raise MigrationError(f"malformed migration filename: {path.name}")
        version = int(match.group(1))
        if version in seen:
            raise MigrationError(f"duplicate migration number {version}")
        seen[version] = path.name
        found.append((version, path.name, path))
    found.sort(key=lambda item: item[0])
    for expected, (version, _, _) in enumerate(found, start=start):
        if version != expected:
            raise MigrationError(
                f"migration gap: expected {expected:03d}, found {version:03d}"
            )
    return found


def _sha256(path: Path) -> str:
    # Hash LF-normalized bytes so a CRLF checkout (git autocrlf on Windows)
    # matches the checksum recorded from an LF checkout of the same file.
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _accepted_digests(path: Path) -> frozenset[str]:
    """Exact content digests accepted from the old raw-byte checksum runner."""
    raw = path.read_bytes()
    normalized = raw.replace(b"\r\n", b"\n")
    return frozenset(hashlib.sha256(value).hexdigest() for value in (
        raw, normalized, normalized.replace(b"\n", b"\r\n"),
    ))


def _ensure_table(conn: Connection) -> None:
    with conn.transaction():
        conn.execute(queries.get("ensure_schema_migrations"))


def fresh_manifest() -> list[tuple[int, str, Path]]:
    migrations = _discover()
    if _MIGRATIONS_DIR == _DEFAULT_MIGRATIONS_DIR:
        if not _BASELINE_PATH.is_file():
            raise MigrationError("consolidated schema baseline is missing")
        return [(_BASELINE_VERSION, _BASELINE_PATH.name, _BASELINE_PATH)] + [
            item for item in migrations if item[0] > _BASELINE_VERSION
        ]
    return migrations


def _manifest_for_rows(rows: list[dict]) -> list[tuple[int, str, Path]]:
    baseline = any(row["version"] == _BASELINE_VERSION
                   and row.get("name") == _BASELINE_PATH.name for row in rows)
    return fresh_manifest() if not rows or baseline else _discover()


def _verify_history(rows: list[dict], migrations: list[tuple[int, str, Path]], *,
                    complete: bool = False, canonical_only: bool = False) -> None:
    by_version = {row["version"]: row for row in rows}
    versions = [version for version, _name, _path in migrations]
    if len(by_version) != len(rows) or set(by_version) != set(versions[:len(rows)]):
        raise MigrationError("database migration history is not a release-compatible prefix")
    if complete and len(rows) != len(migrations):
        raise MigrationError("database has pending release migrations")
    for version, name, path in migrations[:len(rows)]:
        row = by_version[version]
        accepted = {_sha256(path)} if canonical_only else _accepted_digests(path)
        if row.get("name") != name or row["sha256"] not in accepted:
            raise MigrationChanged(f"migration {name} changed after it was applied")


def verify_release_history(rows: list[dict]) -> None:
    """Read-only readiness validation for a complete history.

    Accepts the same LF/CRLF-equivalent digests that `apply_migrations` accepts,
    so a fully migrated legacy-history database reads as ready without waiting
    for a process to normalize its checksums first.
    """
    _verify_history(rows, _manifest_for_rows(rows), complete=True)


def apply_migrations(conn: Connection) -> list[str]:
    """Apply atomically under a transaction lock shared across server processes."""
    with conn.transaction():
        conn.execute(queries.get("lock_schema_migrations"))
        return _apply_locked(conn)


def _apply_locked(conn: Connection) -> list[str]:
    _ensure_table(conn)
    rows = conn.execute(queries.get("list_applied_migrations")).fetchall()
    return _apply_manifest(conn, rows, _manifest_for_rows(rows))


def apply_legacy_migrations(conn: Connection) -> list[str]:
    """Build/upgrade the historical schema for compatibility checks and baseline export."""
    with conn.transaction():
        conn.execute(queries.get("lock_schema_migrations"))
        _ensure_table(conn)
        rows = conn.execute(queries.get("list_applied_migrations")).fetchall()
        return _apply_manifest(conn, rows, _discover())


def _apply_manifest(conn: Connection, rows: list[dict],
                    migrations: list[tuple[int, str, Path]]) -> list[str]:
    _verify_history(rows, migrations)
    applied = {row["version"]: row["sha256"] for row in rows}

    applied_now: list[str] = []
    for version, name, path in migrations:
        digest = _sha256(path)
        if version in applied:
            if applied[version] != digest:
                conn.execute(queries.get("normalize_migration_checksum"), {
                    "version": version, "old_sha": applied[version], "sha": digest,
                })
            continue
        with conn.transaction():
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute(
                queries.get("record_applied_migration"),
                {"version": version, "name": name, "sha": digest},
            )
        applied_now.append(name)

    if applied_now:
        # The extension may have just been created; register the vector adapter
        # so this same connection can bind vector values afterwards.
        register_vector_adapter(conn)
    return applied_now
