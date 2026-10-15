"""Deterministic preparation of explicitly reviewed advising documents.

The registry is an allowlist, not a filename classifier. No extraction, archive
handling, model calls, or network access happen here. PDF bytes are retained;
Markdown loses only the exact acquisition wrapper used by this supplied batch.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath

from app.ingest.provenance import provenance_from_manifest

_SHA = re.compile(r"[a-f0-9]{64}")
_FILENAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\.(?:pdf|md)")
_WRAPPER = re.compile(
    rb"\A# [^\r\n]+\r?\n\r?\nSource: (https?://[^\r\n]+)\r?\n"
    rb"Retrieved: (\d{4}-\d{2}-\d{2}) \(ET\)\r?\n\r?\n---\r?\n\r?\n"
)
_REGISTRY_FIELDS = {"schema_version", "input_subdir", "acquired_at", "review_notes", "document"}
_DOCUMENT_FIELDS = {
    "id", "disposition", "original_path", "original_sha256", "reason", "origin_key",
    "canonical_filename", "normalization", "provenance", "alias_of", "superseded_by",
}


@dataclass(frozen=True)
class PreparedDocument:
    filename: str
    content: bytes
    manifest_entry: dict


@dataclass(frozen=True)
class PreparedBatch:
    documents: tuple[PreparedDocument, ...]
    lineage: dict

    def manifest(self) -> dict:
        return {"sources": {f"documents/{doc.filename}": doc.manifest_entry
                            for doc in self.documents}}


def _text(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _original(root: Path, relative) -> Path:
    relative = _text(relative, "original_path")
    path = PurePosixPath(relative)
    if (path.is_absolute() or "\\" in relative or ":" in relative
            or any(part in {".", "..", ""} for part in relative.split("/"))):
        raise ValueError(f"original_path must stay within the input directory: {relative}")
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"original_path escapes the input directory: {relative}")
    if not resolved.is_file():
        raise ValueError(f"original_path is not a file: {relative}")
    return resolved


def prepare_documents(registry: dict, *, input_root: Path,
                      registry_sha256: str) -> PreparedBatch:
    """Validate the whole review before returning any bytes eligible for writing.

    Unlisted files, historical editions, holds, and aliases never enter the
    manifest. Original hashes are checked for every recorded disposition, so a
    replaced input cannot silently inherit an earlier review.
    """
    if not isinstance(registry, dict) or set(registry) - _REGISTRY_FIELDS:
        raise ValueError("registry contains unknown fields")
    if registry.get("schema_version") != 1:
        raise ValueError("registry schema_version must be 1")
    acquired = _text(registry.get("acquired_at"), "acquired_at")
    try:
        if date.fromisoformat(acquired).isoformat() != acquired:
            raise ValueError
    except ValueError:
        raise ValueError("acquired_at must be an ISO date") from None
    records = registry.get("document")
    if not isinstance(records, list) or not records:
        raise ValueError("registry document must be a non-empty array")
    root = input_root.resolve()
    documents = []
    lineage = []
    identifiers, origins, filenames, original_paths = set(), set(), set(), set()
    for record in records:
        if not isinstance(record, dict) or set(record) - _DOCUMENT_FIELDS:
            raise ValueError("registry document contains unknown fields")
        identifier = _text(record.get("id"), "id")
        if identifier in identifiers:
            raise ValueError(f"duplicate document id: {identifier}")
        identifiers.add(identifier)
        disposition = record.get("disposition")
        if disposition not in {"include", "hold", "historical", "alias"}:
            raise ValueError(f"{identifier}: invalid disposition")
        _text(record.get("reason"), f"{identifier} reason")
        original = _original(root, record.get("original_path"))
        if original in original_paths:
            raise ValueError(f"duplicate original_path: {record['original_path']}")
        original_paths.add(original)
        expected = record.get("original_sha256")
        if not isinstance(expected, str) or not _SHA.fullmatch(expected):
            raise ValueError(f"{identifier}: original_sha256 must be a SHA-256 digest")
        content = original.read_bytes()
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError(f"{identifier}: original does not match reviewed SHA-256")
        item = dict(record)
        lineage.append(item)
        if disposition != "include":
            continue
        if "alias_of" in record:
            raise ValueError(f"{identifier}: an included document cannot be an alias")
        filename = record.get("canonical_filename")
        if (not isinstance(filename, str) or not _FILENAME.fullmatch(filename)
                or len(Path(filename).stem) > 100):
            raise ValueError(f"{identifier}: invalid canonical_filename (stem limit 100)")
        if filename in filenames:
            raise ValueError(f"duplicate canonical_filename: {filename}")
        filenames.add(filename)
        origin = _text(record.get("origin_key"), f"{identifier} origin_key")
        if origin in origins:
            raise ValueError(f"duplicate included origin_key: {origin}")
        origins.add(origin)
        provenance = record.get("provenance")
        if not isinstance(provenance, dict):
            raise TypeError(f"{identifier}: included documents require provenance")
        for field in ("title", "publisher", "institution", "issuing_unit", "source_url"):
            _text(provenance.get(field), f"{identifier} provenance.{field}")
        if not provenance.get("document_roles") or not provenance.get("applicability"):
            raise ValueError(f"{identifier}: document roles and applicability are required")
        if "applicability_status" not in provenance:
            raise ValueError(f"{identifier}: explicit applicability_status is required")
        metadata = {"origin_key": origin, "acquired_at": acquired,
                    "source_url": provenance["source_url"], "provenance": provenance}
        validated = provenance_from_manifest(metadata)
        if validated.applicability_status == "historical":
            raise ValueError(f"{identifier}: historical applicability cannot be included")
        normalization = record.get("normalization")
        if normalization == "preserve_pdf":
            if original.suffix.lower() != ".pdf" or not filename.endswith(".pdf"):
                raise ValueError(f"{identifier}: preserve_pdf requires PDF paths")
            if not content.startswith(b"%PDF-"):
                raise ValueError(f"{identifier}: input is not a PDF")
            item["canonical_byte_start"] = 0
        elif normalization == "acquisition_wrapper":
            if original.suffix.lower() != ".md" or not filename.endswith(".md"):
                raise ValueError(f"{identifier}: acquisition_wrapper requires Markdown paths")
            wrapper = _WRAPPER.match(content)
            if wrapper is None:
                raise ValueError(f"{identifier}: expected exact acquisition wrapper")
            if (wrapper[1].decode("utf-8") != provenance["source_url"]
                    or wrapper[2].decode("ascii") != acquired):
                raise ValueError(f"{identifier}: acquisition wrapper disagrees with registry")
            item["removed_wrapper"] = content[:wrapper.end()].decode("utf-8")
            item["canonical_byte_start"] = wrapper.end()
            content = content[wrapper.end():]
            content.decode("utf-8")
            if not content.strip():
                raise ValueError(f"{identifier}: Markdown body is empty")
        else:
            raise ValueError(f"{identifier}: unsupported normalization")
        metadata["content_sha256"] = hashlib.sha256(content).hexdigest()
        item["canonical_sha256"] = metadata["content_sha256"]
        documents.append(PreparedDocument(filename, content, metadata))
    includes = {record["id"] for record in records if record["disposition"] == "include"}
    for record in records:
        if record["disposition"] == "alias" and record.get("alias_of") not in includes:
            raise ValueError(f"{record['id']}: alias_of must identify an included document")
        target = record.get("superseded_by")
        if target is not None and (target not in identifiers or target == record["id"]):
            raise ValueError(f"{record['id']}: superseded_by must identify another record")
    if not documents:
        raise ValueError("registry contains no included documents")
    return PreparedBatch(tuple(documents), {
        "schema_version": 1, "registry_sha256": registry_sha256,
        "acquired_at": acquired, "review_notes": registry.get("review_notes", ""),
        "documents": lineage,
    })
