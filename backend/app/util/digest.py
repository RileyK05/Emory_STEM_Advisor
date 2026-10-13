"""Canonical-JSON SHA-256 digests for persisted request identities and plans.

Persisted fingerprints must stay byte-stable across releases: replay and
idempotency compare these digests against stored values. Never change the
JSON parameters without a migration strategy for existing stored rows.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def canonical_json_bytes(value) -> bytes:
    """Canonical UTF-8 JSON; raises TypeError/ValueError for non-finite or non-JSON data."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def json_digest(value) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    """SHA-256 of a file, streamed in 1 MiB blocks."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
