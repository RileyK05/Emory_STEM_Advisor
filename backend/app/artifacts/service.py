"""Format dispatch for preview/import/export, without persistence or HTTP concerns."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from .docx import ImportResult as DocxImportResult
from .docx import export_docx, import_docx
from .errors import ArtifactError
from .markdown import parse_markdown, render_document_html, render_markdown
from .models import DocumentSpec, HtmlArtifactSpec, WorkbookSpec
from .xlsx import (
    IsolatedLibreOfficeRunner,
    WorkbookImportResult,
    export_xlsx,
    formula_preview,
    import_xlsx,
    recalculate_xlsx,
)

MAX_OFFICE_PDF_BYTES = 32 * 1024 * 1024

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


@dataclass(frozen=True)
class PdfPreviewResult:
    filename: str
    content: bytes
    recalculation_status: str = "not_required"


class OfficePreviewRunner(IsolatedLibreOfficeRunner):
    def render_docx_pdf(self, docx_bytes: bytes, *, timeout_seconds: int = 60) -> bytes: ...

    def render_xlsx_pdf(self, xlsx_bytes: bytes, *, timeout_seconds: int = 60) -> bytes: ...


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


def import_artifact(
    data: bytes, filename: str
) -> DocxImportResult | WorkbookImportResult | DocumentSpec:
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix == "docx":
        return import_docx(data)
    if suffix == "xlsx":
        return import_xlsx(data)
    if suffix in {"md", "markdown"}:
        return parse_markdown(data.decode("utf-8-sig"))
    raise ArtifactError(
        "unsupported_format", "Supported import formats are Markdown, DOCX, and XLSX."
    )


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


def render_artifact_pdf(
    spec: DocumentSpec | WorkbookSpec | HtmlArtifactSpec,
    *,
    office_runner: OfficePreviewRunner | None,
    timeout_seconds: int = 60,
) -> PdfPreviewResult:
    """Render a chosen artifact revision to PDF through the isolated Office seam."""
    if office_runner is None:
        raise ArtifactError(
            "office_unavailable", "Isolated Office preview is not configured on this server."
        )
    if not 1 <= timeout_seconds <= 300:
        raise ValueError("timeout_seconds must be between 1 and 300")
    status = "not_required"
    try:
        if isinstance(spec, DocumentSpec):
            filename = safe_filename(spec.title, "docx").removesuffix(".docx") + ".pdf"
            content = office_runner.render_docx_pdf(export_docx(spec), timeout_seconds=timeout_seconds)
        elif isinstance(spec, WorkbookSpec):
            xlsx = export_xlsx(spec)
            calculated = recalculate_xlsx(xlsx, spec, runner=office_runner,
                                          timeout_seconds=timeout_seconds)
            status = calculated.status
            has_formulas = any(c.formula is not None for sheet in spec.sheets for c in sheet.cells)
            if has_formulas and status != "verified":
                raise ArtifactError(
                    "calculation_unverified",
                    "Workbook preview is unavailable until formula results are verified.",
                )
            filename = safe_filename(spec.title, "xlsx").removesuffix(".xlsx") + ".pdf"
            content = office_runner.render_xlsx_pdf(
                calculated.xlsx_bytes, timeout_seconds=timeout_seconds
            )
        else:
            raise ArtifactError(
                "format_mismatch", "Only document and workbook artifacts can be rendered as PDF."
            )
    except ArtifactError:
        raise
    except Exception as exc:
        raise ArtifactError("office_failed", "Isolated Office preview could not be completed.") from exc
    if not isinstance(content, bytes) or not content.startswith(b"%PDF-"):
        raise ArtifactError("office_failed", "Isolated Office preview returned an invalid PDF.")
    if len(content) > MAX_OFFICE_PDF_BYTES:
        raise ArtifactError("artifact_limit", "Rendered PDF exceeds the configured output limit.")
    return PdfPreviewResult(filename, content, status)


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
        return {"kind": "html", "title": spec.title, "html": spec.html, "sandboxRequired": True}
    raise ArtifactError("invalid_spec", "Unsupported artifact spec")
