"""Scoped, exact reads for research advising. Read-only; no model, no provider.

A run pins a `ResearchScope` when it starts: the workspace, collection, effective
source selection, and every accessible current indexed source with its index
revision, plus the program projections bound to those exact revisions. Every
read here takes that scope, reads only inside it, and re-checks that the sources
it touches still have the pinned revision. A read never widens scope: an ID or
code that is out of scope reads exactly like one that does not exist.

Each read runs in one transaction that first share-locks the sources it touches
(`lock_sources`). Reindex, supersession and removal update the source row before
they change its chunks, so a read either sees the pinned revision and its content
together or raises `ScopeChanged`; it never stamps new content with an old
revision. The locks last until the enclosing transaction ends.

Reads return exact chunk text with its identity (`ChunkRead`). They never merge
chunks into a synthetic passage, never invent similarity scores, and never turn
a parsed program line into a cited requirement unless that line's stored
evidence still matches the chunk text byte for byte.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from uuid import UUID

from app.db import queries
from app.util.digest import json_digest
from app.util.json import iso

SCOPE_FORMAT_VERSION = "2"
DOCUMENT_PAGE_BYTES = 8_000
MAX_COURSE_CODES = 4


class ScopeChanged(RuntimeError):
    """A pinned source changed revision or left scope; the run must not mix editions."""

    def __init__(self, source_ids):
        super().__init__("pinned sources changed: " + ", ".join(sorted(map(str, source_ids))))
        self.source_ids = frozenset(source_ids)


class NotInScope(LookupError):
    """Unknown or out of scope. Callers report both the same way."""


@dataclass(frozen=True)
class SourcePin:
    source_id: UUID
    index_revision: int
    filename: str
    content_type: str
    origin_key: str | None
    source_kind: str
    publisher: str | None = None
    source_url: str | None = None
    edition: str | None = None
    published_at: str | None = None
    acquired_at: str | None = None
    geography: str | None = None
    occupation_id: str | None = None
    title: str | None = None
    institution: str | None = None
    issuing_unit: str | None = None
    document_roles: tuple[str, ...] = ()
    applicability: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    applicability_status: str = "unknown"
    metadata_revision: int = 0

    def __post_init__(self):
        # Freezing the dataclass alone would leave caller-owned lists/dicts mutable.
        object.__setattr__(self, "document_roles", tuple(self.document_roles))
        object.__setattr__(self, "applicability", MappingProxyType({
            key: tuple(values) for key, values in self.applicability.items()}))


@dataclass(frozen=True)
class ProgramPin:
    program_id: UUID
    title: str
    kind: str
    degree: str | None
    department: str | None
    source_id: UUID
    source_index_revision: int
    parsed_content_digest: str | None
    binding_status: str


@dataclass(frozen=True)
class ResearchScope:
    collection_id: UUID
    workspace_key: str | None
    selection: tuple[UUID, ...] | None  # None = all accessible; () = nothing
    sources: Mapping[UUID, SourcePin]
    programs: Mapping[UUID, ProgramPin]
    digest: str

    def manifest(self) -> dict:
        """JSON for `research_runs.scope_manifest`; `digest` is its hash."""
        return _manifest(self.collection_id, self.workspace_key, self.selection,
                         self.sources, self.programs)


@dataclass(frozen=True)
class ChunkRead:
    """One actual chunk, exactly as indexed, with the identity a citation needs."""

    chunk_id: UUID
    source_id: UUID
    source_revision: int
    chunk_index: int
    locator_id: UUID | None
    locator_label: str
    text: str

    @property
    def text_digest(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SupportedText:
    """A parsed line or header value and the exact chunk spans that support it.

    `support` is empty when the line has no stored evidence or the stored span no
    longer matches the chunk: the line is then a discovery lead, not a citation.
    """

    text: str
    support: tuple[tuple[ChunkRead, int, int], ...] = ()

    @property
    def cited(self) -> bool:
        return bool(self.support)


@dataclass(frozen=True)
class ProgramItem:
    position: int
    section: str | None
    line: SupportedText
    # Mentioned codes include alternatives and exclusions; `choose` is an explicit
    # count on this raw line only, never a whole-degree solution.
    mentioned_course_codes: tuple[str, ...]
    choose: int | None


@dataclass(frozen=True)
class ProgramRead:
    pin: ProgramPin
    fields: Mapping[str, SupportedText]
    items: tuple[ProgramItem, ...]
    catalog: str | None

    @property
    def unresolved_positions(self) -> tuple[int, ...]:
        return tuple(item.position for item in self.items if not item.line.cited
                     and any(char.isalnum() for char in item.line.text))


@dataclass(frozen=True)
class CourseRead:
    code: str
    title: str
    canonical_code: str
    requisites_raw: str | None
    parse_status: str
    chunks: tuple[ChunkRead, ...]
    lifecycle: str = "current"


@dataclass(frozen=True)
class DocumentSection:
    locator_id: UUID
    ordinal: int
    label: str
    first_chunk: int
    chunk_count: int


@dataclass(frozen=True)
class DocumentPage:
    source: SourcePin
    sections: tuple[DocumentSection, ...]
    chunks: tuple[ChunkRead, ...]
    next_chunk: int | None
    # True when a single chunk is larger than the page and could not be returned.
    incomplete: bool = False
    limitations: tuple[str, ...] = field(default=())


def _kind(row) -> str:
    """A verified classification, else what an unverified source honestly is."""
    if row["source_category"]:
        return row["source_category"]
    return "user_supplied" if row["workspace_key"] is not None else "unclassified"


def _pin(row) -> SourcePin:
    return SourcePin(row["source_id"], row["index_revision"], row["filename"],
                     row["content_type"], row["origin_key"], _kind(row), row["publisher"],
                     row["source_url"], row["edition"], iso(row["published_at"]),
                     iso(row["acquired_at"]), row["geography"], row["occupation_id"],
                     title=row["title"], institution=row["institution"],
                     issuing_unit=row["issuing_unit"], document_roles=row["document_roles"] or (),
                     applicability=row["applicability"] or {},
                     applicability_status=row["applicability_status"] or "unknown",
                     metadata_revision=row["metadata_revision"])


def _manifest(collection_id, workspace_key, selection, sources, programs) -> dict:
    return {
        "format": SCOPE_FORMAT_VERSION,
        "collection_id": str(collection_id),
        "workspace_key": workspace_key,
        "selection": None if selection is None else sorted(map(str, selection)),
        "sources": [[str(pin.source_id), pin.index_revision, pin.source_kind,
                     pin.metadata_revision]
                    for pin in sorted(sources.values(), key=lambda p: str(p.source_id))],
        "programs": [[str(pin.program_id), str(pin.source_id), pin.source_index_revision,
                      pin.parsed_content_digest, pin.binding_status]
                     for pin in sorted(programs.values(), key=lambda p: str(p.program_id))],
    }


def build_scope(conn, *, collection_id: UUID, workspace_key: str | None,
                source_ids: list[UUID] | None,
                frozen_snapshot: Mapping[str, int] | None = None) -> ResearchScope:
    """Pin the run's scope. `source_ids=[]` is an empty scope, never "everything".

    `frozen_snapshot` is a saved conversation's stricter source snapshot
    (source_id -> index revision). When given it is the selection, and every
    entry must still be current at exactly that revision.
    """
    if frozen_snapshot is not None:
        source_ids = [UUID(str(source_id)) for source_id in frozen_snapshot]
    selection = None if source_ids is None else tuple(sorted(set(source_ids), key=str))
    rows = [] if selection == () else conn.execute(
        queries.get("list_research_scope_sources"), {
            "collection_id": collection_id, "workspace_key": workspace_key,
            "source_ids": None if selection is None else list(selection)}).fetchall()
    sources = {row["source_id"]: _pin(row) for row in rows}
    if frozen_snapshot is not None:
        stale = {UUID(str(source_id)) for source_id, revision in frozen_snapshot.items()
                 if UUID(str(source_id)) not in sources
                 or sources[UUID(str(source_id))].index_revision != revision}
        if stale:
            raise ScopeChanged(stale)
    program_rows = conn.execute(queries.get("list_research_programs"), {
        "collection_id": collection_id, "source_ids": list(sources)}).fetchall() if sources else []
    programs = {
        row["program_id"]: ProgramPin(
            row["program_id"], row["title"], row["kind"], row["degree"], row["department"],
            row["source_id"], row["source_index_revision"], row["parsed_content_digest"],
            row["binding_status"])
        for row in program_rows
        # Bound to an older revision of an in-scope source: not this edition.
        if row["source_index_revision"] == sources[row["source_id"]].index_revision
    }
    manifest = _manifest(collection_id, workspace_key, selection, sources, programs)
    return ResearchScope(collection_id, workspace_key, selection, MappingProxyType(sources),
                         MappingProxyType(programs), json_digest(manifest))


def lock_sources(conn, *, collection_id: UUID, workspace_key: str | None,
                 pinned: Mapping[UUID, int]) -> None:
    """Share-lock each pinned source; raise ScopeChanged unless it is still current,
    indexed, accessible, and at its pinned revision. Call inside a transaction."""
    if not pinned:
        return
    current = {row["source_id"]: row["index_revision"] for row in conn.execute(
        queries.get("research_current_source_revisions"), {
            "source_ids": sorted(pinned, key=str), "collection_id": collection_id,
            "workspace_key": workspace_key}).fetchall()}
    stale = {source_id for source_id, revision in pinned.items()
             if current.get(source_id) != revision}
    if stale:
        raise ScopeChanged(stale)


def verify_sources(conn, scope: ResearchScope, source_ids) -> None:
    """Lock content and metadata together for the in-scope members of `source_ids`."""
    pins = {source_id: scope.sources[source_id] for source_id in source_ids
            if source_id in scope.sources}
    lock_sources(conn, collection_id=scope.collection_id, workspace_key=scope.workspace_key,
                 pinned={source_id: pin.index_revision for source_id, pin in pins.items()})
    # Read provenance only after acquiring the source locks: a concurrent metadata
    # writer may have committed while the locking statement was waiting.
    current = source_pins(conn, collection_id=scope.collection_id,
                          workspace_key=scope.workspace_key, source_ids=pins)
    changed = {source_id for source_id, pin in pins.items() if current.get(source_id) != pin}
    if changed:
        raise ScopeChanged(changed)


def source_pins(conn, *, collection_id: UUID, workspace_key: str | None,
                source_ids) -> dict[UUID, SourcePin]:
    """Current identity and verified metadata of accessible indexed sources."""
    source_ids = sorted(set(source_ids), key=str)
    if not source_ids:
        return {}
    return {row["source_id"]: _pin(row) for row in conn.execute(
        queries.get("list_research_scope_sources"), {
            "collection_id": collection_id, "workspace_key": workspace_key,
            "source_ids": source_ids}).fetchall()}


def manifest_source_pins(manifest: dict) -> dict[UUID, tuple[int, str, int]]:
    """Read both manifests. Legacy runs did not pin provenance metadata.

    Their zero metadata revision permits only sources without verified provenance. A run
    that needs verified metadata must restart; saved evidence remains readable.
    """
    return {UUID(entry[0]): (entry[1], entry[2], entry[3] if len(entry) > 3 else 0)
            for entry in manifest["sources"]}


def verify_manifest_sources(conn, manifest: dict, source_ids=None) -> dict[UUID, SourcePin]:
    """Lock a persisted selection, checking provenance after the source locks."""
    pinned = manifest_source_pins(manifest)
    if source_ids is not None:
        pinned = {source_id: pinned[source_id] for source_id in source_ids if source_id in pinned}
    collection_id = UUID(manifest["collection_id"])
    lock_sources(conn, collection_id=collection_id, workspace_key=manifest["workspace_key"],
                 pinned={source_id: pin[0] for source_id, pin in pinned.items()})
    sources = source_pins(conn, collection_id=collection_id,
                          workspace_key=manifest["workspace_key"], source_ids=pinned)
    changed = {source_id for source_id, (_revision, kind, metadata_revision) in pinned.items()
               if source_id not in sources or (sources[source_id].source_kind,
                   sources[source_id].metadata_revision) != (kind, metadata_revision)}
    if changed:
        raise ScopeChanged(changed)
    return sources


def verify_manifest(conn, manifest: dict) -> None:
    """Lock a persisted research scope for saved-turn publication."""
    verify_manifest_sources(conn, manifest)
    wanted = {UUID(p): (UUID(s), revision, digest, status)
              for p, s, revision, digest, status in manifest["programs"]}
    current = {r["program_id"]: (r["source_id"], r["source_index_revision"],
                               r["parsed_content_digest"], r["binding_status"])
               for r in conn.execute(queries.get("research_current_program_pins"), {
                   "program_ids": list(wanted)}).fetchall()}
    if any(current.get(p) != pin for p, pin in wanted.items()):
        raise ScopeChanged({pin[0] for p, pin in wanted.items() if current.get(p) != pin})


def exact_chunks(conn, pinned: Mapping[UUID, int], chunk_ids) -> dict[UUID, ChunkRead]:
    """Indexed chunks by ID, limited to `pinned` sources and stamped with their
    pinned revision. Only meaningful while `lock_sources` holds those sources."""
    chunk_ids = sorted(set(chunk_ids), key=str)
    if not chunk_ids or not pinned:
        return {}
    return {row["chunk_id"]: _chunk(row, pinned[row["source_id"]]) for row in conn.execute(
        queries.get("research_chunks_by_ids"), {
            "chunk_ids": chunk_ids, "source_ids": sorted(pinned, key=str)}).fetchall()}


def _chunk(row, revision: int) -> ChunkRead:
    return ChunkRead(row["chunk_id"], row["source_id"], revision, row["chunk_index"],
                     row["locator_id"], row["locator_label"], row["text"])


def list_programs(scope: ResearchScope, *, kind: str | None = None,
                  department: str | None = None) -> list[ProgramPin]:
    """Deterministic browse over the pinned programs; no text beyond identities."""
    wanted = department.casefold() if department else None
    return [pin for pin in sorted(scope.programs.values(), key=lambda p: (p.kind, p.title))
            if (kind is None or pin.kind == kind)
            and (wanted is None or (pin.department or "").casefold().find(wanted) >= 0)]


def _supported(text: str, rows, chunks: dict[UUID, ChunkRead]) -> SupportedText:
    """Keep the evidence only if every stored span still matches its chunk exactly."""
    support = []
    for row in rows:
        chunk = chunks.get(row["chunk_id"])
        if chunk is None or row["span_start"] is None or row["span_end"] is None:
            return SupportedText(text)
        exact = chunk.text[row["span_start"]:row["span_end"]]
        if hashlib.sha256(exact.encode("utf-8")).hexdigest() != row["text_digest"]:
            return SupportedText(text)
        support.append((chunk, row["span_start"], row["span_end"]))
    return SupportedText(text, tuple(support))


def read_program(conn, scope: ResearchScope, program_id: UUID) -> ProgramRead:
    """A pinned program's header and raw lines, each cited or marked a lead."""
    pin = scope.programs.get(program_id)
    if pin is None:
        raise NotInScope(program_id)
    with conn.transaction():
        verify_sources(conn, scope, [pin.source_id])
        detail = conn.execute(queries.get("get_program_for_research"),
                              {"program_id": program_id}).fetchone()
        if (detail is None or detail["source_id"] != pin.source_id
                or detail["source_index_revision"] != pin.source_index_revision
                or detail["parsed_content_digest"] != pin.parsed_content_digest):
            # The projection was re-imported since the run pinned it.
            raise ScopeChanged({pin.source_id})
        items = conn.execute(queries.get("list_program_items"),
                             {"program_id": program_id}).fetchall()
        item_evidence = conn.execute(queries.get("list_program_item_evidence"),
                                     {"program_id": program_id}).fetchall()
        field_evidence = conn.execute(queries.get("list_program_field_evidence"),
                                      {"program_id": program_id}).fetchall()
        chunks = exact_chunks(conn, {pin.source_id: pin.source_index_revision},
                              [row["chunk_id"] for row in [*item_evidence, *field_evidence]])
    by_item: dict[int, list] = {}
    for row in item_evidence:
        by_item.setdefault(row["position"], []).append(row)
    by_field: dict[str, list] = {}
    for row in field_evidence:
        by_field.setdefault(row["field"], []).append(row)
    fields = {name: _supported(detail[name], by_field.get(name, []), chunks)
              for name in ("title", "degree", "hours", "courses_required", "department")
              if detail[name]}
    return ProgramRead(
        pin=pin, catalog=detail["catalog"], fields=MappingProxyType(fields),
        items=tuple(ProgramItem(
            row["position"], row["section"],
            _supported(row["text"], by_item.get(row["position"], []), chunks),
            tuple(row["courses"]), row["choose"]) for row in items),
    )


