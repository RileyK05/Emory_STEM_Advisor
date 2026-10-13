"""Markdown parsing and safe fixed-renderer previews."""

from __future__ import annotations

import html
import re

from .models import (
    CodeBlock,
    DocumentBlock,
    DocumentSpec,
    HeadingBlock,
    ListBlock,
    ParagraphBlock,
    QuoteBlock,
    TableBlock,
)

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_UNORDERED = re.compile(r"^\s*[-+*]\s+(.*)$")
_ORDERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")


def parse_markdown(source: str, *, title: str = "Untitled") -> DocumentSpec:
    if len(source.encode("utf-8")) > 2_000_000:
        raise ValueError("Markdown input exceeds 2 MiB")
    lines = source.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[DocumentBlock] = []
    paragraph: list[str] = []
    serial = 0

    def add(block_type, **values):
        nonlocal serial
        serial += 1
        blocks.append(block_type(id=f"block-{serial:05d}", **values))

    def flush_paragraph():
        if paragraph:
            add(ParagraphBlock, text="\n".join(paragraph))
            paragraph.clear()

    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            flush_paragraph()
            index += 1
            continue
        if line.startswith((chr(96) * 3, "~~~")):
            flush_paragraph()
            fence = line[:3]
            language = line[3:].strip() or None
            code_lines: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].startswith(fence):
                code_lines.append(lines[index])
                index += 1
            if index >= len(lines):
                raise ValueError("Markdown code fence is not closed")
            add(CodeBlock, text="\n".join(code_lines), language=language)
            index += 1
            continue
        heading = _HEADING.match(line)
        if heading and heading.group(2).strip():
            flush_paragraph()
            add(HeadingBlock, level=len(heading.group(1)), text=heading.group(2))
            index += 1
            continue
        if index + 1 < len(lines) and "|" in line and _SEPARATOR.match(lines[index + 1]):
            flush_paragraph()
            headers = _table_row(line)
            index += 2
            rows = []
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                row = _table_row(lines[index])
                if len(row) != len(headers):
                    raise ValueError("Markdown table row width does not match its header")
                rows.append(row)
                index += 1
            add(TableBlock, headers=headers, rows=rows)
            continue
        unordered = _UNORDERED.match(line)
        ordered = _ORDERED.match(line)
        if unordered or ordered:
            flush_paragraph()
            is_ordered = ordered is not None
            items = []
            while index < len(lines):
                match = (_ORDERED if is_ordered else _UNORDERED).match(lines[index])
                if not match:
                    break
                items.append(match.group(1))
                index += 1
            add(ListBlock, ordered=is_ordered, items=items)
            continue
        if line.startswith(">"):
            flush_paragraph()
            quote_lines = []
            while index < len(lines) and lines[index].startswith(">"):
                quote_lines.append(lines[index][1:].removeprefix(" "))
                index += 1
            add(QuoteBlock, text="\n".join(quote_lines))
            continue
        paragraph.append(line)
        index += 1
    flush_paragraph()
    return DocumentSpec(title=title, blocks=blocks)


def _table_row(line: str) -> list[str]:
    """Split on unescaped pipes and undo the renderer's cell escaping."""
    value = line.strip()
    value = value.removeprefix("|").removesuffix("|")
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in value:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "|":
            cells.append("".join(current))
            current = []
        else:
            current.append(char)
    cells.append("".join(current))
    return [cell.strip().replace("<br>", "\n") for cell in cells]


def render_markdown(spec: DocumentSpec) -> str:
    chunks: list[str] = []
    for block in spec.blocks:
        if isinstance(block, HeadingBlock):
            chunks.append("#" * block.level + " " + block.text)
        elif isinstance(block, ParagraphBlock):
            chunks.append(block.text)
        elif isinstance(block, ListBlock):
            if block.ordered:
                chunks.append("\n".join(f"{i}. {item}" for i, item in enumerate(block.items, 1)))
            else:
                chunks.append("\n".join("- " + item for item in block.items))
        elif isinstance(block, TableBlock):
            chunks.append("| " + " | ".join(_escape_table_cell(c) for c in block.headers) + " |")
            table_lines = ["| " + " | ".join("---" for _ in block.headers) + " |"]
            table_lines.extend(
                "| " + " | ".join(_escape_table_cell(c) for c in row) + " |" for row in block.rows
            )
            chunks.append("\n".join([chunks.pop(), *table_lines]))
        elif isinstance(block, CodeBlock):
            fence = chr(96) * max(3, 1 + max(
                (len(run) for run in re.findall(r"\x60+", block.text)), default=0,
            ))
            chunks.append(fence + (block.language or "") + "\n" + block.text + "\n" + fence)
        elif isinstance(block, QuoteBlock):
            chunks.append("\n".join("> " + line for line in block.text.split("\n")))
    if spec.evidence:
        from app.artifacts.evidence import source_legend

        chunks.append("## Sources")
        for element, source, locator, chunk_id in source_legend(spec):
            text = f"{element}: {source} · {locator} ({chunk_id})"
            text = re.sub(r"([\\`*_{}\[\]<>#])", r"\\\1", text)
            chunks.append("- " + text.replace("\n", " ").replace("\r", " "))
    return "\n\n".join(chunks) + "\n"


def _escape_table_cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def _inline_html(value: str) -> str:
    escaped = html.escape(value, quote=False)
    escaped = re.sub(r"\x60([^\x60]+)\x60", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", escaped)
    return escaped.replace("\n", "<br>\n")


def render_document_html(spec: DocumentSpec) -> str:
    """Render document blocks with a fixed, escaped HTML renderer."""
    body: list[str] = []
    for block in spec.blocks:
        if isinstance(block, HeadingBlock):
            body.append(f"<h{block.level}>{_inline_html(block.text)}</h{block.level}>")
        elif isinstance(block, ParagraphBlock):
            body.append("<p>" + _inline_html(block.text) + "</p>")
        elif isinstance(block, ListBlock):
            tag = "ol" if block.ordered else "ul"
            body.append(
                f"<{tag}>"
                + "".join("<li>" + _inline_html(i) + "</li>" for i in block.items)
                + f"</{tag}>"
            )
        elif isinstance(block, TableBlock):
            head = "".join("<th>" + _inline_html(c) + "</th>" for c in block.headers)
            rows = "".join(
                "<tr>" + "".join("<td>" + _inline_html(c) + "</td>" for c in row) + "</tr>"
                for row in block.rows
            )
            body.append(
                "<table><thead><tr>" + head + "</tr></thead><tbody>" + rows + "</tbody></table>"
            )
        elif isinstance(block, CodeBlock):
            body.append("<pre><code>" + html.escape(block.text) + "</code></pre>")
        elif isinstance(block, QuoteBlock):
            body.append("<blockquote>" + _inline_html(block.text) + "</blockquote>")
    title = html.escape(spec.title)
    if spec.evidence:
        from app.artifacts.evidence import source_legend

        body.append("<section><h2>Sources</h2><ul>" + "".join(
            "<li>" + html.escape(f"{element}: {source} · {locator} ({chunk_id})") + "</li>"
            for element, source, locator, chunk_id in source_legend(spec)
        ) + "</ul></section>")
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'
        + title
        + "</title></head><body><main>"
        + "\n".join(body)
        + "</main></body></html>"
    )
