"""Durable research-run records: scope, working state, evidence, steps, decisions.

Everything a run writes is private to its workspace and turn; it is never global
student memory. The rules this module enforces:

* **Only a running run is writable.** A run ends exactly once, in a terminal
  status; after that its state, evidence, steps, decisions and status are fixed,
  so a late worker cannot overwrite a cancellation or alter a settled record.
* **Evidence comes only from the run's pinned scope.** Every read must name a
  source in the run's stored manifest at its pinned revision and kind, and its
  chunk must be that source's indexed text right now (checked under share
  locks). The snapshot is taken from the database, not from caller fields.

* **Evidence IDs are issued here and never change.** Acquisition locks the run,
  reuses the ID of an identical (source, revision, chunk, text digest) item, and
  otherwise issues the next `eN`. Evidence and the step that issued it commit
  together. A replayed tool execution returns the step it already recorded, so
  the same IDs come back and nothing is issued twice.
* **State updates are optimistic.** A write names the revision it read; a stale
  writer gets `StateConflict` instead of overwriting newer work. State may cite
  only evidence this run actually issued.
* **Snapshots outlive the corpus.** Evidence rows hold the exact text and
  identity, with no live chunk key, so audit survives reindexing and removal.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from psycopg import errors
from psycopg.types.json import Jsonb

from app.advising.research_repository import (
    ChunkRead,
    ResearchScope,
    SourcePin,
    exact_chunks,
    manifest_source_pins,
    verify_manifest,
    verify_manifest_sources,
)
from app.advising.research_types import (
    MAX_EVIDENCE_BYTES,
    MAX_EVIDENCE_ITEMS,
    MAX_STATE_BYTES,
    EvidenceRecord,
    FinalDecision,
    ResearchState,
    validate_decision_limits,
    validate_evidence_references,
    validate_fact_support,
)
from app.db import queries
from app.util.digest import canonical_json_bytes, json_digest

TERMINAL_STATUSES = frozenset({"completed", "partial", "clarification",
                               "insufficient_evidence", "interrupted", "failed", "cancelled"})


class ResearchStoreError(RuntimeError):
    pass


class RunNotFound(ResearchStoreError, LookupError):
    """Unknown run, or a run of another workspace (reported identically)."""


class RunClosed(ResearchStoreError):
    """The run has ended; it accepts no further writes."""


class StateConflict(ResearchStoreError):
    """The state changed since the caller read it."""


class EvidenceLimit(ResearchStoreError):
    """Acquiring this would exceed the run's evidence register bounds."""


