"""Markdown parsing and safe fixed-renderer previews."""

from __future__ import annotations

import html
import re

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
from .theme import themed_page
from .visuals import chart_html, chart_rows, diagram_html, diagram_mermaid


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
            elif all(item.startswith(CHECKBOX) for item in block.items):
                # A checklist is a Markdown task list.
                chunks.append("\n".join("- [ ] " + item[len(CHECKBOX):] for item in block.items))
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
        elif isinstance(block, DiagramBlock):
            chunks.append(f"**{block.title}**")
            chunks.append("```mermaid\n" + diagram_mermaid(block) + "\n```")
            if block.caption:
                chunks.append(block.caption)
        elif isinstance(block, ChartBlock):
            headers, rows = chart_rows(block)
            chunks.append(f"**{block.title}**")
            chunks.append("\n".join([
                "| " + " | ".join(_escape_table_cell(c) for c in headers) + " |",
                "| " + " | ".join("---" for _ in headers) + " |",
                *("| " + " | ".join(_escape_table_cell(c) for c in row) + " |" for row in rows)]))
            if block.caption:
                chunks.append(block.caption)
    if spec.evidence:
        from app.artifacts.evidence import source_legend

        chunks.append("## Sources")
        for source, locator in source_legend(spec):
            text = f"{source} · {locator}"
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


CHECKBOX = "☐ "
# Kicker text over the title, by what built the document.
_KICKERS = (("whatif_programs", "What if you switched"), ("plan", "Term plan"))


def _list_html(ordered: bool, items: list[str]) -> str:
    if not ordered and items and all(item.startswith(CHECKBOX) for item in items):
        return ('<ul class="checklist">'
                + "".join("<li>" + _inline_html(item[len(CHECKBOX):]) + "</li>" for item in items)
                + "</ul>")
    tag = "ol" if ordered else "ul"
    return f"<{tag}>" + "".join("<li>" + _inline_html(i) + "</li>" for i in items) + f"</{tag}>"


def _block_html(block) -> str:
    if isinstance(block, HeadingBlock):
        return f"<h{block.level}>{_inline_html(block.text)}</h{block.level}>"
    if isinstance(block, ParagraphBlock):
        return "<p>" + _inline_html(block.text) + "</p>"
    if isinstance(block, TableBlock):
        head = "".join("<th>" + _inline_html(c) + "</th>" for c in block.headers)
        rows = "".join(
            "<tr>" + "".join("<td>" + _inline_html(c) + "</td>" for c in row) + "</tr>"
            for row in block.rows)
        return "<table><thead><tr>" + head + "</tr></thead><tbody>" + rows + "</tbody></table>"
    if isinstance(block, CodeBlock):
        return "<pre><code>" + html.escape(block.text) + "</code></pre>"
    if isinstance(block, QuoteBlock):
        return "<blockquote>" + _inline_html(block.text) + "</blockquote>"
    if isinstance(block, DiagramBlock):
        return diagram_html(block)
    if isinstance(block, ChartBlock):
        return chart_html(block)
    return ""


def render_blocks_html(blocks) -> str:
    """Blocks as themed sections: each level-1 or level-2 heading opens a card.

    Consecutive bullet blocks join one list, so a plan's one-course blocks read
    as one term, and a list of "☐ " items becomes a checklist.
    """
    cards: list[list[str]] = [[]]
    pending: tuple[bool, list[str]] | None = None

    def flush():
        nonlocal pending
        if pending is not None:
            cards[-1].append(_list_html(*pending))
            pending = None

    for block in blocks:
        if isinstance(block, ListBlock):
            if pending is not None and pending[0] == block.ordered:
                pending[1].extend(block.items)
            else:
                flush()
                pending = (block.ordered, list(block.items))
            continue
        flush()
        if isinstance(block, HeadingBlock) and block.level <= 2 and cards[-1]:
            cards.append([])
        cards[-1].append(_block_html(block))
    flush()
    return "".join('<section class="card">' + "".join(card) + "</section>"
                   for card in cards if card)


def _sources_html(spec) -> str:
    from app.artifacts.evidence import source_legend

    return ('<section class="card sources"><h2>Sources</h2><ul>' + "".join(
        "<li>" + html.escape(f"{source} · {locator}") + "</li>"
        for source, locator in source_legend(spec)) + "</ul></section>")


def document_kicker(spec) -> str | None:
    provenance = spec.provenance or {}
    if provenance.get("artifact_label"):
        return str(provenance["artifact_label"])
    return next((label for key, label in _KICKERS if provenance.get(key)), None)


def render_document_html(spec: DocumentSpec) -> str:
    """Render document blocks with a fixed, escaped HTML renderer in the shared theme."""
    body = render_blocks_html(spec.blocks) + (_sources_html(spec) if spec.evidence else "")
    subtitle = (spec.provenance or {}).get("program_title")
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>'
        + html.escape(spec.title)
        + "</title></head><body style=\"margin:0\">"
        + themed_page(body, title=spec.title, kicker=document_kicker(spec),
                      subtitle=str(subtitle) if subtitle else None)
        + "</body></html>"
    )
