"""The explicit program manifest is verified before model or database setup."""

import json
import sys

import pytest

from scripts import ingest


def test_origin_manifest_requires_exact_content_hash_before_runtime_setup(tmp_path, monkeypatch):
    source = tmp_path / "catalog" / "example.md"
    source.parent.mkdir()
    source.write_text("catalog text", encoding="utf-8")
    manifest = tmp_path / "ingest-manifest.json"
    manifest.write_text(json.dumps({"sources": {
        "catalog/example.md": {
            "origin_key": "programs:catalog:example",
            "content_sha256": "0" * 64,
        },
    }}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["scripts.ingest", "--collection", "default",
                                       "--origin-manifest", str(manifest), str(source)])
    monkeypatch.setattr(ingest, "load_settings", lambda: pytest.fail("runtime setup must not start"))
    with pytest.raises(SystemExit) as exc:
        ingest.main()
    assert exc.value.code == 2


@pytest.mark.parametrize("provenance, error", [
    (None, None),  # Older manifests without provenance stay valid.
    ({"source_category": "career_reference", "publisher": "BLS",
      "published_at": "2025-09-01", "geography": "United States"}, None),
    ({"source_category": "institutional_catalog"}, None),
    # A trusted category is a verified importer's claim, never "user_supplied" etc.
    ({"source_category": "user_supplied"}, "source_category"),
    ({"source_category": "official"}, "source_category"),
    ({"source_category": "career_reference", "source_url": "file:///etc/passwd"}, "source_url"),
    ({"source_category": "career_reference", "published_at": "Sept 2025"}, "published_at"),
    ({"source_category": "career_reference", "salary_claim": "high"}, "unknown"),
])
def test_manifest_provenance_is_validated(provenance, error):
    from app.ingest.provenance import ProvenanceError, provenance_from_manifest

    entry = {"origin_key": "careers:bls:data-engineer", "acquired_at": "2026-10-12",
             "source_url": "https://www.bls.gov/ooh/"}
    if provenance is not None:
        entry["provenance"] = provenance
    if error:
        with pytest.raises(ProvenanceError, match=error):
            provenance_from_manifest(entry)
        return
    result = provenance_from_manifest(entry)
    if provenance is None:
        assert result is None
    else:
        # Top-level acquisition fields fill in what the provenance object omits.
        assert result.source_category == provenance["source_category"]
        assert result.source_url == "https://www.bls.gov/ooh/"
        assert str(result.acquired_at) == "2026-10-12"
