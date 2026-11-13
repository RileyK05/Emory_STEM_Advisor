"""DOCX export for the explicitly supported text structures."""

from __future__ import annotations

import io
import re

from docx import Document as WordDocument
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from .models import (
    ChartBlock,
    CodeBlock,
    DiagramBlock,
    DocumentSpec,
    HeadingBlock,
    ListBlock,
    ParagraphBlock,
    QuoteBlock,
    TableBlock,
)
from .visuals import chart_rows, diagram_columns, diagram_edges_text


def _add_inline(paragraph, text: str) -> None:
    """Render the small supported inline subset emitted by Markdown/DOCX import."""
    pattern = re.compile(
        r"(\*\*.+?\*\*|(?<!\*)\*[^*]+\*(?!\*)|" + chr(96) + r"[^" + chr(96) + r"]+" + chr(96) + r")"
    )
    position = 0
    for match in pattern.finditer(text):
        if match.start() > position:
            paragraph.add_run(text[position : match.start()])
        token = match.group(0)
        if token.startswith("**"):
            run = paragraph.add_run(token[2:-2])
            run.bold = True
        elif token.startswith("*"):
            run = paragraph.add_run(token[1:-1])
            run.italic = True
        else:
            run = paragraph.add_run(token[1:-1])
            run.font.name = "Consolas"
        position = match.end()
    if position < len(text):
        paragraph.add_run(text[position:])


# The shared theme's navy, as the HTML renderer uses it.
NAVY = RGBColor(0x01, 0x21, 0x69)


def _shade(cell, fill: str) -> None:
    shading = OxmlElement("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), fill)
    cell._tc.get_or_add_tcPr().append(shading)


def _add_table(document, headers, rows) -> None:
    table = document.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for cell, text in zip(table.rows[0].cells, headers, strict=True):
        _add_inline(cell.paragraphs[0], text)
        _shade(cell, "012169")
        for run in cell.paragraphs[0].runs:
            run.bold = True
            run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    for row in rows:
        cells = table.add_row().cells
        for cell, text in zip(cells, row, strict=True):
            _add_inline(cell.paragraphs[0], text)


def export_docx(spec: DocumentSpec) -> bytes:
    document = WordDocument()
    document.styles["Normal"].font.name = "Calibri"
    document.styles["Normal"].font.size = Pt(11)
    for name in ("Title", "Subtitle", *(f"Heading {level}" for level in range(1, 10))):
        style = document.styles[name]
        style.font.color.rgb = NAVY
        properties = style.element.pPr
        if properties is not None:
            for border in list(properties.findall(qn("w:pBdr"))):
                properties.remove(border)
    document.core_properties.title = spec.title
    section = document.sections[0]
    section.page_width = Inches(spec.page_layout.width_inches)
    section.page_height = Inches(spec.page_layout.height_inches)
    section.top_margin = Inches(spec.page_layout.margin_top_inches)
    section.bottom_margin = Inches(spec.page_layout.margin_bottom_inches)
    section.left_margin = Inches(spec.page_layout.margin_left_inches)
    section.right_margin = Inches(spec.page_layout.margin_right_inches)
    title = document.add_paragraph(spec.title, style="Title")
    properties = title._p.pPr
    if properties is not None:
        for border in list(properties.findall(qn("w:pBdr"))):
            properties.remove(border)
    for block in spec.blocks:
        if isinstance(block, HeadingBlock):
            _add_inline(document.add_paragraph(style=f"Heading {block.level}"), block.text)
        elif isinstance(block, ParagraphBlock):
            para = document.add_paragraph()
            _add_inline(para, block.text)
        elif isinstance(block, ListBlock):
            style = "List Number" if block.ordered else "List Bullet"
            for item in block.items:
                _add_inline(document.add_paragraph(style=style), item)
        elif isinstance(block, QuoteBlock):
            _add_inline(document.add_paragraph(style="Quote"), block.text)
        elif isinstance(block, CodeBlock):
            para = document.add_paragraph()
            run = para.add_run(block.text)
            run.font.name = "Consolas"
            run.font.size = Pt(9)
        elif isinstance(block, TableBlock):
            _add_table(document, block.headers, block.rows)
        elif isinstance(block, DiagramBlock):
            # Word gets the same facts as text: who sits in each column, then each arrow.
            document.add_paragraph().add_run(block.title).bold = True
            columns = diagram_columns(block)
            _add_table(document, [label for label, _nodes in columns],
                       [[", ".join(node.label for node in nodes) for _label, nodes in columns]])
            edges = diagram_edges_text(block)
            if edges:
                document.add_paragraph("Connections:")
                for edge in edges:
                    document.add_paragraph(edge, style="List Bullet")
            if block.caption:
                document.add_paragraph(block.caption)
        elif isinstance(block, ChartBlock):
            document.add_paragraph().add_run(block.title).bold = True
            headers, rows = chart_rows(block)
            _add_table(document, headers, rows)
            if block.caption:
                document.add_paragraph(block.caption)
    if spec.evidence:
        from app.artifacts.evidence import source_legend

        document.add_paragraph("Sources", style="Heading 1")
        for source, locator in source_legend(spec):
            document.add_paragraph(f"{source} · {locator}")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
