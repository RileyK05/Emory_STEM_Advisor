"""Validated, separately stored term-offering snapshots from Atlas captures.

Offering snapshots preserve live identities and are not master course records or
prerequisite facts. They enter the normal cited retrieval corpus as Markdown.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.atlas.live import DetailRecord, IndexRecord, IndexRow, read_capture
from app.db import queries
from app.embeddings.base import Embedder
from app.ingest.pipeline import ingest_file
from app.ingest.provenance import SourceProvenance
from app.retrieval.config import RetrievalConfig
from app.util.digest import canonical_json_bytes

OFFERING_CONTENT_TYPE = "text/markdown"
_PLACEHOLDER_CODES = {"aaaa 997uc", "aaaa 998uc", "aaaa 999t"}
_PLACEHOLDER_TITLES = {"course to be scheduled later", "test course"}


@dataclass(frozen=True)
class OfferingRecord:
    """One Atlas result card; grouped cards retain no invented class number."""

    result_cursor: int
    raw_course_code: str
    title: str
    class_number: str | None
    group_identity: str
    is_grouped: bool
    matched_identity: str
    srcdb: str
    credits: str | None
    description: str
    restrictions: str | None
    notes: str | None
    notices: tuple[tuple[str, str], ...]
    raw_metadata: dict
    evidence_hash: str
    review_status: str
    observed_at: str
    capture_envelope_hash: str


@dataclass(frozen=True)
class ValidatedOfferings:
    term: str
    career: str
    announced_count: int
    search_criteria: str
    index_evidence_hash: str
    index_observed_at: str
    index_capture_envelope_hash: str
    records: tuple[OfferingRecord, ...]
    capture_envelopes: tuple[dict, ...]

    @property
    def capture_hashes(self) -> tuple[str, ...]:
        return (self.index_capture_envelope_hash,
                *(item.capture_envelope_hash for item in self.records))

    @property
    def capture_set_hash(self) -> str:
        return hashlib.sha256(canonical_json_bytes(list(self.capture_hashes))).hexdigest()

    @property
    def observed_at_values(self) -> tuple[str, ...]:
        return (self.index_observed_at, *(item.observed_at for item in self.records))

    @property
    def eligible_count(self) -> int:
        return sum(item.review_status == "eligible" for item in self.records)

    @property
    def held_count(self) -> int:
        return len(self.records) - self.eligible_count


def _capture_time(value: dict) -> datetime:
    raw = value.get("observed_at")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("capture observed_at must be a timezone-aware timestamp")
    try:
        parsed = datetime.fromisoformat(raw.strip())
    except ValueError as exc:
        raise ValueError("capture observed_at must be a timezone-aware timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("capture observed_at must be a timezone-aware timestamp")
    return parsed.astimezone(UTC)


def _envelope_hash(value: dict) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _result_identity(value: dict) -> tuple[str, str, str, str] | None:
    identity = value.get("result_identity")
    if not isinstance(identity, dict):
        return None
    attrs = identity.get("attrs")
    if isinstance(attrs, dict):
        identity = {
            "group": attrs.get("data-group"),
            "key": attrs.get("data-key"),
            "matched": attrs.get("data-matched"),
            "srcdb": attrs.get("data-srcdb"),
        }
    values = tuple(identity.get(key) for key in ("group", "key", "matched", "srcdb"))
    if not all(isinstance(value, str) for value in values):
        return None
    return values


def _validated_index(value: dict, *, career: str, term: str) -> IndexRecord:
    index = read_capture(value)
    if not isinstance(index, IndexRecord):
        raise TypeError("offerings publication requires an index capture")
    if index.career != career or index.term != term:
        raise ValueError("index career or term disagrees with the requested snapshot")
    if index.announced_count <= 0:
        raise ValueError("empty offerings indexes are held and cannot be published")
    if len(index.rows) < index.announced_count:
        raise ValueError("captured index rows are fewer than its announced result count")
    return index


def select_latest_offering_captures(
    index_values: list[dict],
    detail_values: list[dict],
    *,
    career: str,
    term: str,
) -> tuple[dict, list[dict]]:
    """Choose the newest valid index and details tied to its exact cursor identities.

    A newer malformed index is not hidden by an older valid one. Details are
    rebased by exact result identity, so cursor shifts do not misattach details;
    stale and identity-incomplete captures cannot cover current result rows.
    """
    candidates = [value for value in index_values
                  if value.get("kind") == "index"
                  and value.get("career") == career and value.get("term") == term]
    if not candidates:
        raise ValueError(f"no index capture for {career} / {term}")
    dated = [(_capture_time(value), value) for value in candidates]
    latest_time = max(item[0] for item in dated)
    latest_values = [value for captured_at, value in dated if captured_at == latest_time]
    parsed_latest = []
    for value in latest_values:
        parsed_latest.append((_validated_index(value, career=career, term=term), value))
    distinct_hashes = {_envelope_hash(value) for _index, value in parsed_latest}
    if len(distinct_hashes) != 1:
        raise ValueError("latest index captures have the same timestamp but different envelopes")
    index, selected_index_value = parsed_latest[-1]

    rows_by_cursor = {cursor: row for cursor, row in enumerate(index.rows)}
    identity_cursors = {
        (row.group, row.key, row.matched, row.srcdb): cursor
        for cursor, row in rows_by_cursor.items()
    }
    detail_candidates: dict[int, list[tuple[datetime, dict, DetailRecord]]] = {}
    for value in detail_values:
        if (value.get("kind") != "detail" or value.get("career") != career
                or value.get("term") != term):
            continue
        captured_at = _capture_time(value)
        if captured_at < latest_time:
            continue
        identity = _result_identity(value)
        if identity not in identity_cursors:
            # Old-session details and captures missing a parsed result identity
            # are retained in the source archive but cannot cover an index row.
            continue
        cursor = identity_cursors[identity]
        try:
            detail = read_capture(value)
        except (TypeError, ValueError):
            # Another capture of this exact result may be valid. Keep only valid
            # candidates, then choose the latest one below.
            continue
        if not isinstance(detail, DetailRecord):
            continue
        detail_candidates.setdefault(cursor, []).append((captured_at, value, detail))

    selected_details: list[dict] = []
    for cursor in range(len(index.rows)):
        matches = detail_candidates.get(cursor, [])
        valid = []
        row = rows_by_cursor[cursor]
        for captured_at, value, detail in matches:
            try:
                _validate_detail_row(detail, value, row, cursor, career=career, term=term)
            except (TypeError, ValueError):
                continue
            valid.append((captured_at, detail.evidence_hash, value))
        if not valid:
            raise ValueError(f"no valid detail capture for current index result row {cursor}")
        selected_details.append(max(valid, key=lambda item: (item[0], item[1]))[2])
    return selected_index_value, selected_details


def _validate_detail_row(
    detail: DetailRecord,
    value: dict,
    row: IndexRow,
    cursor: int,
    *,
    career: str,
    term: str,
) -> tuple[dict, str]:
    if detail.career != career or detail.term != term:
        raise ValueError("detail career or term disagrees with the index capture")
    raw_identity = _result_identity(value)
    if raw_identity is None:
        raise TypeError("detail result_identity metadata is required")
    expected_identity = {
        "group": row.group,
        "key": row.key,
        "matched": row.matched,
        "srcdb": row.srcdb,
    }
    if raw_identity != tuple(expected_identity.values()):
        raise ValueError("detail result identity does not match an indexed result")
    if detail.code != row.code or row.group != f"code:{detail.code}":
        raise ValueError("detail raw course code does not match its index result")
    if row.key:
        if not row.key.startswith("crn:") or detail.class_number != row.key.removeprefix("crn:"):
            raise ValueError("detail class number does not match its index result")
        if detail.is_grouped:
            raise ValueError("class result was captured as a grouped card")
    elif detail.class_number is not None or not detail.is_grouped:
        raise ValueError("group result must remain a grouped card without a class number")
    return value["result_identity"], detail.evidence_hash


def validate_offering_captures(index_value: dict, detail_values: list[dict]) -> ValidatedOfferings:
    """Require a complete one-to-one match of details to the captured index."""
    index = read_capture(index_value)
    if not isinstance(index, IndexRecord):
        raise TypeError("offerings publication requires exactly one index capture")
    index = _validated_index(index_value, career=index.career, term=index.term)
    index_value, selected_details = select_latest_offering_captures(
        [index_value], detail_values, career=index.career, term=index.term,
    )

    cursor_rows: dict[int, IndexRow] = {}
    identities: set[tuple[str, str, str, str]] = set()
    for cursor, row in enumerate(index.rows):
        identity = (row.group, row.key, row.matched, row.srcdb)
        if identity in identities:
            raise ValueError("index contains duplicate result identities")
        identities.add(identity)
        cursor_rows[cursor] = row

    records: list[OfferingRecord] = []
    for cursor, value in enumerate(selected_details):
        detail = read_capture(value)
        if not isinstance(detail, DetailRecord):
            raise TypeError("detail capture list contains a non-detail record")
        row = cursor_rows[cursor]
        raw_identity, _ = _validate_detail_row(
            detail, value, row, cursor, career=index.career, term=index.term,
        )
        review_status = (
            "held_placeholder"
            if (detail.code.casefold() in _PLACEHOLDER_CODES
                or row.title.casefold() in _PLACEHOLDER_TITLES
                or detail.title.casefold() in _PLACEHOLDER_TITLES)
            else "eligible"
        )

        section_metadata = [asdict(section) for section in detail.sections]
        metadata = {
            "index_row": asdict(row),
            "result_identity": raw_identity,
            "sections": section_metadata,
        }
        original_cursor = value.get("index_cursor")
        if isinstance(original_cursor, bool) or not isinstance(original_cursor, int):
            original_cursor = None
        metadata["captured_cursor"] = original_cursor
        records.append(OfferingRecord(
            result_cursor=cursor,
            raw_course_code=detail.code,
            title=detail.title,
            class_number=detail.class_number,
            group_identity=row.group,
            is_grouped=detail.is_grouped,
            matched_identity=row.matched,
            srcdb=row.srcdb,
            credits=detail.credits,
            description=detail.description,
            restrictions=detail.restrictions,
            notes=detail.notes,
            notices=detail.notices,
            raw_metadata=metadata,
            evidence_hash=detail.evidence_hash,
            review_status=review_status,
            observed_at=_capture_time(value).isoformat(),
            capture_envelope_hash=_envelope_hash(value),
        ))
    records.sort(key=lambda item: item.result_cursor)
    return ValidatedOfferings(
        term=index.term,
        career=index.career,
        announced_count=index.announced_count,
        search_criteria=index.search_criteria,
        index_evidence_hash=index.evidence_hash,
        index_observed_at=_capture_time(index_value).isoformat(),
        index_capture_envelope_hash=_envelope_hash(index_value),
        records=tuple(records),
        capture_envelopes=(index_value, *selected_details),
    )


def render_offerings_markdown(snapshot: ValidatedOfferings) -> bytes:
    """Return stable citable prose with no acquisition timestamp in its identity."""
    lines = [
        f"# {snapshot.term} Course Atlas offerings — {snapshot.career}",
        "",
        "This is a term-scoped capture of Atlas result cards. Course identities are",
        "preserved as displayed; grouped cards do not imply a single class number.",
        "",
        f"- Term: {snapshot.term}",
        f"- Career: {snapshot.career}",
        f"- Announced result count: {snapshot.announced_count}",
        f"- Captured result rows: {len(snapshot.records)}",
        f"- Searchable result rows: {snapshot.eligible_count}",
        f"- Held placeholder rows: {snapshot.held_count}; held row details are excluded",
        f"- Search criteria: {snapshot.search_criteria}",
        f"- Index evidence SHA-256: `{snapshot.index_evidence_hash}`",
        f"- Complete snapshot payload SHA-256: `{_snapshot_payload_hash(snapshot)}`",
    ]
    for item in snapshot.records:
        if item.review_status != "eligible":
            continue
        class_scope = "grouped result" if item.class_number is None else f"class {item.class_number}"
        lines.extend((
            "",
            f"## {item.raw_course_code} — {item.title} ({class_scope})",
            "",
            f"- Term: {snapshot.term}",
            f"- Career: {snapshot.career}",
            f"- Result cursor: {item.result_cursor}",
            ("- Class number: grouped result card; no single class number was shown"
             if item.class_number is None else f"- Class number: `{item.class_number}`"),
            f"- Credits: {item.credits or 'not shown'}",
            f"- Description: {item.description or 'not shown'}",
            f"- Registration restrictions: {item.restrictions or 'not shown'}",
            f"- Class notes: {item.notes or 'not shown'}",
            f"- Consent: {dict(item.notices).get('consent', 'not shown')}",
            f"- Section scope: {_section_scope(item.raw_metadata['sections'], item.class_number)}",
            ("- Grouped offering: section details are child variations; they do not "
             "establish one class number." if item.is_grouped else ""),
            f"- Detail evidence SHA-256: `{item.evidence_hash}`",
        ))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _section_scope(sections: list[dict], class_number: str | None = None) -> str:
    """Expose useful section scope without indexing complete registration payloads."""
    if class_number is not None:
        sections = [section for section in sections if section.get("crn") == class_number]
    identities = sorted({str(section.get("section") or "") for section in sections
                         if section.get("section")})
    campuses = sorted({str(section.get("campus") or "") for section in sections
                       if section.get("campus")})
    kinds = sorted({str(section.get("section_type") or "") for section in sections
                    if section.get("section_type")})
    parts = []
    if identities:
        parts.append("sections " + ", ".join(identities))
    if campuses:
        parts.append("campuses " + ", ".join(campuses))
    if kinds:
        parts.append("types " + ", ".join(kinds))
    return "; ".join(parts) if parts else "not shown"


def _snapshot_payload_hash(snapshot: ValidatedOfferings) -> str:
    records = []
    for item in snapshot.records:
        record = asdict(item)
        record.pop("observed_at")
        record.pop("capture_envelope_hash")
        records.append(record)
    payload = {
        "career": snapshot.career,
        "term": snapshot.term,
        "announced_count": snapshot.announced_count,
        "search_criteria": snapshot.search_criteria,
        "index_evidence_hash": snapshot.index_evidence_hash,
        "records": records,
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def origin_key(snapshot: ValidatedOfferings) -> str:
    return f"emory:course-atlas-offerings:{snapshot.career}:{snapshot.term}"


def publish_offerings(
    conn: Connection,
    *,
    collection_id: UUID,
    snapshot: ValidatedOfferings,
    path: Path,
    data_dir: Path,
    embedder: Embedder,
    embed_model: str,
    count_tokens,
    config: RetrievalConfig,
) -> UUID:
    """Index one validated offering snapshot and its relational projection atomically."""
    if snapshot.eligible_count == 0:
        raise ValueError("snapshot has no eligible offerings; all results are held from publication")
    document = render_offerings_markdown(snapshot)
    expected_hash = hashlib.sha256(document).hexdigest()
    expected_path = path.read_bytes()
    if expected_path != document:
        raise ValueError("offerings staging file does not match validated capture records")
    if len(snapshot.capture_envelopes) != len(snapshot.capture_hashes):
        raise ValueError("offering capture envelopes do not match the capture hash set")
    capture_paths = _preserve_capture_envelopes(snapshot, data_dir)

    def store_snapshot(tx_conn: Connection, source_id: UUID, _chunk_ids: list[UUID]) -> None:
        published = tx_conn.execute(queries.get("get_source_by_hash"), {
            "collection_id": collection_id,
            "file_hash": expected_hash,
            "workspace_key": None,
        }).fetchone()
        if published is None or published["source_id"] != source_id:
            raise ValueError("offerings file changed during ingest; refusing mismatched snapshot")
        tx_conn.execute(queries.get("insert_atlas_offering_snapshot"), {
            "source_id": source_id,
            "capture_set_hash": snapshot.capture_set_hash,
            "career": snapshot.career,
            "term": snapshot.term,
            "index_evidence_hash": snapshot.index_evidence_hash,
            "announced_count": snapshot.announced_count,
            "result_row_count": len(snapshot.records),
            "eligible_row_count": snapshot.eligible_count,
            "held_row_count": snapshot.held_count,
            "search_criteria": snapshot.search_criteria,
            "capture_hashes": Jsonb(list(snapshot.capture_hashes)),
            "observed_from": min(snapshot.observed_at_values),
            "observed_until": max(snapshot.observed_at_values),
        })
        for capture_hash, raw_path in capture_paths:
            capture_row = tx_conn.execute(
                queries.get("insert_atlas_offering_capture_file"), {
                "source_id": source_id,
                "capture_set_hash": snapshot.capture_set_hash,
                "capture_hash": capture_hash,
                "raw_path": str(raw_path),
            }).fetchone()
            if capture_row is None:
                raise ValueError("stored Atlas capture path disagrees with immutable evidence hash")
        for ordinal, item in enumerate(snapshot.records):
            tx_conn.execute(queries.get("insert_atlas_offering_row"), {
                "source_id": source_id,
                "capture_set_hash": snapshot.capture_set_hash,
                "row_ordinal": ordinal,
                "result_cursor": item.result_cursor,
                "raw_course_code": item.raw_course_code,
                "title": item.title,
                "class_number": item.class_number,
                "group_identity": item.group_identity,
                "is_grouped": item.is_grouped,
                "matched_identity": item.matched_identity,
                "srcdb": item.srcdb,
                "credits": item.credits,
                "description": item.description,
                "restrictions": item.restrictions,
                "notes": item.notes,
                "notices": Jsonb([list(notice) for notice in item.notices]),
                "raw_metadata": Jsonb(item.raw_metadata),
                "evidence_hash": item.evidence_hash,
                "review_status": item.review_status,
                "observed_at": item.observed_at,
                "capture_envelope_hash": item.capture_envelope_hash,
            })

    existing_source = conn.execute(queries.get("get_source_by_hash"), {
        "collection_id": collection_id,
        "file_hash": expected_hash,
        "workspace_key": None,
    }).fetchone()
    first_observed = min(
        _capture_time({"observed_at": value}).date()
        for value in snapshot.observed_at_values
    )
    if existing_source is not None:
        prior = conn.execute(queries.get("get_atlas_offerings_first_observation"), {
            "source_id": existing_source["source_id"],
        }).fetchone()
        if prior is not None and prior["acquired_at"] is not None:
            first_observed = min(first_observed, prior["acquired_at"])

    provenance = SourceProvenance(
        "institutional_catalog",
        publisher="Emory University Office of the Registrar",
        source_url="https://atlas.emory.edu/",
        edition=f"{snapshot.term} — {snapshot.career}",
        title=f"{snapshot.term} Course Atlas offerings — {snapshot.career}",
        institution="Emory University",
        issuing_unit="Office of the Registrar",
        document_roles=("course_offerings",),
        applicability={"career": (snapshot.career,), "term": (snapshot.term,)},
        applicability_status="unknown",
        acquired_at=first_observed,
    )
    source_id = ingest_file(
        conn,
        collection_id=collection_id,
        path=path,
        content_type=OFFERING_CONTENT_TYPE,
        count_tokens=count_tokens,
        embedder=embedder,
        embed_model=embed_model,
        config=config,
        origin_key=origin_key(snapshot),
        on_chunk_transaction=store_snapshot,
        provenance=provenance,
    )
    # ingest_file intentionally returns early for already-indexed identical
    # canonical content. Preserve each newly observed raw capture set append-only.
    with conn.transaction():
        store_snapshot(conn, source_id, [])
    conn.commit()
    return source_id


def _preserve_capture_envelopes(
    snapshot: ValidatedOfferings, data_dir: Path
) -> tuple[tuple[str, Path], ...]:
    """Write canonical capture envelopes immutably before publishing DB pointers."""
    root = Path(data_dir).resolve() / "raw" / "atlas-captures"
    root.mkdir(parents=True, exist_ok=True)
    results = []
    for envelope, expected_hash in zip(snapshot.capture_envelopes, snapshot.capture_hashes,
                                       strict=True):
        content = canonical_json_bytes(envelope)
        digest = hashlib.sha256(content).hexdigest()
        if digest != expected_hash:
            raise ValueError("capture envelope bytes disagree with the selected capture hash")
        destination = root / f"{digest}.json"
        if destination.exists():
            if destination.is_symlink() or destination.read_bytes() != content:
                raise ValueError("existing Atlas capture file differs from its immutable hash")
        else:
            fd, temp_name = tempfile.mkstemp(prefix=f".{digest}-", suffix=".tmp", dir=root)
            temp = Path(temp_name)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    os.link(temp, destination)
                except FileExistsError:
                    if destination.is_symlink() or destination.read_bytes() != content:
                        raise ValueError("concurrent Atlas capture file differs from its hash")
            finally:
                temp.unlink(missing_ok=True)
        if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
            raise ValueError("preserved Atlas capture failed its content hash")
        results.append((digest, destination))
    return tuple(results)


def read_offering_snapshot(
    conn: Connection,
    *,
    source_id: UUID,
    index_revision: int,
    collection_id: UUID,
    include_held: bool = False,
    capture_set_hash: str | None = None,
) -> dict | None:
    """Read only a caller-pinned indexed source revision, current or historical."""
    header = conn.execute(queries.get("get_atlas_offering_snapshot_for_revision"), {
        "source_id": source_id,
        "index_revision": index_revision,
        "collection_id": collection_id,
        "capture_set_hash": capture_set_hash,
    }).fetchone()
    if header is None:
        return None
    rows = conn.execute(queries.get("list_atlas_offering_rows_for_revision"), {
        "source_id": source_id,
        "index_revision": index_revision,
        "collection_id": collection_id,
        "capture_set_hash": header["capture_set_hash"],
        "include_held": include_held,
    }).fetchall()
    result = dict(header)
    result["rows"] = rows
    return result


def capture_slug(value: str) -> str:
    """Build a readable deterministic filename component from an exact facet."""
    slug = "-".join(re.findall(r"[a-z0-9]+", value.casefold()))
    if not slug:
        raise ValueError("career name has no filename-safe characters")
    return slug


