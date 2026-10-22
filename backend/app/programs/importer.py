"""Load parsed program requirements (JSONL) into a collection. No model."""

import json
from collections import Counter
from pathlib import Path
from uuid import UUID, uuid5

from psycopg import Connection

from app.db import queries
from app.programs.binding import Binding, bind
from app.util.digest import json_digest


class SourceChanged(RuntimeError):
    """A bound source was replaced while its programs were being imported."""


def program_id(collection_id: UUID, title: str) -> UUID:
    return uuid5(collection_id, f"program:{title}")


def load_programs(path: Path) -> list[dict]:
    records = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record["title"] in seen:
            raise ValueError(f"duplicate program title: {record['title']!r}")
        seen.add(record["title"])
        records.append(record)
    if not records:
        # The retirement predicate is `NOT (program_id = ANY('{}'))`, which is
        # true for every row. An empty or truncated download must fail before
        # it can retire the collection's programs and reviewed rule releases.
        raise ValueError("program import requires at least one record")
    return records


def parsed_content_digest(record: dict) -> str:
    """Digest of the parsed projection itself, excluding its source identity."""
    return json_digest({key: value for key, value in record.items() if key != "source"})


def import_programs(conn: Connection, *, collection_id: UUID, path: Path) -> dict[str, int]:
    """Publish the current projections, preserving retired programs and saved releases.

    Bindings are computed read-only first; the replacement (rows, bindings, and
    supporting evidence) then publishes in one transaction, so readers never see
    a projection without its binding or a binding for an older projection.
    """
    records = load_programs(path)
    bindings = [bind(conn, collection_id, record) for record in records]
    conn.commit()  # Release the read snapshot before the write transaction.
    with conn.transaction():
        conn.execute(queries.get("lock_program_collection"), {"collection_id": collection_id})
        conn.execute(queries.get("retire_missing_programs"), {
            "collection_id": collection_id,
            "program_ids": [program_id(collection_id, r["title"]) for r in records],
        })
        for record, binding in zip(records, bindings, strict=True):
            pid = program_id(collection_id, record["title"])
            conn.execute(queries.get("insert_program"), {
                "program_id": pid, "collection_id": collection_id,
                **{key: record[key] for key in (
                    "title", "kind", "degree", "hours", "courses_required", "department",
                    "catalog", "source_url", "first_page", "last_page", "parser_version")},
            })
            conn.execute(queries.get("clear_program_projection"), {"program_id": pid})
            conn.execute(queries.get("clear_program_field_evidence"), {"program_id": pid})
            for item in record["items"]:
                conn.execute(queries.get("insert_program_item"), {
                    "program_id": pid, "position": item["position"],
                    "section": item["section"], "text": item["text"], "choose": item["choose"],
                })
                for code in item["courses"]:
                    conn.execute(queries.get("insert_program_course_mention"), {
                        "program_id": pid, "position": item["position"], "target_code": code,
                    })
            _write_binding(conn, pid, record, binding)
        resolve_program_mentions(conn, collection_id)
        conn.execute(queries.get("archive_incompatible_program_releases"),
                     {"collection_id": collection_id})
        conn.execute(queries.get("clear_inactive_program_release_pointers"),
                     {"collection_id": collection_id})
    # transaction() is only a savepoint if the caller already had a transaction
    # open (e.g. after a lookup query), so commit explicitly.
    conn.commit()
    counts = conn.execute(queries.get("program_mention_counts"),
                          {"collection_id": collection_id}).fetchone()
    statuses = Counter(binding.status for binding in bindings)
    return {"programs": len(records), "mentions": counts["mentions"],
            "resolved": counts["resolved"], "bound": statuses["bound"],
            "evidence_unresolved": statuses["evidence_unresolved"],
            "unbound": statuses["unbound"]}


def _write_binding(conn: Connection, pid: UUID, record: dict, binding: Binding) -> None:
    source = binding.source
    if source is None:
        conn.execute(queries.get("bind_program_source"), {
            "program_id": pid, "source_id": None, "source_index_revision": None,
            "source_sha256": None, "origin_key": None,
            "parsed_content_digest": parsed_content_digest(record), "binding_status": "unbound",
        })
        return
    if conn.execute(queries.get("lock_program_source"), {
            "source_id": source.source_id, "index_revision": source.index_revision,
            "file_hash": source.file_hash}).fetchone() is None:
        raise SourceChanged(f"source for {record['title']!r} changed during import; rerun it")
    conn.execute(queries.get("bind_program_source"), {
        "program_id": pid, "source_id": source.source_id,
        "source_index_revision": source.index_revision, "source_sha256": source.file_hash,
        "origin_key": source.origin_key, "parsed_content_digest": parsed_content_digest(record),
        "binding_status": binding.status,
    })
    common = {"program_id": pid, "source_id": source.source_id,
              "source_index_revision": source.index_revision}
    for position, supports in binding.items.items():
        for ordinal, support in enumerate(supports):
            conn.execute(queries.get("insert_program_item_evidence"), {
                **common, "position": position, "ordinal": ordinal, **_support(support)})
    for field, supports in binding.fields.items():
        for ordinal, support in enumerate(supports):
            conn.execute(queries.get("insert_program_field_evidence"), {
                **common, "field": field, "ordinal": ordinal, **_support(support)})


def _support(support) -> dict:
    return {"chunk_id": support.chunk_id, "locator_id": support.locator_id,
            "span_start": support.span_start, "span_end": support.span_end,
            "text_digest": support.text_digest}


def resolve_program_mentions(conn: Connection, collection_id: UUID) -> None:
    """Link program course mentions to course rows by code (after either side changes)."""
    conn.execute(queries.get("resolve_program_mentions"), {"collection_id": collection_id})
