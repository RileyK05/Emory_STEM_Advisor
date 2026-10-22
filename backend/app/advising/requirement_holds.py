"""Curator-controlled, source-scoped holds for conflicting atlas requirements."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, TypedDict, cast
from uuid import UUID, uuid4

from app.atlas.records import normalize_code
from app.db import queries

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_ATLAS_TYPE = "application/x-course-atlas+jsonl"


class RequirementHold(TypedDict):
    hold_id: UUID
    source_id: UUID
    course_code: str
    master_file_hash: str
    conflict_reason: str
    evidence_capture_sha256: str
    evidence_capture_path: str
    evidence_source_id: UUID | None
    evidence_index_revision: int | None
    evidence_capture_set_hash: str | None
    status: Literal["active", "released"]
    created_by: str
    created_at: datetime
    released_by: str | None
    released_at: datetime | None
    release_note: str | None


def _hash(value: str, name: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
    return value


def _course_code(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("course_code must be an exact normalized atlas code")
    code = normalize_code(value)
    if code is None:
        raise ValueError("course_code must be an exact normalized atlas code")
    return code


def _verify_capture_file(path_value: str, expected_hash: str) -> str:
    path = Path(path_value)
    candidates = [path] if path.is_absolute() else [Path.cwd() / path]
    if not path.is_absolute():
        repository_root = Path(__file__).resolve().parents[3]
        repository_path = repository_root / path
        if repository_path not in candidates:
            candidates.append(repository_path)
    capture = next((candidate for candidate in candidates
                    if candidate.is_file() and not candidate.is_symlink()), None)
    if capture is None:
        raise ValueError("evidence_capture_path must point to a saved capture file")
    actual_hash = hashlib.sha256(capture.read_bytes()).hexdigest()
    if actual_hash != expected_hash:
        raise ValueError("evidence_capture_sha256 does not match the saved capture file")
    return str(capture.resolve(strict=True))


def _master(conn, source_id: UUID, expected_hash: str, course_code: str) -> Mapping[str, Any]:
    source = conn.execute(queries.get("get_requirement_hold_master"), {
        "source_id": source_id,
    }).fetchone()
    if (source is None or source["content_type"] != _ATLAS_TYPE
            or source["status"] != "indexed" or not source["is_current"]):
        raise ValueError("source must be the current indexed course atlas")
    if source["file_hash"] != expected_hash:
        raise ValueError("master file hash does not match the current source")
    course = conn.execute(queries.get("get_requirement_hold_course"), {
        "source_id": source_id, "course_code": course_code,
    }).fetchone()
    if course is None:
        raise ValueError("course_code is not present in the exact master source")
    return source


def apply_requirement_hold(
    conn,
    *,
    source_id: UUID,
    course_code: str,
    master_file_hash: str,
    conflict_reason: str,
    evidence_capture_sha256: str,
    evidence_capture_path: str,
    curator: str,
    evidence_source_id: UUID | None = None,
    evidence_index_revision: int | None = None,
    evidence_capture_set_hash: str | None = None,
) -> RequirementHold:
    """Hold one exact master course after validating both its source hash and identity.

    Capture evidence may remain a local immutable file pointer until it is
    published. If published offering evidence is supplied, its exact indexed
    revision and capture-set hash are required and checked before the hold saves.
    The validation and insert share one transaction; when called inside an
    existing transaction the connection uses a savepoint.
    """
    if not isinstance(source_id, UUID):
        raise TypeError("source_id must be a UUID")
    if evidence_source_id is not None and not isinstance(evidence_source_id, UUID):
        raise TypeError("evidence_source_id must be a UUID")
    code = _course_code(course_code)
    master_hash = _hash(master_file_hash, "master_file_hash")
    capture_hash = _hash(evidence_capture_sha256, "evidence_capture_sha256")
    if not isinstance(conflict_reason, str) or not conflict_reason.strip():
        raise ValueError("conflict_reason must be nonempty")
    if not isinstance(evidence_capture_path, str) or not evidence_capture_path.strip():
        raise ValueError("evidence_capture_path must be nonempty")
    capture_path = _verify_capture_file(evidence_capture_path.strip(), capture_hash)
    if not isinstance(curator, str) or not curator.strip():
        raise ValueError("curator must be nonempty")
    if (evidence_source_id is None) != (evidence_index_revision is None):
        raise ValueError("published evidence requires both source_id and index_revision")
    if evidence_source_id is None and evidence_capture_set_hash is not None:
        raise ValueError("capture_set_hash requires published offering evidence")
    if evidence_source_id is not None:
        if (not isinstance(evidence_index_revision, int)
                or isinstance(evidence_index_revision, bool) or evidence_index_revision <= 0
                or evidence_capture_set_hash is None):
            raise ValueError("published evidence requires a positive revision and exact capture-set hash")
        capture_set_hash = _hash(evidence_capture_set_hash, "evidence_capture_set_hash")
    else:
        capture_set_hash = None

    with conn.transaction():
        master = _master(conn, source_id, master_hash, code)
        if evidence_source_id is not None:
            offering = conn.execute(queries.get("get_atlas_offering_snapshot_for_revision"), {
                "source_id": evidence_source_id,
                "index_revision": evidence_index_revision,
                "collection_id": master["collection_id"],
                "capture_set_hash": capture_set_hash,
            }).fetchone()
            if offering is None:
                raise ValueError("offering evidence does not match the exact indexed revision and capture set")
            if capture_hash not in offering["capture_hashes"]:
                raise ValueError("evidence capture hash is not part of the exact offering capture set")
        active = conn.execute(queries.get("get_active_atlas_requirement_hold"), {
            "source_id": source_id, "course_code": code, "master_file_hash": master_hash,
        }).fetchone()
        if active is not None:
            raise ValueError("an active hold already exists for this source and course")
        row = conn.execute(queries.get("insert_atlas_requirement_hold"), {
            "hold_id": uuid4(), "source_id": source_id, "course_code": code,
            "master_file_hash": master_hash, "conflict_reason": conflict_reason.strip(),
            "evidence_capture_sha256": capture_hash,
            "evidence_capture_path": capture_path.strip(),
            "evidence_source_id": evidence_source_id,
            "evidence_index_revision": evidence_index_revision,
            "evidence_capture_set_hash": capture_set_hash,
            "created_by": curator.strip(),
        }).fetchone()
        if row is None:
            raise ValueError("an active hold already exists for this source, course, and master hash")
        return cast(RequirementHold, dict(row))


def release_requirement_hold(
    conn,
    *,
    hold_id: UUID,
    source_id: UUID,
    course_code: str,
    master_file_hash: str,
    curator: str,
    release_note: str,
) -> RequirementHold:
    """Release one hold only against the same current source and exact master hash."""
    if not isinstance(hold_id, UUID) or not isinstance(source_id, UUID):
        raise TypeError("hold_id and source_id must be UUIDs")
    code = _course_code(course_code)
    master_hash = _hash(master_file_hash, "master_file_hash")
    if not isinstance(curator, str) or not curator.strip():
        raise ValueError("curator must be nonempty")
    if not isinstance(release_note, str) or not release_note.strip():
        raise ValueError("release_note must explain the review disposition")
    with conn.transaction():
        _master(conn, source_id, master_hash, code)
        row = conn.execute(queries.get("release_atlas_requirement_hold"), {
            "hold_id": hold_id, "source_id": source_id, "course_code": code,
            "master_file_hash": master_hash, "released_by": curator.strip(),
            "release_note": release_note.strip(),
        }).fetchone()
        if row is None:
            raise ValueError("active hold does not match the exact source, course, and master hash")
        return cast(RequirementHold, dict(row))
