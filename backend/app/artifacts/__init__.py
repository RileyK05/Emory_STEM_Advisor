"""Native, validated artifact formats. Persistence and sandbox integration live at API edges."""

from .docx import export_docx
from .errors import ArtifactError, ArtifactLimitError, UnsupportedFeatureError
from .markdown import render_document_html, render_markdown
from .models import (
    ChartBlock,
    CodeBlock,
    DiagramBlock,
    DocumentSpec,
    HeadingBlock,
    HtmlArtifactSpec,
    ListBlock,
    ParagraphBlock,
    QuoteBlock,
    TableBlock,
    WorkbookCell,
    WorkbookChart,
    WorkbookSheet,
    WorkbookSpec,
)
from .service import (
    ExportResult,
    export_artifact,
    preview_artifact,
    safe_filename,
)
from .xlsx import (
    IsolatedLibreOfficeRunner,
    RecalculationResult,
    export_xlsx,
    formula_preview,
    recalculate_xlsx,
)

__all__ = (
    "ArtifactError",
    "ArtifactLimitError",
    "ChartBlock",
    "CodeBlock",
    "DiagramBlock",
    "DocumentSpec",
    "ExportResult",
    "HeadingBlock",
    "HtmlArtifactSpec",
    "IsolatedLibreOfficeRunner",
    "ListBlock",
    "ParagraphBlock",
    "QuoteBlock",
    "RecalculationResult",
    "TableBlock",
    "UnsupportedFeatureError",
    "WorkbookCell",
    "WorkbookChart",
    "WorkbookSheet",
    "WorkbookSpec",
    "export_artifact",
    "export_docx",
    "export_xlsx",
    "formula_preview",
    "preview_artifact",
    "recalculate_xlsx",
    "render_document_html",
    "render_markdown",
    "safe_filename",
)
