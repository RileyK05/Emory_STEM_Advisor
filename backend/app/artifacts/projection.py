"""Compact, model-facing projections of attached artifact revisions.

The research prompt needs to know what an attached document, workbook or page
contains so a follow-up can revise it. It does not need the full spec: evidence
records, page layout and every cell can be hundreds of kilobytes. The server
keeps the full revision; the prompt gets this projection.
"""

from __future__ import annotations

import json
import re

_MAX_BLOCKS = 60
_MAX_ITEMS = 20
_MAX_TABLE_ROWS = 12
_MAX_SHEET_ROWS = 30
_MAX_SHEETS = 6
_MAX_TEXT = 400
_MAX_TOTAL_BYTES = 12_000

_TAG = re.compile(r"<[^>]+>")


def prompt_references(references) -> list[dict]:
    """Project artifact references to the small shape a research prompt can hold."""
    projected = []
    for reference in references or []:
        spec = reference.get("spec") or {}
        item = {
            "artifactId": str(reference.get("artifactId") or ""),
            "revisionId": str(reference.get("revisionId") or ""),
            "kind": spec.get("kind") or "document",
            "title": _clip(spec.get("title")),
        }
        kind = item["kind"]
        if kind == "document":
            item["blocks"] = _document_blocks(spec.get("blocks") or [])
        elif kind == "workbook":
            item["sheets"] = _workbook_sheets(spec.get("sheets") or [])
        elif kind == "html":
            item["text"] = _clip(_TAG.sub(" ", str(spec.get("html") or "")), 4_000)
        projected.append(item)
    while len(projected) > 1 and _encoded_length(projected) > _MAX_TOTAL_BYTES:
        projected.pop()
    return projected


def _document_blocks(blocks) -> list[dict]:
    out = []
    for block in blocks[:_MAX_BLOCKS]:
        btype = str(block.get("type") or "")
        if btype == "heading":
            out.append({"type": "heading", "text": _clip(block.get("text"), 200)})
        elif btype == "list":
            out.append({"type": "list",
                        "items": [_clip(item, 160) for item in
                                  (block.get("items") or [])[:_MAX_ITEMS]]})
        elif btype == "table":
            out.append({"type": "table",
                        "headers": [_clip(cell, 80) for cell in block.get("headers") or []],
                        "rows": [[_clip(cell, 80) for cell in row]
                                 for row in (block.get("rows") or [])[:_MAX_TABLE_ROWS]]})
        else:
            out.append({"type": btype, "text": _clip(block.get("text"), _MAX_TEXT)})
    return out


def _workbook_sheets(sheets) -> list[dict]:
    out = []
    for sheet in sheets[:_MAX_SHEETS]:
        cells = sheet.get("cells") or []
        rows: dict[int, list[str]] = {}
        for cell in cells:
            row = cell.get("row")
            column = cell.get("column")
            if not isinstance(row, int) or not isinstance(column, int):
                continue
            if row > _MAX_SHEET_ROWS:
                continue
            value = cell.get("value")
            if value is None and cell.get("formula") is not None:
                value = cell.get("formula")
            rows.setdefault(row, [])
            while len(rows[row]) < column:
                rows[row].append("")
            rows[row][column - 1] = _clip(value, 80)
        out.append({"name": _clip(sheet.get("name"), 80),
                    "rows": [rows[index] for index in sorted(rows)]})
    return out


def _clip(value, limit: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _encoded_length(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
