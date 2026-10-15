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
    ({"source_category": "institutional_publication", "title": "Medicine admissions",
      "institution": "Emory University", "issuing_unit": "School of Medicine",
      "document_roles": ["admissions", "preparation guidance"],
      "applicability": {"admitting_school": ["Emory School of Medicine"],
                        "entry_cohort": ["2027"]},
      "applicability_status": "reviewed_default"}, None),
    ({"source_category": "institutional_publication", "applicability_status": "historical"}, None),
    ({"source_category": "institutional_publication",
      "applicability": {"audience": ["x" * 198 + f"{i:02d}" for i in range(14)]}}, None),
    # A trusted category is a verified importer's claim, never "user_supplied" etc.
    ({"source_category": "user_supplied"}, "source_category"),
    ({"source_category": "official"}, "source_category"),
    ({"source_category": "career_reference", "source_url": "file:///etc/passwd"}, "source_url"),
    ({"source_category": "career_reference", "published_at": "Sept 2025"}, "published_at"),
    ({"source_category": "career_reference", "salary_claim": "high"}, "unknown"),
    ({"source_category": []}, "source_category"),
    ({"source_category": "institutional_publication", "title": " "}, "title"),
    ({"source_category": "institutional_publication", "title": "x" * 301}, "title"),
    ({"source_category": "institutional_publication", "institution": "x" * 201}, "institution"),
    ({"source_category": "institutional_publication", "issuing_unit": 42}, "issuing_unit"),
    ({"source_category": "institutional_publication", "document_roles": "admissions"},
     "document_roles"),
    ({"source_category": "institutional_publication", "document_roles": [" "]}, "document_roles"),
    ({"source_category": "institutional_publication", "document_roles": ["x" * 101]},
     "document_roles"),
    ({"source_category": "institutional_publication", "document_roles": ["x"] * 21},
     "document_roles"),
    ({"source_category": "institutional_publication", "applicability": []}, "applicability"),
    ({"source_category": "institutional_publication", "applicability": {"School": ["Emory"]}},
     "snake_case"),
    ({"source_category": "institutional_publication", "applicability": {"a" * 65: ["Emory"]}},
     "snake_case"),
    ({"source_category": "institutional_publication",
      "applicability": {f"scope_{i}": [] for i in range(21)}}, "applicability"),
    ({"source_category": "institutional_publication", "applicability": {"school": "Emory"}},
     "applicability.school"),
    ({"source_category": "institutional_publication", "applicability": {"school": [""]}},
     "applicability.school"),
    ({"source_category": "institutional_publication", "applicability": {"school": [42]}},
     "applicability.school"),
    ({"source_category": "institutional_publication", "applicability": {"school": ["x" * 201]}},
     "applicability.school"),
    ({"source_category": "institutional_publication", "applicability": {"school": ["x"] * 21}},
     "applicability.school"),
    ({"source_category": "institutional_publication", "applicability_status": "current"},
     "applicability_status"),
    ({"source_category": "institutional_publication",
      "applicability": {"audience": ["界" * 198 + f"{i:02d}" for i in range(14)]}}, "UTF-8"),
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
        assert result.title == provenance.get("title")
        assert result.institution == provenance.get("institution")
        assert result.issuing_unit == provenance.get("issuing_unit")
        assert result.document_roles == tuple(sorted(provenance.get("document_roles", [])))
        assert result.applicability == {
            key: tuple(sorted(values)) for key, values in provenance.get("applicability", {}).items()
        }
        assert result.applicability_status == provenance.get("applicability_status", "unknown")
