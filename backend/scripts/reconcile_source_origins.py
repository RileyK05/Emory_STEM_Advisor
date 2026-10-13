"""Safely group verified legacy program Markdown versions on a cloned database.

The manifest must explicitly enumerate exact raw SHA-256 values for every
recognized historical version and select one already-indexed current version.
This command never downloads or rewrites source, chunk, or excerpt data.

Manifest shape::

    {"origins": [{"origin_key": "programs:department:...",
                  "source_sha256s": ["<old sha256>", "<current sha256>"],
                  "current_sha256": "<current sha256>"}]}
"""

import argparse
import hashlib
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from app.config import load_settings
from app.db import queries
from scripts import scrape_programs
from scripts._db import open_connection

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DEPARTMENT_HEADER = re.compile(
    r"\A# Program requirements: [^\n]+\n\n"
    r"Source: (https?://[^\n]+)\nRetrieved: (\d{4}-\d{2}-\d{2})\n"
)
_CATALOG_HEADER = re.compile(
    r"\A# ([^\n]+)\n\n"
    r"Source: Emory College of Arts and Sciences Catalog[^\n]*, "
    r"official catalog PDF, [^\n]+\n"
    # Legacy acquisition header, or the scraper's current canonical header
    # (acquisition details moved to the ingest manifest).
    r"(?:URL: https?://[^\n]+\nRetrieved: (\d{4}-\d{2}-\d{2})"
    r"|URL: https://college\.catalog\.emory\.edu/\nCatalog ID: [A-Za-z0-9]+)\n"
)


class OriginReconciliationError(ValueError):
    """An explicit legacy-origin manifest does not match source provenance."""


def provenance_origin_key(raw: bytes) -> str | None:
    """Recognize only exact headers emitted by the official programs scraper."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    department = _DEPARTMENT_HEADER.match(text)
    if department is not None:
        url, acquired = department.groups()
        date.fromisoformat(acquired)
        host = urlparse(url).hostname
        if host is None or not (host == "emory.edu" or host.endswith(".emory.edu")):
            return None
        return scrape_programs.department_origin_key(url)
    catalog = _CATALOG_HEADER.match(text)
    if catalog is not None:
        title, acquired = catalog.groups()
        if acquired is not None:
            date.fromisoformat(acquired)
        return scrape_programs.catalog_origin_key(title)
    return None


def _validate_groups(groups: object) -> list[dict]:
    if not isinstance(groups, list) or not groups:
        raise OriginReconciliationError("manifest origins must be a non-empty list")
    validated = []
    seen_hashes: set[str] = set()
    seen_origins: set[str] = set()
    for item in groups:
        if not isinstance(item, dict):
            raise OriginReconciliationError("each origin entry must be an object")
        origin_key = item.get("origin_key")
        hashes = item.get("source_sha256s")
        current = item.get("current_sha256")
        if (not isinstance(origin_key, str)
                or not origin_key.startswith(("programs:catalog:", "programs:department:"))):
            raise OriginReconciliationError("origin_key is not an official program origin")
        if origin_key in seen_origins:
            raise OriginReconciliationError("origin_key appears more than once")
        if (not isinstance(hashes, list) or not hashes
                or any(not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None
                       for value in hashes)):
            raise OriginReconciliationError("source_sha256s must contain lowercase SHA-256 values")
        if len(set(hashes)) != len(hashes) or current not in hashes:
            raise OriginReconciliationError("source hashes must be unique and include current_sha256")
        if any(value in seen_hashes for value in hashes):
            raise OriginReconciliationError("one source hash cannot belong to multiple origins")
        seen_origins.add(origin_key)
        seen_hashes.update(hashes)
        validated.append({"origin_key": origin_key, "source_sha256s": hashes,
                          "current_sha256": current})
    return validated


def reconcile_origins(conn, collection_id: UUID, groups: object) -> int:
    """Assign explicit groups after verifying each stored raw file and header.

    Mutations occur in one transaction; any missing, stale, or unrecognized
    source aborts the whole reconciliation.
    """
    validated = _validate_groups(groups)
    prepared: list[dict] = []
    for group in validated:
        rows = conn.execute(queries.get("list_origin_reconciliation_sources"), {
            "collection_id": collection_id,
            "workspace_key": None,
            "file_hashes": group["source_sha256s"],
        }).fetchall()
        by_hash = {row["file_hash"]: row for row in rows}
        if set(by_hash) != set(group["source_sha256s"]):
            raise OriginReconciliationError("manifest source hashes do not match shared source rows")
        verified = []
        for file_hash, row in by_hash.items():
            raw_path = Path(row["raw_path"])
            if row["content_type"] != "text/markdown" or row["status"] != "indexed":
                raise OriginReconciliationError("only indexed Markdown sources can be reconciled")
            try:
                raw = raw_path.read_bytes()
            except OSError as exc:
                raise OriginReconciliationError("a source raw file is unavailable") from exc
            if hashlib.sha256(raw).hexdigest() != file_hash:
                raise OriginReconciliationError("a source raw file failed SHA-256 verification")
            if provenance_origin_key(raw) != group["origin_key"]:
                raise OriginReconciliationError("source header does not match its declared origin")
            verified.append(row)
        prepared.append({**group, "rows": verified})

    with conn.transaction():
        for group in prepared:
            row_ids = [row["source_id"] for row in group["rows"]]
            current_id = next(row["source_id"] for row in group["rows"]
                              if row["file_hash"] == group["current_sha256"])
            identity = {
                "collection_id": collection_id, "workspace_key": None,
                "origin_key": group["origin_key"], "source_id": current_id,
            }
            conn.execute(queries.get("lock_source_origin_publication"), identity)
            locked = conn.execute(queries.get("lock_origin_reconciliation_sources"), {
                "collection_id": collection_id, "workspace_key": None,
                "source_ids": row_ids,
            }).fetchall()
            if {row["source_id"] for row in locked} != set(row_ids):
                raise OriginReconciliationError("source rows changed during reconciliation")
            conn.execute(queries.get("deactivate_sources_for_origin"), identity)
            for row in group["rows"]:
                updated = conn.execute(queries.get("assign_reconciled_source_origin"), {
                    "source_id": row["source_id"], "collection_id": collection_id,
                    "workspace_key": None, "file_hash": row["file_hash"],
                    "origin_key": group["origin_key"],
                })
                if updated.rowcount != 1:
                    raise OriginReconciliationError("a source stopped being eligible")
            activated = conn.execute(queries.get("activate_source_origin"), {
                **identity, "source_id": current_id,
            })
            if activated.rowcount != 1:
                raise OriginReconciliationError("the selected source could not be activated")
    return sum(len(group["rows"]) for group in prepared)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        groups = _validate_groups(manifest.get("origins") if isinstance(manifest, dict) else None)
    except (OSError, json.JSONDecodeError, OriginReconciliationError) as exc:
        parser.error(f"invalid reconciliation manifest: {exc}")

    settings = load_settings()
    with open_connection(settings, args.database_url) as conn:
        collection = conn.execute(queries.get("get_collection_by_name"), {
            "name": args.collection,
        }).fetchone()
        if collection is None:
            parser.error(f"unknown collection {args.collection!r}")
        count = reconcile_origins(conn, collection["collection_id"], groups)
        conn.commit()
    print(f"reconciled {count} verified historical program sources")


if __name__ == "__main__":
    main()
