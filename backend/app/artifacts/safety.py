"""Defensive Office container inspection performed before any parser opens a file."""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

from defusedxml import ElementTree as SafeET

from .errors import ArtifactLimitError, UnsupportedFeatureError

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 2_000
MAX_TOTAL_UNCOMPRESSED = 50 * 1024 * 1024
MAX_ENTRY_UNCOMPRESSED = 12 * 1024 * 1024
MAX_COMPRESSION_RATIO = 150


@dataclass(frozen=True)
class OfficeArchive:
    names: frozenset[str]
    total_uncompressed: int


def inspect_office_archive(data: bytes, *, kind: str) -> OfficeArchive:
    """Validate container bounds and unsafe paths before python-docx/openpyxl parse it."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise ArtifactLimitError(f"Office file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError("Office file is not a valid ZIP container") from exc

    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_ENTRIES:
        raise ArtifactLimitError("Office file contains too many archive entries.")
    total = 0
    names: set[str] = set()
    for info in infos:
        name = info.filename.replace("\\", "/")
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or name.startswith("/"):
            raise ValueError("Office archive contains an unsafe path")
        if name in names:
            raise ValueError("Office archive contains duplicate paths")
        names.add(name)
        if info.file_size > MAX_ENTRY_UNCOMPRESSED:
            raise ArtifactLimitError(f"Office archive entry is too large: {name}")
        total += info.file_size
        if total > MAX_TOTAL_UNCOMPRESSED:
            raise ArtifactLimitError("Office archive expands beyond the allowed size.")
        if info.file_size and info.compress_size == 0:
            raise ArtifactLimitError("Office archive has an invalid compression ratio.")
        if info.compress_size and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
            raise ArtifactLimitError(
                f"Office archive entry has an extreme compression ratio: {name}"
            )

    features: list[str] = []
    # Defused parsing rejects DTDs/entities before Office libraries parse the package.
    for name in sorted(n for n in names if n.lower().endswith((".xml", ".rels"))):
        try:
            SafeET.fromstring(read_bounded_entry(data, name))
        except ArtifactLimitError:
            raise
        except Exception as exc:
            raise ValueError(f"Office archive contains unsafe or invalid XML: {name}") from exc
    lower_names = {name.lower() for name in names}
    if any("vbaproject.bin" in name or "macrosheets/" in name for name in lower_names):
        features.append("macro-enabled content")
    if any(name.startswith("xl/externallinks/") for name in lower_names):
        features.append("external workbook links")
    if any("embeddings/" in name or "oleobject" in name for name in lower_names):
        features.append("embedded objects")
    if kind == "docx":
        if any(name.startswith("word/media/") for name in lower_names):
            features.append("embedded images")
        if any("word/charts/" in name or "word/diagrams/" in name for name in lower_names):
            features.append("embedded charts or diagrams")
        if any(
            name in {"word/comments.xml", "word/footnotes.xml", "word/endnotes.xml"}
            for name in lower_names
        ):
            features.append("comments or notes")
    elif kind == "xlsx":
        if any(name.startswith("xl/charts/") for name in lower_names):
            features.append("existing charts")
        if any(name.startswith("xl/pivot") or "/pivot" in name for name in lower_names):
            features.append("pivot tables or pivot caches")
        if any(name.startswith(("xl/drawings/", "xl/media/")) for name in lower_names):
            features.append("drawings or images")
        if any(name.startswith("xl/tables/") for name in lower_names):
            features.append("Excel tables")
        if any(
            "connections.xml" in name or "querytables/" in name or "slicer" in name
            for name in lower_names
        ):
            features.append("external data connections or slicers")
        if any(
            name.startswith("xl/comments") or "threadedcomments" in name for name in lower_names
        ):
            features.append("cell comments")
    else:
        raise ValueError("kind must be docx or xlsx")

    for name in names:
        if name.endswith(".rels"):
            try:
                rel_xml = archive.read(name)
            except (KeyError, OSError, zipfile.BadZipFile) as exc:
                raise ValueError("Office relationships XML is unreadable") from exc
            try:
                root = SafeET.fromstring(rel_xml)
            except Exception as exc:
                raise ValueError("Office relationships XML is unsafe or invalid") from exc
            if any(
                (value or "").casefold() == "external"
                for element in root.iter()
                for key, value in element.attrib.items()
                if key.rsplit("}", 1)[-1].casefold() == "targetmode"
            ):
                features.append("external relationships")
                break
    if features:
        raise UnsupportedFeatureError(sorted(set(features)))
    return OfficeArchive(frozenset(names), total)


def read_bounded_entry(data: bytes, name: str, *, limit: int = MAX_ENTRY_UNCOMPRESSED) -> bytes:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        try:
            info = archive.getinfo(name)
        except KeyError:
            # A missing required part is a malformed upload, not a server fault.
            raise ValueError(f"Office archive is missing a required part: {name}") from None
        if info.file_size > limit:
            raise ArtifactLimitError(f"Office XML part is too large: {name}")
        chunks: list[bytes] = []
        total = 0
        with archive.open(info) as source:
            while True:
                block = source.read(64 * 1024)
                if not block:
                    break
                total += len(block)
                if total > limit:
                    raise ArtifactLimitError(f"Office XML part is too large: {name}")
                chunks.append(block)
        return b"".join(chunks)
