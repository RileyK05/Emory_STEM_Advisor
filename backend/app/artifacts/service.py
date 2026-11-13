"""Format dispatch for preview and export, without persistence or HTTP concerns."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from .docx import export_docx
from .errors import ArtifactError
from .markdown import document_kicker, render_document_html, render_markdown
from .models import DocumentSpec, HtmlArtifactSpec, WorkbookSpec
from .theme import themed_page
from .xlsx import (
    IsolatedLibreOfficeRunner,
    export_xlsx,
    formula_preview,
    recalculate_xlsx,
)

MIME_TYPES = {
    "md": "text/markdown; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "html": "text/html; charset=utf-8",
}


@dataclass(frozen=True)
class ExportResult:
    filename: str
    mime_type: str
    content: bytes
    recalculation_status: str = "not_required"
    message: str | None = None


def safe_filename(title: str, extension: str) -> str:
    if extension not in MIME_TYPES:
        raise ValueError("unsupported export extension")
    normalized = unicodedata.normalize("NFKD", title)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-.")[:80].rstrip("-.")
    if not slug:
        slug = "artifact"
    if slug.upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        slug = "artifact-" + slug.lower()
    return f"{slug}.{extension}"


def export_artifact(
    spec: DocumentSpec | WorkbookSpec | HtmlArtifactSpec,
    extension: str,
    *,
    office_runner: IsolatedLibreOfficeRunner | None = None,
    office_timeout_seconds: int = 60,
) -> ExportResult:
    extension = extension.lower().removeprefix(".")
    if extension not in MIME_TYPES:
        raise ArtifactError(
            "unsupported_format", "Supported export formats are Markdown, DOCX, XLSX, and HTML."
        )
    status, message = "not_required", None
    if isinstance(spec, DocumentSpec):
        if extension == "md":
            content = render_markdown(spec).encode("utf-8")
        elif extension == "docx":
            content = export_docx(spec)
        elif extension == "html":
            content = render_document_html(spec).encode("utf-8")
        else:
            raise ArtifactError(
                "format_mismatch", "A document spec exports as .md, .docx, or .html."
            )
    elif isinstance(spec, WorkbookSpec):
        if extension != "xlsx":
            raise ArtifactError("format_mismatch", "A workbook spec exports as .xlsx.")
        raw = export_xlsx(spec)
        recalculation = recalculate_xlsx(
            raw, spec, runner=office_runner, timeout_seconds=office_timeout_seconds
        )
        content, status, message = (
            recalculation.xlsx_bytes,
            recalculation.status,
            recalculation.message,
        )
    elif isinstance(spec, HtmlArtifactSpec):
        if extension != "html":
            raise ArtifactError("format_mismatch", "An HTML artifact exports as .html.")
        from .active_html import render_active_html
        content = render_active_html(spec).encode("utf-8")
    else:  # pragma: no cover - type guards at application API boundary
        raise ArtifactError("invalid_spec", "Unsupported artifact spec")
    return ExportResult(
        safe_filename(spec.title, extension), MIME_TYPES[extension], content, status, message
    )


def preview_artifact(spec: DocumentSpec | WorkbookSpec | HtmlArtifactSpec) -> dict[str, Any]:
    if isinstance(spec, DocumentSpec):
        return {
            "kind": "document",
            "title": spec.title,
            "html": render_document_html(spec),
            "sandboxRequired": False,
        }
    if isinstance(spec, WorkbookSpec):
        return {"kind": "workbook", **formula_preview(spec)}
    if isinstance(spec, HtmlArtifactSpec):
        # The page sits in the shared look; the preview still sanitizes it.
        return {"kind": "html", "title": spec.title,
                "html": themed_page(spec.html, title=spec.title, kicker=document_kicker(spec)),
                "sandboxRequired": True}
    raise ArtifactError("invalid_spec", "Unsupported artifact spec")
