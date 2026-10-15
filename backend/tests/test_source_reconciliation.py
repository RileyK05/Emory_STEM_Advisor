"""Explicit legacy provenance and raw-hash reconciliation against Postgres."""

import hashlib
from pathlib import Path

import pytest

from app.db import queries
from app.db.connection import connect
from app.ingest.pipeline import ingest_file
from app.ingest.provenance import SourceProvenance
from scripts import reconcile_source_origins
from scripts.reconcile_source_origins import (
    OriginReconciliationError,
    provenance_origin_key,
    reconcile_origins,
)
from scripts.scrape_programs import department_origin_key
from tests.fakes import FakeEmbedder, whitespace_tokens
from tests.fixtures.pdf import make_pdf
from tests.helpers import lenient_config


def _ingest_legacy(db, collection, tmp_path, name, raw):
    path = tmp_path / name
    path.write_bytes(raw)
    return ingest_file(
        db,
        collection_id=collection,
        path=path,
        content_type="application/pdf" if name.endswith(".pdf") else "text/markdown",
        count_tokens=whitespace_tokens,
        embedder=FakeEmbedder(dim=8),
        embed_model="test-embed",
        config=lenient_config(),
    )


@pytest.mark.parametrize("content_type", ["text/markdown", "application/pdf"])
def test_reconcile_verified_legacy_program_versions_keeps_history(
        db, collection, tmp_path, content_type):
    url = ("https://biology.emory.edu/documents/"
           "2020_bs_requirements-8.1.20_updated_23_august_2023.pdf")
    old = (
        "# Program requirements: Chemistry BS (CHEMBS)\n\n"
        f"Source: {url}\nRetrieved: 2026-10-07\n"
        "Programs: Chemistry BS (CHEMBS)\n\nCopied content.\n"
    ).encode()
    new = old.replace(b"2026-10-07", b"2026-10-08")
    suffix = ".md"
    if content_type == "application/pdf":
        old, new = make_pdf(["Earlier requirements."]), make_pdf(["Later requirements."])
        suffix = ".pdf"
    old_id = _ingest_legacy(db, collection, tmp_path, "old" + suffix, old)
    new_id = _ingest_legacy(db, collection, tmp_path, "new" + suffix, new)
    old_hash, new_hash = hashlib.sha256(old).hexdigest(), hashlib.sha256(new).hexdigest()
    origin = department_origin_key(url)

    groups = [{
        "origin_key": origin,
        "source_sha256s": [old_hash, new_hash],
        "current_sha256": new_hash,
        "provenance_by_sha256": {digest: {
            "source_category": "institutional_department", "source_url": url,
            "publisher": "Fixture department", "applicability_status": "unknown",
        } for digest in [old_hash, new_hash]},
    }]
    before = {source_id: db.execute(queries.get("list_chunks_for_source"), {
        "source_id": source_id}).fetchall() for source_id in [old_id, new_id]}
    assert reconcile_origins(db, collection, groups, dry_run=True) == 2
    untouched = db.execute("SELECT origin_key, is_current FROM sources WHERE source_id=%s",
                           (new_id,)).fetchone()
    assert untouched == {"origin_key": None, "is_current": True}
    assert db.execute("SELECT 1 FROM source_provenance WHERE source_id=%s",
                      (new_id,)).fetchone() is None
    changed = reconcile_origins(db, collection, groups)

    assert changed == 2
    rows = db.execute(
        "SELECT source_id, origin_key, is_current FROM sources "
        "WHERE source_id = ANY(%(ids)s::uuid[]) ORDER BY source_id",
        {"ids": [old_id, new_id]},
    ).fetchall()
    by_id = {row["source_id"]: row for row in rows}
    assert by_id[old_id]["origin_key"] == origin
    assert by_id[old_id]["is_current"] is False
    assert by_id[new_id]["origin_key"] == origin
    assert by_id[new_id]["is_current"] is True
    for source_id in [old_id, new_id]:
        assert db.execute(queries.get("list_chunks_for_source"), {
            "source_id": source_id}).fetchall() == before[source_id]
    metadata = db.execute("SELECT publisher, applicability_status FROM source_provenance "
                          "WHERE source_id=%s", (new_id,)).fetchone()
    assert metadata == {"publisher": "Fixture department", "applicability_status": "unknown"}
    assert [row["source_id"] for row in db.execute(
        queries.get("list_source_fences_for_workspace"), {
            "collection_id": collection, "workspace_key": "development",
        }
    ).fetchall()] == [new_id]


