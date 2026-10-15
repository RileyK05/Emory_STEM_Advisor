"""Verify legacy program source identities; apply only to an explicit cloned database.

The manifest must explicitly enumerate exact raw SHA-256 values for every
recognized historical version and select one already-indexed current version.
This command never downloads or rewrites source, chunk, or excerpt data. The
default is a read-only dry run. --apply requires an explicit --database-url.

Manifest shape::

    {"origins": [{"origin_key": "programs:department:...",
                  "source_sha256s": ["<old sha256>", "<current sha256>"],
                  "current_sha256": "<current sha256>",
                  "provenance_by_sha256": {
                    "<current sha256>": {
                      "source_category": "institutional_department",
                      "source_url": "<exact configured department PDF URL>",
                      "applicability_status": "unknown"}}}]}

PDFs require explicit exact-hash provenance with a URL from --config. Markdown
retains the scraper-header verification. Selecting an indexed current version
does not verify that its academic requirements apply to today's student.
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
from app.db.connection import connect
from app.ingest.provenance import ProvenanceError, provenance_from_manifest
from scripts import scrape_programs

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
        if set(item) - {"origin_key", "source_sha256s", "current_sha256", "provenance_by_sha256"}:
            raise OriginReconciliationError("unknown origin reconciliation fields")
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
        metadata = item.get("provenance_by_sha256", {})
        if not isinstance(metadata, dict) or set(metadata) - set(hashes):
            raise OriginReconciliationError("provenance hashes must belong to the explicit source group")
        for provenance in metadata.values():
            try:
                parsed = provenance_from_manifest({"provenance": provenance})
            except ProvenanceError as exc:
                raise OriginReconciliationError(str(exc)) from exc
            if parsed is None:
                raise OriginReconciliationError("per-source provenance must be an object")
        seen_origins.add(origin_key)
        seen_hashes.update(hashes)
        validated.append({"origin_key": origin_key, "source_sha256s": hashes,
                          "current_sha256": current, "provenance_by_sha256": metadata})
    return validated


def _verify_source(row, group, department_urls):
    if row["origin_key"] not in (None, group["origin_key"]):
        raise OriginReconciliationError("a source is already assigned to a different origin")
    if row["status"] != "indexed" or row["content_type"] not in ("text/markdown", "application/pdf"):
        raise OriginReconciliationError("only indexed Markdown or PDF sources can be reconciled")
    try:
        raw = Path(row["raw_path"]).read_bytes()
    except OSError as exc:
        raise OriginReconciliationError("a source raw file is unavailable") from exc
    file_hash = row["file_hash"]
    if hashlib.sha256(raw).hexdigest() != file_hash:
        raise OriginReconciliationError("a source raw file failed SHA-256 verification")
    metadata = group["provenance_by_sha256"].get(file_hash)
    provenance = provenance_from_manifest({"provenance": metadata})
    if row["content_type"] == "text/markdown":
        if provenance_origin_key(raw) != group["origin_key"]:
            raise OriginReconciliationError("source header does not match its declared origin")
        header = _DEPARTMENT_HEADER.match(raw.decode("utf-8"))
        if (header is not None and provenance is not None
                and provenance.source_url != header.group(1)):
            raise OriginReconciliationError("verified source URL does not match its legacy header")
    elif (provenance is None or provenance.source_url not in department_urls
          or not raw.startswith(b"%PDF-")
          or scrape_programs.department_origin_key(provenance.source_url) != group["origin_key"]):
        raise OriginReconciliationError(
            "PDF requires exact-hash provenance with its configured department source URL")
    return provenance


def _verify_members(conn, collection_id, group):
    members = conn.execute(queries.get("list_origin_reconciliation_members"), {
        "collection_id": collection_id, "workspace_key": None,
        "origin_key": group["origin_key"],
    }).fetchall()
    if any(row["file_hash"] not in group["source_sha256s"] for row in members):
        raise OriginReconciliationError("origin has source versions absent from the explicit group")


def reconcile_origins(conn, collection_id: UUID, groups: object, *, dry_run: bool = False,
                      department_urls: set[str] | None = None) -> int:
    """Assign explicit groups after verifying each stored raw file and header.

    Mutations occur in one transaction; any missing, stale, or unrecognized
    source aborts the whole reconciliation.
    """
    validated = _validate_groups(groups)
    if department_urls is None:
        _, pages = scrape_programs.load_config(scrape_programs.DEFAULT_CONFIG)
        department_urls = {page["url"] for page in pages}
    prepared: list[dict] = []
    for group in sorted(validated, key=lambda item: item["origin_key"]):
        rows = conn.execute(queries.get("list_origin_reconciliation_sources"), {
            "collection_id": collection_id,
            "workspace_key": None,
            "file_hashes": group["source_sha256s"],
        }).fetchall()
        by_hash = {row["file_hash"]: row for row in rows}
        if set(by_hash) != set(group["source_sha256s"]):
            raise OriginReconciliationError("manifest source hashes do not match shared source rows")
        provenance = {file_hash: _verify_source(row, group, department_urls)
                      for file_hash, row in by_hash.items()}
        _verify_members(conn, collection_id, group)
        prepared.append({**group, "rows": list(by_hash.values()), "provenance": provenance})

    if dry_run:
        return sum(len(group["rows"]) for group in prepared)

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
            if {row["source_id"]: row for row in locked} != {
                    row["source_id"]: {key: value for key, value in row.items()
                                       if key != "provenance"} for row in group["rows"]}:
                raise OriginReconciliationError("source rows changed during reconciliation")
            # Read metadata in a fresh statement after the source locks. A
            # writer may have committed while our locking statement waited;
            # joining provenance in that statement could retain its old snapshot.
            refreshed = conn.execute(queries.get("list_origin_reconciliation_sources"), {
                "collection_id": collection_id, "workspace_key": None,
                "file_hashes": group["source_sha256s"],
            }).fetchall()
            if {row["source_id"]: row for row in refreshed} != {
                    row["source_id"]: row for row in group["rows"]}:
                raise OriginReconciliationError("source provenance changed during reconciliation")
            _verify_members(conn, collection_id, group)
            for row in locked:
                _verify_source(row, group, department_urls)
            conn.execute(queries.get("deactivate_sources_for_origin"), identity)
            for row in group["rows"]:
                updated = conn.execute(queries.get("assign_reconciled_source_origin"), {
                    "source_id": row["source_id"], "collection_id": collection_id,
                    "workspace_key": None, "file_hash": row["file_hash"],
                    "origin_key": group["origin_key"],
                })
                if updated.rowcount != 1:
                    raise OriginReconciliationError("a source stopped being eligible")
                provenance = group["provenance"][row["file_hash"]]
                if provenance is not None:
                    conn.execute(queries.get("upsert_source_provenance"),
                                 provenance.row(row["source_id"], row["file_hash"]))
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
    parser.add_argument("--config", type=Path, default=scrape_programs.DEFAULT_CONFIG)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="read-only verification (default)")
    mode.add_argument("--apply", action="store_true", help="apply to an explicit cloned database URL")
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        groups = _validate_groups(manifest.get("origins") if isinstance(manifest, dict) else None)
    except (OSError, json.JSONDecodeError, OriginReconciliationError) as exc:
        parser.error(f"invalid reconciliation manifest: {exc}")

    settings = load_settings()
    dsn = args.database_url or settings.database_url
    if not dsn or (args.apply and not args.database_url):
        parser.error("use --database-url for --apply; dry runs also accept DATABASE_URL")
    try:
        _, pages = scrape_programs.load_config(args.config)
    except (OSError, ValueError) as exc:
        parser.error(f"invalid department config: {exc}")
    with connect(dsn) as conn:
        if not args.apply:
            # Registering the vector adapter can start a read transaction.
            # READ ONLY must be set before this verification transaction reads.
            conn.rollback()
            conn.execute(queries.get("intake_read_only"))
        collection = conn.execute(queries.get("get_collection_by_name"), {
            "name": args.collection,
        }).fetchone()
        if collection is None:
            parser.error(f"unknown collection {args.collection!r}")
        count = reconcile_origins(conn, collection["collection_id"], groups,
                                  dry_run=not args.apply,
                                  department_urls={page["url"] for page in pages})
        if args.apply:
            conn.commit()
        else:
            conn.rollback()
    print(f"{'reconciled' if args.apply else 'verified (dry run; no changes)'} "
          f"{count} historical program sources")


if __name__ == "__main__":
    main()
