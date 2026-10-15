"""Scoped catalog directory for query planning: discovery data, never evidence.

The directory lists every course and document the query may read: current,
indexed, workspace-visible sources inside the effective source selection. Codes,
titles, and filenames come from the database; the short hint words come from the
supplied `configs/catalog-directory.md`. Rendering is deterministic, so an
unchanged scope yields a byte-identical prefix for provider prompt caching. The
memo key covers the scope and every visible source revision, so a catalog
replacement or selection change produces a new directory.
"""

import json
import re
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from threading import Lock
from types import MappingProxyType
from uuid import UUID

from app.db import queries
from app.util.digest import json_digest

FORMAT_VERSION = "2"
_HINTS_PATH = Path(__file__).resolve().parents[2] / "configs" / "catalog-directory.md"
_HINT_LINE = re.compile(r"^([A-Z]{2,7} \d{3}[A-Z]{0,2})\|[^|\n]*\|([^|\n]+)$")
_MEMO_SIZE = 8
_memo: OrderedDict[str, "CatalogDirectory"] = OrderedDict()
_memo_lock = Lock()


@dataclass(frozen=True)
class CatalogDirectory:
    text: str
    digest: str
    course_codes: frozenset[str]
    # Directory document id -> source id. Only these ids may be planned.
    documents: Mapping[str, UUID]

    @property
    def empty(self) -> bool:
        return not self.course_codes and not self.documents


def _clean(value: str, limit: int) -> str:
    """One line of untrusted text that cannot open a header, field, or prompt region."""
    text = " ".join(str(value).split()).replace("|", "/").replace("<<<", "<\u200b<<")
    return text[:limit]


@cache
def _hint_file() -> tuple[str, dict[str, str]]:
    """(glossary, code -> hint words) from the supplied directory, if present."""
    try:
        lines = _HINTS_PATH.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return "", {}
    glossary: list[str] = []
    for line in lines[next((i for i, line in enumerate(lines) if line.startswith("Glossary:")),
                           len(lines)):]:
        if not line.strip():
            break
        glossary.append(line.strip())
    hints = {match.group(1): _clean(match.group(2), 120)
             for match in map(_HINT_LINE.match, (line.strip() for line in lines)) if match}
    return " ".join(glossary), hints


def _document_ids(rows) -> dict[str, UUID]:
    ids: dict[str, UUID] = {}
    for row in rows:
        stem = re.sub(r"[^a-z0-9._-]+", "-", Path(row["filename"]).stem.lower()).strip("-")
        stem = stem[:100] or "document"
        name, suffix = stem, 2
        while name in ids:
            name, suffix = f"{stem}~{suffix}", suffix + 1
        ids[name] = row["source_id"]
    return ids


def _document_metadata(row) -> dict:
    """Compact discovery hints, never evidence or inferred applicability."""
    roles = ", ".join(row["document_roles"] or [])
    applicability = json.dumps(row["applicability"] or {}, sort_keys=True, ensure_ascii=False)
    return {
        "title": _clean(row["title"] or row["filename"], 300),
        "category": row["source_category"] or (
            "user_supplied" if row["workspace_key"] is not None else "unclassified"),
        "publisher": _clean(row["publisher"] or "unknown", 300),
        "institution": _clean(row["institution"] or "unknown", 200),
        "issuing_unit": _clean(row["issuing_unit"] or "unknown", 200),
        "roles": _clean(roles, 300),
        "edition": _clean(row["edition"] or "unknown", 200),
        "source_url": _clean(row["source_url"] or "unknown", 800),
        "published_at": str(row["published_at"] or "unknown"),
        "acquired_at": str(row["acquired_at"] or "unknown"),
        "applicability": _clean(applicability, 1200),
        "applicability_status": row["applicability_status"] or "unknown",
        "metadata_truncated": (len(roles) > 300 or len(applicability) > 1200
                               or len(row["source_url"] or "") > 800),
    }


def _render(courses, documents: dict[str, UUID], document_rows) -> str:
    glossary, hints = _hint_file()
    lines = [(f"Directory format {FORMAT_VERSION}. Course lines are CODE|Title|hint words; "
              "hint words are abbreviated discovery guesses, not catalog text.")]
    if glossary:
        lines.append(_clean(glossary, 2000))
    subject = None
    for row in courses:
        if row["subject"] != subject:
            subject = row["subject"]
            lines.append(f"## {_clean(subject, 20)}")
        hint = hints.get(row["code"])
        lines.append(f"{_clean(row['code'], 20)}|{_clean(row['title'], 160)}"
                     + (f"|{hint}" if hint else ""))
    if documents:
        lines.append("## DOCUMENTS (ID|discovery metadata; unknown applicability stays unknown)")
        by_source = {row["source_id"]: row for row in document_rows}
        lines.extend(f"{name}|" + json.dumps(_document_metadata(by_source[source_id]),
                                            sort_keys=True, ensure_ascii=False)
                     for name, source_id in documents.items())
    return "\n".join(lines)


def load_directory(conn, *, collection_id: UUID, workspace_key: str | None,
                   source_ids: list[UUID] | None) -> CatalogDirectory:
    """The directory for exactly this query scope. An empty selection is empty."""
    if source_ids == []:
        return CatalogDirectory("", json_digest([FORMAT_VERSION, "empty"]), frozenset(),
                                MappingProxyType({}))
    params = {"collection_id": collection_id, "workspace_key": workspace_key,
              "source_ids": source_ids}
    revisions = conn.execute(queries.get("list_directory_source_revisions"), params).fetchall()
    key = json_digest({
        "format": FORMAT_VERSION, "collection": str(collection_id), "workspace": workspace_key,
        "selection": sorted(str(source_id) for source_id in source_ids)
        if source_ids is not None else None,
        "revisions": [[str(row["source_id"]), row["index_revision"], row["metadata_revision"]]
                      for row in revisions],
    })
    with _memo_lock:
        if key in _memo:
            _memo.move_to_end(key)
            return _memo[key]
    courses = conn.execute(queries.get("list_directory_courses"), params).fetchall()
    document_rows = conn.execute(queries.get("list_directory_documents"), params).fetchall()
    documents = _document_ids(document_rows)
    text = _render(courses, documents, document_rows)
    directory = CatalogDirectory(
        text=text, digest=json_digest([FORMAT_VERSION, text]),
        course_codes=frozenset(row["code"] for row in courses),
        documents=MappingProxyType(documents),
    )
    with _memo_lock:
        _memo[key] = directory
        while len(_memo) > _MEMO_SIZE:
            _memo.popitem(last=False)
    return directory
