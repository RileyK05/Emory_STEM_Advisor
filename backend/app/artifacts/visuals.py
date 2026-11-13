"""Fixed renderers for diagram and chart blocks.

Every shape here is drawn by code from validated block data: positions,
colors and text are computed, never copied from a model. The SVG uses only
plain shapes, text and an internal arrow marker, so it stays inert in the
sandboxed preview and in exported pages. Each drawing carries its own light
card, so it reads the same inside a light or a dark page.
"""

from __future__ import annotations

import html
import math
import re
import textwrap

from .models import ChartBlock, DiagramBlock

# A diagram reads top to bottom, one banded row per group (a term), so it fits
# a portrait page and grows in height rather than shrinking its text.
NODE_WIDTH = 176
NODE_HEIGHT = 58
NODE_GAP = 14
ROW_GAP = 34
LABEL_WIDTH = 120
PADDING = 16
LEGEND_HEIGHT = 30

INK = "rgb(20,33,61)"
MUTED = "rgb(91,100,116)"
LINE = "rgb(220,227,238)"
EDGE = "rgb(148,163,184)"
BAND = "rgb(245,247,251)"

# Accent stripe, node fill and the key's words per status.
_STATUS_STYLE = {
    "done": ("rgb(46,125,50)", "rgb(241,248,242)", "Done"),
    "next": ("rgb(242,169,0)", "rgb(255,248,230)", "Take next"),
    "planned": ("rgb(1,33,105)", "rgb(255,255,255)", "Planned"),
    "blocked": ("rgb(179,38,30)", "rgb(253,241,240)", "Ask an advisor"),
    None: ("rgb(156,163,175)", "rgb(255,255,255)", ""),
}
_SERIES_COLORS = ("rgb(1,33,105)", "rgb(242,169,0)", "rgb(199,206,217)", "rgb(46,125,50)", "rgb(109,91,208)", "rgb(179,38,30)",
                  "rgb(14,116,144)", "rgb(107,114,128)")
# Text that sits on a series color: dark on the light fills, white on the dark ones.
_ON_COLOR = {"rgb(242,169,0)": INK, "rgb(199,206,217)": INK}
_CODE = re.compile(r"^([A-Z]{2,8}(?:_OX)?\s\d{3}[A-Z]{0,3})\s+(.+)$")


def _esc(value: str) -> str:
    return html.escape(str(value), quote=True)


_ARROW = ('<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
          'markerHeight="6" orient="auto-start-reverse"><path d="M0,1 L9,5 L0,9 z" '
          f'fill="{EDGE}"/></marker></defs>')


def _svg_open(width: float, height: float, title: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            f'width="{width}" height="{height}" role="img" aria-label="{_esc(title)}" '
            'style="max-width:100%;height:auto;font-family:system-ui,-apple-system,'
            '&quot;Segoe UI&quot;,Roboto,sans-serif">'
            f"<title>{_esc(title)}</title>"
            f'<rect x="0" y="0" width="{width}" height="{height}" rx="10" fill="rgb(255,255,255)"/>')


def _text(x, y, value, *, size=12, weight=None, fill=INK, anchor=None) -> str:
    return (f'<text x="{x}" y="{y}" font-size="{size}"'
            + (f' font-weight="{weight}"' if weight else "")
            + (f' text-anchor="{anchor}"' if anchor else "")
            + f' fill="{fill}">{_esc(value)}</text>')


def diagram_columns(block: DiagramBlock) -> list[tuple[str, list]]:
    """Nodes in reading order: by group when given, otherwise by dependency depth."""
    if block.groups:
        columns = [(group.label, [n for n in block.nodes if n.group == group.id])
                   for group in block.groups]
        loose = [n for n in block.nodes if n.group is None]
        if loose:
            columns.append(("Other", loose))
        return [(label, nodes) for label, nodes in columns if nodes]
    depth = {node.id: 0 for node in block.nodes}
    # Longest path from a source; a cycle stops after one pass per node.
    for _ in range(len(block.nodes)):
        changed = False
        for edge in block.edges:
            if depth[edge.target] < depth[edge.source] + 1 <= len(block.nodes):
                depth[edge.target] = depth[edge.source] + 1
                changed = True
        if not changed:
            break
    layers: dict[int, list] = {}
    for node in block.nodes:
        layers.setdefault(depth[node.id], []).append(node)
    return [(f"Step {index + 1}", layers[key]) for index, key in enumerate(sorted(layers))]