def _course_rows(conn, scope: ResearchScope, codes, source_ids) -> list:
    return conn.execute(queries.get("research_courses_by_codes"), {
        "collection_id": scope.collection_id, "codes": codes,
        "source_ids": list(source_ids)}).fetchall()


def read_courses(conn, scope: ResearchScope, codes) -> list[CourseRead]:
    """Exact course records by code, from in-scope atlas sources only."""
    codes = list(dict.fromkeys(codes))
    if len(codes) > MAX_COURSE_CODES:
        raise ValueError(f"read at most {MAX_COURSE_CODES} course codes per call")
    if not codes or not scope.sources:
        return []
    with conn.transaction():
        found = {row["source_id"] for row in _course_rows(conn, scope, codes, scope.sources)}
        if not found:
            return []
        verify_sources(conn, scope, found)
        # Re-read under the locks, so the records match the verified revisions.
        rows = _course_rows(conn, scope, codes, found)
        chunk_rows = conn.execute(queries.get("research_chunks_for_locators"), {
            "locator_ids": [row["locator_id"] for row in rows],
            "source_ids": list(found)}).fetchall()
    by_locator: dict[UUID, list[ChunkRead]] = {}
    for row in chunk_rows:
        by_locator.setdefault(row["locator_id"], []).append(
            _chunk(row, scope.sources[row["source_id"]].index_revision))
    return [CourseRead(row["code"], row["title"], row["canonical_code"],
                       row["requisites_raw"], row["parse_status"],
                       tuple(by_locator.get(row["locator_id"], ())),
                       row["lifecycle"])
            for row in rows]


