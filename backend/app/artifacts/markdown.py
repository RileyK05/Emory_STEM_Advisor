"""Markdown parsing and safe fixed-renderer previews."""

from __future__ import annotations

import html
import re

from .models import (
    CodeBlock,
    DocumentSpec,
    HeadingBlock,
    ListBlock,
    ParagraphBlock,
    QuoteBlock,
    TableBlock,
)


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