def _node_text(label: str) -> tuple[str, list[str]]:
    """A course node shows its code on top and up to two lines of title under it."""
    match = _CODE.match(label)
    if match:
        head, rest = match.groups()
    else:
        head, rest = label, ""
        if len(label) > 24:
            head, rest = textwrap.wrap(label, 24, max_lines=1, placeholder="…")[0], ""
    lines = textwrap.wrap(rest, 26, max_lines=2, placeholder="…") if rest else []
    return head, lines


def diagram_svg(block: DiagramBlock) -> str:
    rows = diagram_columns(block)
    notes = {group.label: group.note for group in block.groups if group.note}
    positions: dict[str, tuple[float, float]] = {}
    row_of: dict[str, int] = {}
    for row, (_label, nodes) in enumerate(rows):
        for node in nodes:
            row_of[node.id] = row
    widest = max(len(nodes) for _label, nodes in rows)
    left = PADDING + LABEL_WIDTH
    content_right = left + widest * NODE_WIDTH + (widest - 1) * NODE_GAP
    # An edge that skips a row would run behind that row's boxes and look like
    # it starts there, so it travels in a gutter on the right instead.
    long_edges = [edge for edge in block.edges
                  if abs(row_of[edge.target] - row_of[edge.source]) > 1
                  or row_of[edge.target] < row_of[edge.source]]
    width = content_right + PADDING + (14 + 8 * len(long_edges) if long_edges else 0)
    legend = diagram_legend(block)
    top = PADDING + (LEGEND_HEIGHT if legend else 0)
    height = top + len(rows) * NODE_HEIGHT + (len(rows) - 1) * ROW_GAP + PADDING
    parts = [_svg_open(width, height, block.title), _ARROW]
    # The key: one chip per status the drawing uses.
    x = PADDING
    for status in dict.fromkeys(node.status for node in block.nodes if node.status):
        stripe, fill, name = _STATUS_STYLE[status]
        parts.append(f'<rect x="{x}" y="{PADDING}" width="14" height="14" rx="3" '
                     f'fill="{fill}" stroke="{stripe}" stroke-width="2"/>')
        parts.append(_text(x + 20, PADDING + 11.5, name, size=12, fill=MUTED))
        x += 34 + 7 * len(name)
    for row, (label, nodes) in enumerate(rows):
        y = top + row * (NODE_HEIGHT + ROW_GAP)
        parts.append(f'<rect x="{PADDING / 2}" y="{y - ROW_GAP / 2 + 4}" '
                     f'width="{width - PADDING}" height="{NODE_HEIGHT + ROW_GAP - 8}" rx="10" '
                     f'fill="{BAND if row % 2 == 0 else "rgb(255,255,255)"}"/>')
        note = notes.get(label)
        label_y = y + NODE_HEIGHT / 2 + (0 if note else 4)
        parts.append(_text(PADDING + 4, label_y, label, size=13, weight=700))
        if note:
            parts.append(_text(PADDING + 4, label_y + 16, note, size=11, fill=MUTED))
        for index, node in enumerate(nodes):
            positions[node.id] = (left + index * (NODE_WIDTH + NODE_GAP), y)
    for edge in block.edges:
        sx, sy = positions[edge.source]
        tx, ty = positions[edge.target]
        x1, x2 = sx + NODE_WIDTH / 2, tx + NODE_WIDTH / 2
        if edge in long_edges:
            lane = long_edges.index(edge)
            gutter = content_right + 14 + 8 * lane
            y1 = sy + NODE_HEIGHT
            leave = y1 + ROW_GAP / 2 - 3 * lane
            arrive = ty - ROW_GAP / 2 + 3 * lane
            path = (f"M{x1},{y1} L{x1},{leave} L{gutter},{leave} L{gutter},{arrive} "
                    f"L{x2},{arrive} L{x2},{ty}")
        elif sy == ty:
            # Same row (a corequisite pair): loop underneath so the arrow shows.
            y1 = y2 = sy + NODE_HEIGHT
            bend = y1 + ROW_GAP * 0.6
            path = f"M{x1},{y1} C{x1},{bend} {x2},{bend} {x2},{y2}"
        else:
            # The next row down: a short curve through the gap between rows.
            y1, y2 = sy + NODE_HEIGHT, ty
            middle = (y1 + y2) / 2
            path = f"M{x1},{y1} C{x1},{middle} {x2},{middle} {x2},{y2}"
        parts.append(f'<path d="{path}" fill="none" stroke="{EDGE}" stroke-width="1.6" '
                     'marker-end="url(#arrow)"/>')
    for node in block.nodes:
        x, y = positions[node.id]
        stripe, fill, _name = _STATUS_STYLE[node.status]
        dash = ' stroke-dasharray="5 3"' if node.status == "blocked" else ""
        parts.append(f'<rect x="{x}" y="{y}" width="{NODE_WIDTH}" height="{NODE_HEIGHT}" '
                     f'rx="9" fill="{fill}" stroke="{LINE}" stroke-width="1.2"{dash}/>')
        parts.append(f'<rect x="{x}" y="{y + 8}" width="4" height="{NODE_HEIGHT - 16}" '
                     f'rx="2" fill="{stripe}"/>')
        head, lines = _node_text(node.label)
        first = y + (19 if lines else NODE_HEIGHT / 2 + 5)
        parts.append(_text(x + 14, first, head, size=13, weight=700))
        for index, line in enumerate(lines):
            parts.append(_text(x + 14, first + 16 + index * 14, line, size=11, fill=MUTED))
    parts.append("</svg>")
    return "".join(parts)


