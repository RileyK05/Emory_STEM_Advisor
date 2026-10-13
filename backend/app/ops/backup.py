"""Checksummed logical database plus immutable raw/original file backup.

The encryption keys for application secrets are configuration and remain outside
the backup. Provider-key ciphertext is retained in the database dump.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import UUID

from psycopg.conninfo import conninfo_to_dict

from app.db import queries

FORMAT = "emory-stem-advisor-backup-v1"
_MAX_MANIFEST = 16 * 1024 * 1024


class BackupError(RuntimeError):
    """The backup cannot be created, verified, or restored safely."""


def _pg_bin(name: str) -> Path:
    try:
        import pgserver

        candidate = Path(pgserver.__file__).parent / "pginstall" / "bin" / f"{name}.exe"
        if candidate.is_file():
            return candidate
        candidate = candidate.with_suffix("")
        if candidate.is_file():
            return candidate
    except ImportError:
        pass
    candidate = shutil.which(name)
    if candidate:
        return Path(candidate)
    raise BackupError(f"{name} is unavailable; install matching PostgreSQL client tools")


def _pg_env(dsn: str) -> dict[str, str]:
    values = conninfo_to_dict(dsn)
    mapped = {
        "host": "PGHOST", "hostaddr": "PGHOSTADDR", "port": "PGPORT",
        "dbname": "PGDATABASE", "user": "PGUSER", "password": "PGPASSWORD",
        "sslmode": "PGSSLMODE", "sslcert": "PGSSLCERT", "sslkey": "PGSSLKEY",
        "sslrootcert": "PGSSLROOTCERT", "sslcrl": "PGSSLCRL",
        "connect_timeout": "PGCONNECT_TIMEOUT", "application_name": "PGAPPNAME",
    }
    env = os.environ.copy()
    for key in mapped.values():
        env.pop(key, None)
    for key, value in values.items():
        if key in mapped and value:
            env[mapped[key]] = value
    if not env.get("PGDATABASE"):
        raise BackupError("database URL must name a database")
    return env


def _run(command: list[str], env: dict[str, str]) -> str:
    result = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
    if result.returncode:
        # PostgreSQL tool output can contain connection details; never include it.
        raise BackupError(f"PostgreSQL utility failed with exit code {result.returncode}")
    return result.stdout.strip()


def _resolved_file(root: Path, raw_path: str, allowed: Path) -> Path:
    raw = Path(raw_path)
    if any(part.is_symlink() for part in (raw, *raw.parents) if part.exists()):
        raise BackupError("a database file reference crosses a symbolic link")
    path = raw.resolve(strict=True)
    root_resolved = root.resolve()
    allowed_resolved = allowed.resolve()
    if not path.is_relative_to(root_resolved) or not path.is_relative_to(allowed_resolved):
        raise BackupError("a database file reference escapes the configured data directory")
    if not path.is_file() or path.is_symlink():
        raise BackupError("a database file reference is not a regular file")
    return path


def _referenced_files(conn, data_dir: Path) -> list[dict]:
    items: dict[str, dict] = {}
    raw_root = data_dir / "raw"
    for row in conn.execute(queries.get("backup_source_files")).fetchall():
        path = _resolved_file(data_dir, row["raw_path"], raw_root)
        expected = row["file_hash"]
        rel = path.relative_to(data_dir.resolve()).as_posix()
        if rel in items:
            if items[rel]["sha256"] != expected:
                raise BackupError("shared raw-file references have inconsistent hashes")
        else:
            items[rel] = {"path": path, "sha256": expected, "source_ids": []}
        items[rel].setdefault("source_ids", []).append(str(row["source_id"]))
    originals_root = data_dir / "artifacts" / "originals"
    for row in conn.execute(queries.get("backup_artifact_files")).fetchall():
        path = _resolved_file(data_dir, row["original_path"], originals_root)
        expected = row["original_sha256"]
        rel = path.relative_to(data_dir.resolve()).as_posix()
        if rel not in items:
            items[rel] = {"path": path, "sha256": expected, "artifact_ids": []}
        items[rel].setdefault("artifact_ids", []).append(str(row["artifact_id"]))
    result = []
    for rel, item in sorted(items.items()):
        path, expected = item["path"], item["sha256"]
        digest, _ = _hash_file(path)
        if digest != expected:
            raise BackupError("a referenced file failed its database checksum")
        result.append({**item, "relative_path": rel, "sha256": digest})
    return result


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def create_backup(
    conn, *, dsn: str, data_dir: Path, destination: Path, maintenance_confirmed: bool = False
) -> dict:
    """Create a consistent logical DB dump and referenced immutable files."""
    if maintenance_confirmed is not True:
        raise BackupError("source maintenance must be confirmed before creating a backup")
    destination = destination.resolve()
    if destination.exists():
        raise BackupError("backup destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # End any caller transaction without committing it, so SET TRANSACTION can
    # take effect. The exported snapshot must stay open until pg_dump finishes,
    # then the read-only transaction is rolled back before returning.
    conn.rollback()
    conn.execute(queries.get("backup_set_repeatable_read_snapshot"))
    if not conn.execute(queries.get("backup_ready")).fetchone()["ready"]:
        raise BackupError("backup refused while jobs are queued or running, or model requests remain pending")
    snapshot = conn.execute(queries.get("backup_export_snapshot")).fetchone()["snapshot_id"]
    version = int(conn.execute(queries.get("backup_server_version")).fetchone()["server_version_num"])
    files = _referenced_files(conn, data_dir.resolve())

    with tempfile.TemporaryDirectory(prefix="emory-backup-") as temp_name:
        temp = Path(temp_name)
        dump = temp / "database.dump"
        env = _pg_env(dsn)
        dump_tool = _pg_bin("pg_dump")
        dump_version = _run([str(dump_tool), "--version"], env)
        dump_major = int(dump_version.split()[2].split(".")[0])
        if dump_major != version // 10000:
            raise BackupError("pg_dump major version must match the source database")
        _run([
            str(dump_tool), "--format=custom", "--no-owner", "--no-acl",
            f"--snapshot={snapshot}",
            f"--file={dump}",
        ], env)
        dump_sha, dump_size = _hash_file(dump)
        manifest = {
            "format": FORMAT,
            "created_at": datetime.now(UTC).isoformat(),
            "maintenance_confirmed": True,
            "postgres_server_version_num": version,
            "pg_dump_version": dump_version,
            "database_dump": {"path": "database.dump", "sha256": dump_sha, "size": dump_size},
            "files": [
                {
                    "path": item["relative_path"], "sha256": item["sha256"],
                    "size": item["path"].stat().st_size,
                    "source_ids": item.get("source_ids", []),
                    "artifact_ids": item.get("artifact_ids", []),
                }
                for item in files
            ],
        }
        manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        temp_archive_fd, temp_archive_name = tempfile.mkstemp(
            prefix=".emory-backup-", suffix=".tmp", dir=destination.parent
        )
        os.close(temp_archive_fd)
        temp_archive = Path(temp_archive_name)
        try:
            with zipfile.ZipFile(temp_archive, "w", compression=zipfile.ZIP_STORED) as archive:
                archive.write(dump, "database.dump")
                for item in files:
                    digest = hashlib.sha256()
                    size = 0
                    with item["path"].open("rb") as source, archive.open(
                        f"files/{item['relative_path']}", "w"
                    ) as target:
                        for block in iter(lambda: source.read(1024 * 1024), b""):
                            digest.update(block)
                            size += len(block)
                            target.write(block)
                    if digest.hexdigest() != item["sha256"] or size != item["path"].stat().st_size:
                        raise BackupError("a referenced file changed during backup")
                archive.writestr("manifest.json", manifest_bytes)
            # Same-directory hard link publishes the finished archive atomically
            # and fails if another process created the destination meanwhile.
            try:
                os.link(temp_archive, destination)
            except FileExistsError as exc:
                raise BackupError("backup destination already exists") from exc
        finally:
            temp_archive.unlink(missing_ok=True)
    # Release the exported snapshot / read-only transaction.
    conn.rollback()
    return manifest


def _validate_member(name: str) -> bool:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        return False
    if name == "database.dump":
        return True
    return name.startswith(("files/raw/", "files/artifacts/originals/"))


def _load_and_verify(archive_path: Path, stage: Path) -> tuple[dict, Path]:
    try:
        archive = zipfile.ZipFile(archive_path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise BackupError("backup archive is invalid") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > 200_000 or sum(info.file_size for info in infos) > 2**44:
            raise BackupError("backup archive exceeds supported bounds")
        names = [info.filename for info in infos]
        symlink_members = [
            info for info in infos
            if ((info.external_attr >> 16) & 0o170000) == 0o120000
        ]
        if (len(names) != len(set(names)) or symlink_members
                or any(not _validate_member(n) and n != "manifest.json" for n in names)):
            raise BackupError("backup archive contains an invalid path")
        if "manifest.json" not in names or "database.dump" not in names:
            raise BackupError("backup archive is missing required members")
        info = archive.getinfo("manifest.json")
        if info.file_size > _MAX_MANIFEST:
            raise BackupError("backup manifest is too large")
        try:
            manifest = json.loads(archive.read(info))
        except (ValueError, UnicodeDecodeError) as exc:
            raise BackupError("backup manifest is invalid") from exc
        if manifest.get("format") != FORMAT:
            raise BackupError("unsupported backup format")
        expected = {"database.dump": manifest.get("database_dump", {})}
        for item in manifest.get("files", []):
            rel = item.get("path", "")
            if not rel.startswith(("raw/", "artifacts/originals/")):
                raise BackupError("backup manifest contains an invalid file path")
            expected[f"files/{rel}"] = item
        if set(names) != set(expected) | {"manifest.json"}:
            raise BackupError("backup archive contents do not match its manifest")
        for name, item in expected.items():
            if archive.getinfo(name).file_size != item.get("size"):
                raise BackupError("backup member size does not match its manifest")
            out = (stage / name).resolve()
            if not out.is_relative_to(stage.resolve()):
                raise BackupError("backup member path escapes the restore staging directory")
            out.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as source, out.open("xb") as target:
                digest = hashlib.sha256()
                size = 0
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
                    size += len(block)
                    target.write(block)
            if size != item["size"] or digest.hexdigest() != item["sha256"]:
                raise BackupError("backup member failed checksum verification")
    return manifest, stage / "database.dump"


def restore_backup(conn, *, dsn: str, archive_path: Path, data_dir: Path) -> dict:
    """Restore into an empty database and a new data directory only."""
    target_data = data_dir.resolve()
    if target_data.exists():
        raise BackupError("restore data directory must not already exist")
    target_data.parent.mkdir(parents=True, exist_ok=True)
    if not conn.execute(queries.get("backup_target_empty")).fetchone()["is_empty"]:
        raise BackupError("restore target database is not empty")
    target_version = int(conn.execute(queries.get("backup_server_version")).fetchone()["server_version_num"])
    conn.commit()
    database_restored = False
    with tempfile.TemporaryDirectory(prefix="emory-restore-", dir=target_data.parent) as temp_name:
        temp = Path(temp_name)
        manifest, dump_path = _load_and_verify(archive_path.resolve(), temp / "stage")
        source_major = int(manifest["postgres_server_version_num"]) // 10000
        if target_version // 10000 != source_major:
            raise BackupError("restore target PostgreSQL major version must match the backup")
        restore_tool = _pg_bin("pg_restore")
        env = _pg_env(dsn)
        restore_version = _run([str(restore_tool), "--version"], env)
        if int(restore_version.split()[2].split(".")[0]) != source_major:
            raise BackupError("pg_restore major version must match the backup")
        _run([
            str(restore_tool), "--single-transaction", "--exit-on-error",
            "--no-owner", "--no-acl", f"--dbname={env['PGDATABASE']}", str(dump_path),
        ], env)
        database_restored = True
        staged_data = temp / "stage" / "files"
        target_created = False
        try:
            target_data.mkdir()
            target_created = True
            with conn.transaction():
                for item in manifest.get("files", []):
                    rel_path = item["path"]
                    final_path = target_data.joinpath(*PurePosixPath(rel_path).parts)
                    if final_path.exists():
                        raise BackupError("restore would overwrite an existing data path")
                    for source_id in item.get("source_ids", []):
                        conn.execute(queries.get("backup_restore_source_path"), {
                            "source_id": UUID(source_id), "raw_path": str(final_path),
                        })
                    for artifact_id in item.get("artifact_ids", []):
                        conn.execute(queries.get("backup_restore_artifact_path"), {
                            "artifact_id": UUID(artifact_id), "original_path": str(final_path),
                        })
                for top in ("raw", "artifacts"):
                    staged = staged_data / top
                    if staged.exists():
                        shutil.move(str(staged), str(target_data / top))
                # Validate every DB-referenced immutable file while path updates are
                # still uncommitted. On error the pointers roll back and new files
                # are removed below.
                _referenced_files(conn, target_data)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            if target_created:
                shutil.rmtree(target_data, ignore_errors=True)
            if database_restored:
                raise BackupError(
                    "database restore completed but file publication failed; discard this "
                    "incomplete target database and retry with a fresh one"
                ) from exc
            raise
    return manifest