class InvalidResearchRecord(ResearchStoreError, ValueError):
    """A state or decision that fails structural validation; `problems` says why."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Acquired:
    """A read to register: one exact chunk and the source it came from."""

    chunk: ChunkRead
    source: SourcePin
    entity_refs: tuple[str, ...] = ()
    complete: bool = True


@dataclass(frozen=True)
class Acquisition:
    step_ordinal: int
    evidence_ids: tuple[str, ...]
    replayed: bool


@dataclass(frozen=True)
class RunRecord:
    run_id: UUID
    operation_id: UUID | None
    collection_id: UUID
    scope_digest: str
    scope_manifest: dict
    status: str
    stop_reason: str | None
    state_revision: int
    state: ResearchState | None


def _record(row, collection_id=None) -> RunRecord:
    return RunRecord(
        row["run_id"], row["operation_id"], row.get("collection_id", collection_id),
        row["scope_digest"], row["scope_manifest"], row["status"], row["stop_reason"],
        row["state_revision"],
        ResearchState.model_validate(row["state"], strict=False) if row["state"] else None,
    )


def _evidence_number(evidence_id: str) -> int:
    return int(evidence_id[1:])


class ResearchStore:
    def __init__(self, conn, workspace_key: str | None):
        self.conn = conn
        self.workspace_key = workspace_key

    # --- runs --------------------------------------------------------------

    def start(self, scope: ResearchScope, *, run_id: UUID, prompt_version: str,
              config_version: str, operation_id: UUID | None = None,
              root_trace_id: UUID | None = None, conversation_id: UUID | None = None,
              request_id: UUID | None = None) -> tuple[RunRecord, bool]:
        """Create the run, or return the one already bound to this conversation request.

        Returns (run, created). A duplicate request never starts a second run.
        """
        if scope.workspace_key != self.workspace_key:
            raise ValueError("scope belongs to another workspace")
        if conversation_id is not None and request_id is not None:
            existing = self._for_request(conversation_id, request_id)
            if existing is not None:
                return existing, False
        try:
            with self.conn.transaction():
                self.conn.execute(queries.get("insert_research_run"), {
                    "run_id": run_id, "operation_id": operation_id,
                    "root_trace_id": root_trace_id, "workspace_key": self.workspace_key,
                    "collection_id": scope.collection_id, "conversation_id": conversation_id,
                    "conversation_request_id": request_id,
                    "scope_manifest": Jsonb(scope.manifest()), "scope_digest": scope.digest,
                    "prompt_version": prompt_version, "config_version": config_version,
                    "state_revision": 0, "state": None, "status": "running",
                    "stop_reason": None,
                })
        except errors.UniqueViolation:
            # A concurrent duplicate request won the unique request binding.
            self.conn.commit()
            existing = self._for_request(conversation_id, request_id)
            if existing is None:
                raise
            return existing, False
        self.conn.commit()
        return self.get(run_id), True

    def _for_request(self, conversation_id: UUID, request_id: UUID) -> RunRecord | None:
        row = self.conn.execute(queries.get("get_research_run_for_conversation_request"), {
            "conversation_id": conversation_id, "request_id": request_id}).fetchone()
        self.conn.commit()
        return self.get(row["run_id"]) if row is not None else None

    def get(self, run_id: UUID) -> RunRecord:
        row = self.conn.execute(queries.get("get_research_run"), {"run_id": run_id}).fetchone()
        self.conn.commit()
        if row is None or row["workspace_key"] != self.workspace_key:
            raise RunNotFound(run_id)
        return _record(row)

    def _lock_any(self, run_id: UUID):
        row = self.conn.execute(queries.get("lock_research_run"), {"run_id": run_id}).fetchone()
        if row is None or row["workspace_key"] != self.workspace_key:
            raise RunNotFound(run_id)
        return row

    def _lock(self, run_id: UUID):
        """Lock a running run for a write; an ended run raises RunClosed."""
        row = self._lock_any(run_id)
        if row["status"] != "running":
            raise RunClosed(f"research run ended as {row['status']}")
        return row

    def finish(self, run_id: UUID, status: str, stop_reason: str | None = None, *,
               commit: bool = True) -> None:
        """End a running run. Repeating the same ending is a no-op; any other
        change to an ended run raises RunClosed."""
        if status not in TERMINAL_STATUSES:
            raise ValueError(f"not a terminal research status: {status!r}")
        with self.conn.transaction():
            current = self._lock_any(run_id)["status"]
            if current == "running":
                self.conn.execute(queries.get("set_research_run_status"), {
                    "run_id": run_id, "status": status, "stop_reason": stop_reason})
            elif current != status:
                raise RunClosed(f"research run already ended as {current}")
        if commit:
            self.conn.commit()

    # --- state -------------------------------------------------------------

    def update_state(self, run_id: UUID, state: ResearchState, *,
                     expected_revision: int) -> ResearchState:
        """Replace the working state if nobody wrote since `expected_revision`."""
        issued = {record.evidence_id for record in self.evidence(run_id)}
        problems = []
        cited = set(state.active_evidence_ids)
        cited.update(e for criterion in state.criteria for e in criterion.evidence_ids)
        cited.update(e for candidate in state.candidates for e in candidate.evidence_ids)
        problems.extend(f"state cites evidence {e!r} this run never issued"
                        for e in sorted(cited - issued))
        stored = state.model_copy(update={"revision": expected_revision + 1})
        size = len(canonical_json_bytes(stored.model_dump(mode="json")))
        if size > MAX_STATE_BYTES:
            problems.append(f"state is {size} bytes, over {MAX_STATE_BYTES}")
        if problems:
            raise InvalidResearchRecord(problems)
        with self.conn.transaction():
            self._lock(run_id)
            row = self.conn.execute(queries.get("update_research_state"), {
                "run_id": run_id, "expected_state_revision": expected_revision,
                "state": Jsonb(stored.model_dump(mode="json"))}).fetchone()
            if row is None:
                raise StateConflict(f"research state is no longer at revision {expected_revision}")
        self.conn.commit()
        return stored

    # --- evidence ----------------------------------------------------------

    def evidence(self, run_id: UUID) -> list[EvidenceRecord]:
        self.get(run_id)
        rows = self.conn.execute(queries.get("list_research_evidence"),
                                 {"run_id": run_id}).fetchall()
        self.conn.commit()
        records = [EvidenceRecord.model_validate(row["snapshot"], strict=False) for row in rows]
        return sorted(records, key=lambda record: _evidence_number(record.evidence_id))

    def acquire(self, run_id: UUID, reads: list[Acquired], *, action_kind: str, summary: str,
                tool_execution_id: UUID | None = None,
                child_trace_id: UUID | None = None) -> Acquisition:
        """Register reads as evidence and record the step that issued them, atomically."""
        with self.conn.transaction():
            result = self._acquire_locked(run_id, reads, action_kind, summary,
                                          tool_execution_id, child_trace_id)
        self.conn.commit()
        return result

    def _acquire_locked(self, run_id, reads, action_kind, summary, tool_execution_id,
                        child_trace_id) -> Acquisition:
        """Inside the caller's transaction: lock the run, then issue or replay."""
        run = self._lock(run_id)
        if tool_execution_id is not None:
            prior = self.conn.execute(queries.get("get_research_step_for_tool_execution"), {
                "run_id": run_id, "tool_execution_id": tool_execution_id}).fetchone()
            if prior is not None:
                return Acquisition(prior["ordinal"], tuple(prior["issued_evidence_ids"]), True)
        verified = self._verify_reads(run, reads)
        existing = [EvidenceRecord.model_validate(row["snapshot"], strict=False)
                    for row in self.conn.execute(queries.get("list_research_evidence"),
                                                 {"run_id": run_id}).fetchall()]
        by_key = {(r.source_id, r.source_revision, r.chunk_id, r.text_digest): r.evidence_id
                  for r in existing}
        next_number = max((_evidence_number(r.evidence_id) for r in existing), default=0) + 1
        total_bytes = sum(len(r.exact_text.encode("utf-8")) for r in existing)
        count = len(existing)
        ordinal = self.conn.execute(queries.get("next_research_step_ordinal"),
                                    {"run_id": run_id}).fetchone()["ordinal"]
        issued: list[str] = []
        new: list[EvidenceRecord] = []
        for read, (chunk, source) in zip(reads, verified, strict=True):
            key = (str(chunk.source_id), chunk.source_revision, str(chunk.chunk_id),
                   chunk.text_digest)
            if key in by_key:
                evidence_id = by_key[key]
            else:
                evidence_id = f"e{next_number}"
                next_number += 1
                count += 1
                total_bytes += len(chunk.text.encode("utf-8"))
                if count > MAX_EVIDENCE_ITEMS or total_bytes > MAX_EVIDENCE_BYTES:
                    raise EvidenceLimit(
                        f"evidence register is full ({MAX_EVIDENCE_ITEMS} items or "
                        f"{MAX_EVIDENCE_BYTES} bytes); nothing from this step was kept")
                by_key[key] = evidence_id
                new.append(self._snapshot(evidence_id, chunk, source, read, ordinal,
                                          child_trace_id))
            if evidence_id not in issued:
                issued.append(evidence_id)
        for record in new:
            snapshot = record.model_dump(mode="json")
            self.conn.execute(queries.get("insert_research_evidence"), {
                "run_id": run_id, "evidence_id": record.evidence_id,
                "snapshot": Jsonb(snapshot), "digest": json_digest(snapshot)})
        self.conn.execute(queries.get("insert_research_step"), {
            "run_id": run_id, "ordinal": ordinal, "tool_execution_id": tool_execution_id,
            "child_trace_id": child_trace_id, "action_kind": action_kind,
            "summary": summary[:500], "issued_evidence_ids": Jsonb(issued)})
        if reads:
            self.conn.execute(queries.get("update_research_step_details"), {
                "run_id": run_id, "ordinal": ordinal,
                "details": Jsonb({"read_entities": {
                    str(read.chunk.chunk_id): list(read.entity_refs) for read in reads}})})
        return Acquisition(ordinal, tuple(issued), False)

    def _verify_reads(self, run, reads: list[Acquired]) -> list[tuple[ChunkRead, SourcePin]]:
        """Each read's (chunk, source) as indexed now, or raise.

        A read outside the run's manifest, or whose chunk and source disagree with
        each other or with the database, is InvalidResearchRecord. A pinned source
        that has since changed revision or classification is ScopeChanged.
        """
        pinned = manifest_source_pins(run["scope_manifest"])
        problems = []
        for read in reads:
            chunk, source = read.chunk, read.source
            pin = pinned.get(chunk.source_id)
            if pin is None:
                problems.append(f"chunk {chunk.chunk_id} is from source {chunk.source_id}, "
                                "outside this run's scope")
            elif (source.source_id != chunk.source_id
                  or (chunk.source_revision, source.index_revision) != (pin[0], pin[0])
                  or source.source_kind != pin[1]):
                problems.append(f"chunk {chunk.chunk_id} does not match the pinned source "
                                f"{chunk.source_id}")
        if problems:
            raise InvalidResearchRecord(problems)
        revisions = {read.chunk.source_id: pinned[read.chunk.source_id][0] for read in reads}
        sources = verify_manifest_sources(self.conn, run["scope_manifest"], revisions)
        chunks = exact_chunks(self.conn, revisions, [read.chunk.chunk_id for read in reads])
        problems = [f"chunk {read.chunk.chunk_id} is not the indexed text of its source"
                    for read in reads if chunks.get(read.chunk.chunk_id) != read.chunk]
        problems.extend(f"source {read.source.source_id} metadata does not match its pinned source"
                        for read in reads if sources[read.chunk.source_id] != read.source)
        if problems:
            raise InvalidResearchRecord(problems)
        return [(chunks[read.chunk.chunk_id], sources[read.chunk.source_id]) for read in reads]

    @staticmethod
    def _snapshot(evidence_id: str, chunk: ChunkRead, source: SourcePin, read: Acquired,
                  step: int, child_trace_id: UUID | None) -> EvidenceRecord:
        return EvidenceRecord(
            evidence_id=evidence_id, source_id=str(chunk.source_id),
            source_revision=chunk.source_revision, origin_key=source.origin_key,
            source_kind=source.source_kind, source_name=source.filename[:400],
            publisher=source.publisher, geography=source.geography, occupation_id=source.occupation_id,
            source_url=source.source_url, edition=source.edition,
            acquired_at=source.acquired_at, published_at=source.published_at,
            title=source.title, institution=source.institution, issuing_unit=source.issuing_unit,
            document_roles=source.document_roles, applicability=dict(source.applicability),
            applicability_status=source.applicability_status,
            metadata_revision=source.metadata_revision,
            chunk_id=str(chunk.chunk_id),
            locator_id=str(chunk.locator_id) if chunk.locator_id else None,
            locator_label=chunk.locator_label[:400] or "(unlabeled)", exact_text=chunk.text,
            text_digest=chunk.text_digest, entity_refs=tuple(read.entity_refs),
            acquired_step=step,
            child_trace_id=str(child_trace_id) if child_trace_id else None,
            content_completeness="complete" if read.complete else "incomplete",
        )

    def steps(self, run_id: UUID) -> list[dict]:
        self.get(run_id)
        rows = self.conn.execute(queries.get("list_research_steps"),
                                 {"run_id": run_id}).fetchall()
        self.conn.commit()
        return [dict(row) for row in rows]

    def record_action(self, run_id: UUID, *, action_kind: str, details: dict,
                      child_trace_id: UUID | None = None) -> None:
        """Audit a discovery, deterministic check, or rejected final proposal."""
        with self.conn.transaction():
            acquired = self._acquire_locked(run_id, [], action_kind, action_kind,
                                            None, child_trace_id)
            self.conn.execute(queries.get("update_research_step_details"), {
                "run_id": run_id, "ordinal": acquired.step_ordinal, "details": Jsonb(details)})
        self.conn.commit()

    # --- decisions ---------------------------------------------------------

    def record_decision(self, run_id: UUID, decision: FinalDecision, *, render_version: str,
                        response_id: UUID | None = None, commit: bool = True,
                        supplied: set[str] | None = None) -> str:
        """Store a structurally valid decision; its digest identifies it in audit."""
        # Publication callers supply the already validated final-request IDs so
        # this method does not commit their source/profile/operation locks.
        issued = supplied if supplied is not None else {
            record.evidence_id for record in self.evidence(run_id)}
        problems = (validate_decision_limits(decision)
                    + validate_evidence_references(decision, issued)
                    + validate_fact_support(decision))
        if problems:
            raise InvalidResearchRecord(problems)
        payload = decision.model_dump(mode="json")
        digest = json_digest(payload)
        with self.conn.transaction():
            run = self._lock(run_id)
            verify_manifest(self.conn, run["scope_manifest"])
            self.conn.execute(queries.get("insert_research_decision"), {
                "run_id": run_id, "response_id": response_id, "render_version": render_version,
                "decision": Jsonb(payload), "digest": digest})
        if commit:
            self.conn.commit()
        return digest