def diagram_legend(block: DiagramBlock) -> list[str]:
    used = dict.fromkeys(node.status for node in block.nodes if node.status)
    return [_STATUS_STYLE[status][2] for status in used]


def diagram_edges_text(block: DiagramBlock) -> list[str]:
    """Each edge as plain words, for readers and formats without pictures."""
    labels = {node.id: node.label for node in block.nodes}
    return [f"{labels[edge.source]} → {labels[edge.target]}" for edge in block.edges]


def diagram_html(block: DiagramBlock) -> str:
    edges = diagram_edges_text(block)
    listing = ("<details><summary>Connections as a list</summary><ul>"
               + "".join(f"<li>{_esc(edge)}</li>" for edge in edges)
               + "</ul></details>") if edges else ""
    return (f'<figure class="artifact-diagram"><h3>{_esc(block.title)}</h3>'
            f'<div class="viz">{diagram_svg(block)}</div>'
            + (f"<figcaption>{_esc(block.caption)}</figcaption>" if block.caption else "")
            + listing + "</figure>")


def diagram_mermaid(block: DiagramBlock) -> str:
    """A Mermaid flowchart, which Markdown viewers that support it draw."""
    ids = {node.id: f"n{index}" for index, node in enumerate(block.nodes, 1)}

    def label(text: str) -> str:
        return '"' + text.replace('"', "#quot;").replace("\n", " ") + '"'

    lines = ["flowchart TB"]
    for index, (title, nodes) in enumerate(diagram_columns(block), 1):
        lines.append(f"  subgraph g{index}[{label(title)}]")
        for node in nodes:
            lines.append(f"    {ids[node.id]}[{label(node.label)}]")
        lines.append("  end")
    for edge in block.edges:
        lines.append(f"  {ids[edge.source]} --> {ids[edge.target]}")
    return "\n".join(lines)


def chart_rows(block: ChartBlock) -> tuple[list[str], list[list[str]]]:
    headers = ["", *(series.name for series in block.series)]
    rows = [[category, *(_number(series.values[index]) for series in block.series)]
            for index, category in enumerate(block.categories)]
    return headers, rows


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def _nice_step(top: float) -> float:
    """A round gridline step that gives three to six lines."""
    raw = top / 4
    magnitude = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    for factor in (1, 2, 2.5, 5, 10):
        if raw <= factor * magnitude:
            return factor * magnitude
    return 10 * magnitude