def read_document(conn, scope: ResearchScope, source_id: UUID, *, from_chunk: int = 0,
                  page_bytes: int = DOCUMENT_PAGE_BYTES) -> DocumentPage:
    """A section index plus whole chunks from `from_chunk`, up to `page_bytes`.

    Paging is at chunk boundaries only: a condition is never cut off mid-chunk.
    A chunk that alone exceeds the page is reported, not truncated.
    """
    source = scope.sources.get(source_id)
    if source is None:
        raise NotInScope(source_id)
    with conn.transaction():
        verify_sources(conn, scope, [source_id])
        sections = tuple(DocumentSection(row["locator_id"], row["ordinal"], row["label"],
                                         row["first_chunk"], row["chunk_count"])
                         for row in conn.execute(queries.get("research_document_sections"),
                                                 {"source_id": source_id}).fetchall())
        rows = conn.execute(queries.get("research_document_chunks"), {
            "source_id": source_id, "from_chunk": from_chunk, "limit": 200}).fetchall()
    page: list[ChunkRead] = []
    used = 0
    for row in rows:
        size = len(row["text"].encode("utf-8"))
        if page and used + size > page_bytes:
            return DocumentPage(source, sections, tuple(page), row["chunk_index"])
        if not page and size > page_bytes:
            return DocumentPage(source, sections, (), row["chunk_index"] + 1, incomplete=True,
                                limitations=((f"chunk {row['chunk_index']} is larger than one "
                                              "page and was not returned"),))
        page.append(_chunk(row, source.index_revision))
        used += size
    last = page[-1].chunk_index if page else from_chunk - 1
    more = len(rows) == 200
    return DocumentPage(source, sections, tuple(page), last + 1 if more else None)
