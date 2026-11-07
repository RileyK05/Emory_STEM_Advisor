"""DOCX export for the explicitly supported text structures."""

from __future__ import annotations

import io
import re

from docx import Document as WordDocument
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from .models import (
    CodeBlock,
    DocumentSpec,
    HeadingBlock,
    ListBlock,
    ParagraphBlock,
    QuoteBlock,
    TableBlock,
)


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


def export_docx(spec: DocumentSpec) -> bytes:
    document = WordDocument()
    for name in ("Title", "Subtitle", *(f"Heading {level}" for level in range(1, 10))):
        style = document.styles[name]
        style.font.color.rgb = RGBColor(0, 0, 0)
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
            table = document.add_table(rows=1, cols=len(block.headers))
            table.style = "Light Shading Accent 1"
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            for cell, text in zip(table.rows[0].cells, block.headers, strict=True):
                _add_inline(cell.paragraphs[0], text)
                for run in cell.paragraphs[0].runs:
                    run.bold = True
            for row in block.rows:
                cells = table.add_row().cells
                for cell, text in zip(cells, row, strict=True):
                    _add_inline(cell.paragraphs[0], text)
    if spec.evidence:
        from app.artifacts.evidence import source_legend

        document.add_paragraph("Sources", style="Heading 1")
        for element, source, locator, chunk_id in source_legend(spec):
            document.add_paragraph(f"{element}: {source} · {locator} ({chunk_id})")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
