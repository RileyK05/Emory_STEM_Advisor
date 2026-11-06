"""Load parsed program requirements (JSONL) into a collection. No model."""

import json
import re
from collections import Counter
from pathlib import Path
from uuid import UUID, uuid5

from psycopg import Connection

from app.atlas.records import normalize_code
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


# A number that stands alone on a requirement line, not a level, a range or an hour count.
_BARE_NUMBER_RE = re.compile(
    r"(?<![\d.$-])(?<!numbered )\b([1-7]\d{2}[A-Z]{0,3})\b"
    r"(?![\d-]|\+|\s*(?:level|credit|hour|academic|or\s+(?:higher|above)))", re.IGNORECASE)


def home_subject(record: dict) -> str | None:
    """The subject most of a program's own lines name, when one clearly leads."""
    subjects = Counter(code.split()[0] for item in record["items"]
                       for code in (normalize_code(raw) or raw for raw in item["courses"]))
    total = sum(subjects.values())
    if not total:
        return None
    subject, count = subjects.most_common(1)[0]
    return subject if count * 2 >= total else None


def bare_course_mentions(text: str, subject: str | None, known: set[str],
                         named: set[str]) -> list[str]:
    """Codes a line gives by number alone: "209 Perception and Action" in Psychology.

    A number becomes a course only when the program's own subject really has a
    course with that number. Round numbers (300) are levels, not courses.
    """
    if subject is None:
        return []
    found = []
    for number in _BARE_NUMBER_RE.findall(text):
        code = f"{subject} {number.upper()}"
        if (code in known and code not in named and code not in found
                and not re.fullmatch(r"\d00", number)):
            found.append(code)
    return found


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
    known = {row["code"] for row in conn.execute(queries.get("list_collection_course_codes"),
                                                 {"collection_id": collection_id})}
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
            subject = home_subject(record)
            for item in record["items"]:
                conn.execute(queries.get("insert_program_item"), {
                    "program_id": pid, "position": item["position"],
                    "section": item["section"], "text": item["text"], "choose": item["choose"],
                })
                # Normalized again here so a subject rename applies without re-scraping.
                named = dict.fromkeys(normalize_code(code) or code for code in item["courses"])
                for code in (*named, *bare_course_mentions(item["text"], subject, known, set(named))):
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
    conn.execute(queries.get("resolve_program_mentions_by_suffix_variant"),
                 {"collection_id": collection_id})