def chart_svg(block: ChartBlock) -> str:
    bar_area, bar_height, gap = 380, 24, 16
    # Wide enough for the longest label (wrapped at 28 characters), no wider.
    longest = max(min(len(category), 28) for category in block.categories)
    label_width = min(200, max(72, 12 + longest * 6.6))
    stacked = block.chart_type == "stacked_bar"
    per_category = 1 if stacked else len(block.series)
    totals = [sum(series.values[i] for series in block.series)
              for i in range(len(block.categories))]
    top = max([*(totals if stacked else
                 [value for series in block.series for value in series.values]), 1])
    step = _nice_step(top)
    top = math.ceil(top / step) * step
    legend_height = 28 if len(block.series) > 1 else 0
    row_height = per_category * bar_height + gap
    plot_top = PADDING + legend_height + 6
    plot_height = len(block.categories) * row_height
    width = PADDING * 2 + label_width + bar_area + 48
    height = plot_top + plot_height + 26 + PADDING
    left = PADDING + label_width
    parts = [_svg_open(width, height, block.title)]
    x = PADDING
    if legend_height:
        for index, series in enumerate(block.series):
            color = _SERIES_COLORS[index % len(_SERIES_COLORS)]
            parts.append(f'<rect x="{x}" y="{PADDING}" width="14" height="14" rx="3" '
                         f'fill="{color}"/>')
            parts.append(_text(x + 20, PADDING + 11.5, series.name, fill=MUTED))
            x += 34 + 7 * len(series.name)
    ticks = round(top / step)
    for tick in range(ticks + 1):
        gx = left + bar_area * tick / ticks
        parts.append(f'<line x1="{gx}" y1="{plot_top - 4}" x2="{gx}" '
                     f'y2="{plot_top + plot_height - gap + 4}" stroke="{LINE}" '
                     'stroke-width="1"/>')
        parts.append(_text(gx, plot_top + plot_height + 10, _number(step * tick), size=11,
                           fill=MUTED, anchor="middle"))
    if block.unit:
        parts.append(_text(left + bar_area, plot_top + plot_height + 26, block.unit, size=11,
                           fill=MUTED, anchor="end"))
    for row, category in enumerate(block.categories):
        y = plot_top + row * row_height
        lines = textwrap.wrap(category, 28, max_lines=2, placeholder="…") or [category]
        middle = y + per_category * bar_height / 2
        for line_index, line in enumerate(lines):
            parts.append(_text(left - 10, middle + 4 + (line_index - (len(lines) - 1) / 2) * 14,
                               line, anchor="end"))
        cursor = left
        for index, series in enumerate(block.series):
            value = series.values[row]
            color = _SERIES_COLORS[index % len(_SERIES_COLORS)]
            length = bar_area * value / top
            bar_y = y if stacked else y + index * bar_height
            if length:
                parts.append(f'<rect x="{cursor:.1f}" y="{bar_y}" width="{length:.1f}" '
                             f'height="{bar_height - 4}" rx="4" fill="{color}">'
                             f"<title>{_esc(category)}: {_esc(series.name)} "
                             f"{_number(value)}</title></rect>")
                if stacked and length >= 24:
                    parts.append(_text(cursor + length / 2, bar_y + bar_height / 2 + 2,
                                       _number(value), size=11, weight=600,
                                       fill=_ON_COLOR.get(color, "rgb(255,255,255)"),
                                       anchor="middle"))
            if stacked:
                cursor += length
            else:
                parts.append(_text(left + length + 6, bar_y + bar_height / 2 + 2,
                                   _number(value), size=12, weight=600))
        if stacked:
            parts.append(_text(cursor + 6, y + bar_height / 2 + 2, _number(totals[row]),
                               size=12, weight=700))
    parts.append("</svg>")
    return "".join(parts)


def chart_html(block: ChartBlock) -> str:
    headers, rows = chart_rows(block)
    table = ("<details><summary>Values as a table</summary><table><thead><tr>"
             + "".join(f"<th>{_esc(cell)}</th>" for cell in headers) + "</tr></thead><tbody>"
             + "".join("<tr>" + "".join(f"<td>{_esc(cell)}</td>" for cell in row) + "</tr>"
                       for row in rows)
             + "</tbody></table></details>")
    return (f'<figure class="artifact-chart"><h3>{_esc(block.title)}</h3>'
            f'<div class="viz">{chart_svg(block)}</div>'
            + (f"<figcaption>{_esc(block.caption)}</figcaption>" if block.caption else "")
            + table + "</figure>")


def visual_text(block) -> list[str]:
    """The words a visual shows, for checks and model-facing projections."""
    if isinstance(block, DiagramBlock):
        return [block.title, *(group.label for group in block.groups),
                *(node.label for node in block.nodes), *([block.caption] if block.caption else [])]
    if isinstance(block, ChartBlock):
        return [block.title, *block.categories, *(series.name for series in block.series),
                *([block.caption] if block.caption else [])]
    return []