@pytest.mark.parametrize("invalid", [
    "header", "pdf_missing_provenance", "pdf_wrong_url", "metadata_wrong_url",
    "other_origin", "unlisted_version",
])
def test_reconcile_rejects_hash_or_header_mismatch_without_mutation(
        db, collection, tmp_path, invalid):
    url = "https://chemistry.emory.edu/undergraduate/requirements.html"
    raw = ("# Program requirements: Chemistry BS (CHEMBS)\n\n"
           f"Source: {url}\nRetrieved: 2026-10-08\n\nRequirements.\n").encode()
    if invalid.startswith("pdf_"):
        raw = make_pdf(["Requirements from a department sheet."])
    source_id = _ingest_legacy(db, collection, tmp_path,
                               "legacy.pdf" if invalid.startswith("pdf_") else "legacy.md", raw)
    digest = hashlib.sha256(raw).hexdigest()
    group = {"origin_key": department_origin_key(url),
             "source_sha256s": [digest], "current_sha256": digest}
    if invalid == "header":
        group["origin_key"] = "programs:department:wrong-origin"
    elif invalid in {"pdf_wrong_url", "metadata_wrong_url"}:
        group["provenance_by_sha256"] = {digest: {
            "source_category": "institutional_department",
            "source_url": "https://chemistry.emory.edu/undergraduate/",
        }}
    elif invalid == "other_origin":
        db.execute("UPDATE sources SET origin_key=%s WHERE source_id=%s",
                   ("programs:department:another-origin", source_id))
    elif invalid == "unlisted_version":
        other = _ingest_legacy(db, collection, tmp_path, "other.md",
                               raw.replace(b"2026-10-08", b"2026-10-07"))
        db.execute("UPDATE sources SET origin_key=%s WHERE source_id=%s",
                   (group["origin_key"], other))
    before = db.execute("SELECT source_id, origin_key, is_current FROM sources "
                        "WHERE collection_id=%s ORDER BY source_id", (collection,)).fetchall()
    with pytest.raises(OriginReconciliationError):
        reconcile_origins(db, collection, [group])
    assert db.execute("SELECT source_id, origin_key, is_current FROM sources "
                      "WHERE collection_id=%s ORDER BY source_id", (collection,)).fetchall() == before


@pytest.mark.parametrize("mutation", ["raw", "provenance"])
def test_reconcile_checks_raw_bytes_against_stored_sha256(
        db, collection, tmp_path, migrated_dsn, monkeypatch, mutation):
    url = "https://chemistry.emory.edu/undergraduate/requirements.html"
    raw = ("# Program requirements: Chemistry BS (CHEMBS)\n\n"
           f"Source: {url}\nRetrieved: 2026-10-08\n\nRequirements.\n").encode()
    source_id = _ingest_legacy(db, collection, tmp_path, "legacy.md", raw)
    row = db.execute("SELECT file_hash, raw_path FROM sources WHERE source_id=%s",
                     (source_id,)).fetchone()
    group = {"origin_key": department_origin_key(url),
             "source_sha256s": [row["file_hash"]], "current_sha256": row["file_hash"]}
    if mutation == "raw":
        Path(row["raw_path"]).write_bytes(raw + b"tampered")
    else:
        group["provenance_by_sha256"] = {row["file_hash"]: {
            "source_category": "institutional_department", "source_url": url,
            "publisher": "Stale metadata draft",
        }}
        checked = reconcile_source_origins._verify_members
        corrected = False
        def correction_after_preflight(conn, collection_id, origin):
            nonlocal corrected
            checked(conn, collection_id, origin)
            if not corrected:
                with connect(migrated_dsn) as other:
                    other.execute(queries.get("upsert_source_provenance"), SourceProvenance(
                        "institutional_department", publisher="Reviewed correction",
                        source_url=url).row(source_id, row["file_hash"]))
                corrected = True
        monkeypatch.setattr(reconcile_source_origins, "_verify_members", correction_after_preflight)
    with pytest.raises(OriginReconciliationError,
                       match="failed SHA-256" if mutation == "raw" else "provenance changed"):
        reconcile_origins(db, collection, [group])
    state = db.execute("SELECT origin_key, is_current FROM sources WHERE source_id=%s",
                        (source_id,)).fetchone()
    assert state["origin_key"] is None
    assert state["is_current"] is True
    if mutation == "provenance":
        assert db.execute("SELECT publisher FROM source_provenance WHERE source_id=%s",
                          (source_id,)).fetchone()["publisher"] == "Reviewed correction"


@pytest.mark.parametrize("provenance", [
    b"URL: https://signed.example.invalid/download?token=rotating\nRetrieved: 2026-10-08\n",
    b"URL: https://college.catalog.emory.edu/\nCatalog ID: FUoE83Jq1SyDx5rJvewc\n",  # current scraper
])
def test_catalog_header_maps_to_stable_title_origin(provenance):
    raw = (
        b"# Biology BS\n\n"
        b"Source: Emory College of Arts and Sciences Catalog 2026-2027, "
        b"official catalog PDF, pages 81-83\n" + provenance + b"\nRequirements.\n"
    )
    assert provenance_origin_key(raw) == "programs:catalog:biology-bs"
