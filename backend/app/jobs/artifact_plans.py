"""Durable frozen plans and immutable checkpoints for dependent artifact jobs.

This module stores validated plan/unit state only. It does not call providers,
advance job leases, or publish partial artifact data.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid4

from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from app.db import queries
from app.db.connection import connection_transaction
from app.jobs.store import LeaseLost
from app.util.digest import canonical_json_bytes, json_digest
from app.util.workspace import is_valid_workspace_key

_ARTIFACT_KINDS = frozenset({"document", "workbook", "html"})
_UNIT_KINDS = frozenset({"section", "worksheet", "repair", "assembly"})
_UNIT_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
_FAILURE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_MAX_FROZEN_BYTES = 2_000_000
_MAX_PLAN_INPUT_BYTES = 8_000_000
_MAX_CHECKPOINT_BYTES = 4_000_000
_MAX_UNITS = 100
_MAX_SOURCES = 500


class ArtifactPlanError(RuntimeError):
    """Safe artifact-plan storage error."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.safe_message = message
        super().__init__(message)


class ArtifactPlanNotFound(ArtifactPlanError):
    def __init__(self):
        super().__init__("plan_not_found", "The artifact plan is unavailable.")


class ArtifactPlanConflict(ArtifactPlanError):
    def __init__(self):
        super().__init__("plan_conflict", "The artifact plan inputs have changed.")


class ArtifactUnitConflict(ArtifactPlanError):
    def __init__(self, message: str = "The artifact unit state has changed."):
        super().__init__("unit_conflict", message)


class ArtifactUnitNotReady(ArtifactPlanError):
    def __init__(self):
        super().__init__("unit_not_ready", "The artifact unit dependencies are not validated.")


class ArtifactBindingStale(ArtifactPlanError):
    def __init__(self):
        super().__init__("stale_binding", "A frozen source or model connection changed.")


@dataclass(frozen=True)
class ArtifactRunQuotas:
    max_units: int = 100
    max_input_bytes: int = _MAX_PLAN_INPUT_BYTES
    max_checkpoint_bytes: int = 16_000_000
    max_output_tokens: int = 32_000
    max_repairs_per_unit: int = 1
    max_blocks: int = 1_000
    max_cells: int = 10_000

    def __post_init__(self) -> None:
        limits = {
            "max_units": (1, _MAX_UNITS),
            "max_input_bytes": (1, _MAX_PLAN_INPUT_BYTES),
            "max_checkpoint_bytes": (1, 32_000_000),
            "max_output_tokens": (1, 32_000),
            "max_repairs_per_unit": (0, 2),
            "max_blocks": (1, 10_000),
            "max_cells": (1, 100_000),
        }
        for name, (minimum, maximum) in limits.items():
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"{name} is outside its supported bound")

    def as_json(self) -> dict[str, int]:
        return {
            "max_units": self.max_units,
            "max_input_bytes": self.max_input_bytes,
            "max_checkpoint_bytes": self.max_checkpoint_bytes,
            "max_output_tokens": self.max_output_tokens,
            "max_repairs_per_unit": self.max_repairs_per_unit,
            "max_blocks": self.max_blocks,
            "max_cells": self.max_cells,
        }


@dataclass(frozen=True)
class ArtifactRunSource:
    source_id: UUID
    collection_id: UUID
    file_hash: str
    index_revision: int

    def __post_init__(self) -> None:
        if not self.file_hash or len(self.file_hash) > 256:
            raise ValueError("source file hash is invalid")
        if type(self.index_revision) is not int or self.index_revision < 0:
            raise ValueError("source index revision is invalid")

    def as_json(self) -> dict[str, Any]:
        return {
            "source_id": str(self.source_id),
            "collection_id": str(self.collection_id),
            "file_hash": self.file_hash,
            "index_revision": self.index_revision,
        }


@dataclass(frozen=True)
class ArtifactRunExcerpt:
    excerpt_id: int
    source_id: UUID
    chunk_id: UUID
    locator_id: UUID
    source_name: str
    locator_label: str
    text_snapshot: str

    def __post_init__(self) -> None:
        if type(self.excerpt_id) is not int or not 1 <= self.excerpt_id <= 500:
            raise ValueError("excerpt identity is outside its supported bound")
        if not 1 <= len(self.locator_label) <= 500:
            raise ValueError("excerpt locator is invalid")
        if not 1 <= len(self.source_name) <= 500:
            raise ValueError("excerpt source name is invalid")
        if not 1 <= len(self.text_snapshot.encode("utf-8")) <= 100_000:
            raise ValueError("excerpt text is outside its supported bound")

    def as_json(self) -> dict[str, Any]:
        return {
            "excerpt_id": self.excerpt_id, "source_id": str(self.source_id),
            "chunk_id": str(self.chunk_id), "locator_id": str(self.locator_id),
            "locator_label": self.locator_label, "source_name": self.source_name,
            "text_snapshot": self.text_snapshot,
        }


@dataclass(frozen=True)
class ArtifactRunUnitInput:
    unit_key: str
    unit_kind: Literal["section", "worksheet", "repair", "assembly"]
    input_snapshot: Mapping[str, Any]
    source_ids: tuple[UUID, ...] = ()
    depends_on: tuple[str, ...] = ()
    profile_id: UUID | None = None
    profile_revision: int | None = None

    def __post_init__(self) -> None:
        if not _UNIT_KEY.fullmatch(self.unit_key):
            raise ValueError("unit key has invalid characters or length")
        if self.unit_kind not in _UNIT_KINDS:
            raise ValueError("unsupported artifact unit kind")
        if not isinstance(self.input_snapshot, Mapping):
            raise TypeError("unit input snapshot must be an object")
        if (self.profile_id is None) != (self.profile_revision is None):
            raise ValueError("unit profile identity must be complete")
        if self.profile_revision is not None and (
            type(self.profile_revision) is not int or self.profile_revision < 1
        ):
            raise ValueError("unit profile revision is invalid")
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("unit source IDs must be unique")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("unit dependencies must be unique")

    def as_json(self, *, canonical: bool = False) -> dict[str, Any]:
        """The unit's JSON form; `canonical` orders set-like fields so equal plans hash equal."""
        source_ids = [str(source_id) for source_id in self.source_ids]
        depends_on = list(self.depends_on)
        if canonical:
            source_ids.sort()
            depends_on.sort()
        return {
            "unit_key": self.unit_key,
            "unit_kind": self.unit_kind,
            "input_snapshot": dict(self.input_snapshot),
            "source_ids": source_ids,
            "depends_on": depends_on,
            "profile_id": str(self.profile_id) if self.profile_id else None,
            "profile_revision": self.profile_revision,
        }


@dataclass(frozen=True)
class ArtifactRunPlanInput:
    workspace_key: str
    collection_id: UUID
    artifact_kind: Literal["document", "workbook", "html"]
    frozen_inputs: Mapping[str, Any]
    units: tuple[ArtifactRunUnitInput, ...]
    sources: tuple[ArtifactRunSource, ...] = ()
    excerpts: tuple[ArtifactRunExcerpt, ...] = ()
    quotas: ArtifactRunQuotas = ArtifactRunQuotas()
    target_artifact_id: UUID | None = None
    target_revision_id: UUID | None = None
    profile_id: UUID | None = None
    profile_revision: int | None = None

    def __post_init__(self) -> None:
        if not is_valid_workspace_key(self.workspace_key):
            raise ValueError("workspace key must contain 1 to 200 characters")
        if self.artifact_kind not in _ARTIFACT_KINDS:
            raise ValueError("unsupported artifact kind")
        if not isinstance(self.frozen_inputs, Mapping):
            raise TypeError("frozen plan inputs must be an object")
        if (self.target_artifact_id is None) != (self.target_revision_id is None):
            raise ValueError("target artifact and expected revision must be supplied together")
        if (self.profile_id is None) != (self.profile_revision is None):
            raise ValueError("profile identity must be complete")
        if self.profile_revision is not None and (
            type(self.profile_revision) is not int or self.profile_revision < 1
        ):
            raise ValueError("profile revision is invalid")
        if not 1 <= len(self.units) <= _MAX_UNITS:
            raise ValueError("a plan must contain 1 to 100 units")
        if len(self.sources) > _MAX_SOURCES:
            raise ValueError("a plan cannot bind more than 500 sources")
        if len(self.excerpts) > 500:
            raise ValueError("a plan cannot bind more than 500 exact excerpts")
        _validate_dag(self)


@dataclass(frozen=True)
class ArtifactRunPlan:
    job_id: UUID
    workspace_key: str
    collection_id: UUID
    artifact_kind: str
    status: str
    input_fingerprint: str
    frozen_inputs: dict[str, Any]
    quotas: dict[str, int]
    target_artifact_id: UUID | None
    target_revision_id: UUID | None
    profile_id: UUID | None
    profile_revision: int | None


@dataclass(frozen=True)
class ArtifactRunUnit:
    unit_id: UUID
    job_id: UUID
    ordinal: int
    unit_key: str
    unit_kind: str
    status: str
    input_fingerprint: str
    input_snapshot: dict[str, Any]
    source_ids: tuple[UUID, ...]
    depends_on: tuple[UUID, ...]
    profile_id: UUID | None
    profile_revision: int | None
    checkpoint: dict[str, Any] | None
    checkpoint_fingerprint: str | None
    failure_code: str | None


def _canonical(value: Any) -> tuple[str, bytes]:
    try:
        encoded = canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("artifact plan values must be finite JSON data") from exc
    return encoded.decode("utf-8"), encoded


def _plan_fingerprint(spec: ArtifactRunPlanInput) -> str:
    return json_digest({
        "workspace_key": spec.workspace_key,
        "collection_id": str(spec.collection_id),
        "artifact_kind": spec.artifact_kind,
        "frozen_inputs": dict(spec.frozen_inputs),
        "quotas": spec.quotas.as_json(),
        "target_artifact_id": str(spec.target_artifact_id) if spec.target_artifact_id else None,
        "target_revision_id": str(spec.target_revision_id) if spec.target_revision_id else None,
        "profile_id": str(spec.profile_id) if spec.profile_id else None,
        "profile_revision": spec.profile_revision,
        "sources": [
            source.as_json()
            for source in sorted(spec.sources, key=lambda source: str(source.source_id))
        ],
        "excerpts": [excerpt.as_json() for excerpt in spec.excerpts],
        # Unit order is part of the frozen artifact structure (section/sheet
        # order). Sorting here would let a reordered plan replay as identical.
        "units": [unit.as_json(canonical=True) for unit in spec.units],
    })


def _validate_dag(spec: ArtifactRunPlanInput) -> None:
    units = {unit.unit_key: unit for unit in spec.units}
    if len(units) != len(spec.units):
        raise ValueError("unit keys must be unique")
    if len(units) > spec.quotas.max_units:
        raise ValueError("plan contains more units than its max_units quota")
    source_ids = {source.source_id for source in spec.sources}
    if len(source_ids) != len(spec.sources):
        raise ValueError("source bindings must be unique")
    if any(source.collection_id != spec.collection_id for source in spec.sources):
        raise ValueError("all source bindings must belong to the frozen collection")
    if len({excerpt.excerpt_id for excerpt in spec.excerpts}) != len(spec.excerpts):
        raise ValueError("excerpt IDs must be unique")
    if len({excerpt.chunk_id for excerpt in spec.excerpts}) != len(spec.excerpts):
        raise ValueError("excerpt chunks must be unique")
    if any(excerpt.source_id not in source_ids for excerpt in spec.excerpts):
        raise ValueError("every exact excerpt must use a frozen source")
    for unit in spec.units:
        if unit.profile_id != spec.profile_id or unit.profile_revision != spec.profile_revision:
            raise ValueError("every unit must use the frozen profile revision")
        if not set(unit.source_ids) <= source_ids:
            raise ValueError("a unit source is outside the frozen source set")
        if any(dependency not in units for dependency in unit.depends_on):
            raise ValueError("unit dependency is not part of this plan")
        if unit.unit_key in unit.depends_on:
            raise ValueError("a unit cannot depend on itself")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(key: str) -> None:
        if key in visiting:
            raise ValueError("unit dependency graph contains a cycle")
        if key in visited:
            return
        visiting.add(key)
        for dependency in units[key].depends_on:
            visit(dependency)
        visiting.remove(key)
        visited.add(key)

    for key in units:
        visit(key)
    frozen_value = {
        "frozen_inputs": dict(spec.frozen_inputs),
        "sources": [source.as_json() for source in spec.sources],
        "excerpts": [excerpt.as_json() for excerpt in spec.excerpts],
        "units": [unit.as_json() for unit in spec.units],
    }
    _, encoded = _canonical(frozen_value)
    if len(encoded) > spec.quotas.max_input_bytes:
        raise ValueError("frozen plan inputs exceed their configured byte quota")


def _plan_from_row(row: Mapping[str, Any]) -> ArtifactRunPlan:
    return ArtifactRunPlan(
        job_id=row["job_id"], workspace_key=row["workspace_key"],
        collection_id=row["collection_id"], artifact_kind=row["artifact_kind"],
        status=row["status"], input_fingerprint=row["input_fingerprint"].strip(),
        frozen_inputs=dict(row["frozen_inputs"]), quotas=dict(row["quotas"]),
        target_artifact_id=row["target_artifact_id"], target_revision_id=row["target_revision_id"],
        profile_id=row["profile_id"], profile_revision=row["profile_revision"],
    )


class ArtifactRunPlanStore:
    """Owner-scoped plan store with active-lease transitions and immutable checkpoints."""

    def __init__(self, connect):
        self._connect = connect

    def _transaction(self):
        return connection_transaction(self._connect)

    def create_plan(
        self, job_id: UUID, lease_token: UUID, spec: ArtifactRunPlanInput
    ) -> ArtifactRunPlan:
        _validate_dag(spec)
        fingerprint = _plan_fingerprint(spec)
        with self._transaction() as conn:
            self._lock_active_job(conn, job_id, spec.workspace_key, lease_token)
            self._assert_source_bindings(conn, job_id, spec.workspace_key, spec)
            self._assert_excerpt_bindings(conn, spec)
            self._assert_profile(conn, spec.profile_id, spec.profile_revision)
            self._assert_target_current(
                conn, spec.workspace_key, spec.target_artifact_id, spec.target_revision_id
            )
            frozen_text, frozen_bytes = _canonical(dict(spec.frozen_inputs))
            if len(frozen_bytes) > _MAX_FROZEN_BYTES:
                raise ArtifactPlanError(
                    "plan_input_quota", "Frozen plan inputs exceed the durable storage bound."
                )
            database_bytes = conn.execute(queries.get("measure_artifact_run_json_bytes"), {
                "value": Jsonb(json.loads(frozen_text)),
            }).fetchone()["value_bytes"]
            if database_bytes > _MAX_FROZEN_BYTES:
                raise ArtifactPlanError(
                    "plan_input_quota", "Frozen plan inputs exceed the durable storage bound."
                )
            plan_row = conn.execute(queries.get("insert_artifact_run_plan"), {
                "job_id": job_id,
                "workspace_key": spec.workspace_key,
                "collection_id": spec.collection_id,
                "artifact_kind": spec.artifact_kind,
                "input_fingerprint": fingerprint,
                "frozen_inputs": Jsonb(json.loads(frozen_text)),
                "quotas": Jsonb(spec.quotas.as_json()),
                "target_artifact_id": spec.target_artifact_id,
                "target_revision_id": spec.target_revision_id,
                "profile_id": spec.profile_id,
                "profile_revision": spec.profile_revision,
            }).fetchone()
            if plan_row is None:
                existing = conn.execute(queries.get("get_artifact_run_plan"), {
                    "job_id": job_id, "workspace_key": spec.workspace_key,
                }).fetchone()
                if existing is None:
                    raise ArtifactPlanNotFound()
                plan = _plan_from_row(existing)
                if plan.input_fingerprint != fingerprint:
                    raise ArtifactPlanConflict()
                return plan
            id_by_key: dict[str, UUID] = {}
            for source in sorted(spec.sources, key=lambda item: str(item.source_id)):
                conn.execute(queries.get("insert_artifact_run_plan_source"), {
                    "job_id": job_id, "source_id": source.source_id,
                    "collection_id": source.collection_id, "file_hash": source.file_hash,
                    "index_revision": source.index_revision,
                })
            for excerpt in sorted(spec.excerpts, key=lambda item: item.excerpt_id):
                conn.execute(queries.get("insert_artifact_run_plan_excerpt"), {
                    "job_id": job_id, "excerpt_id": excerpt.excerpt_id,
                    "source_id": excerpt.source_id, "chunk_id": excerpt.chunk_id,
                    "locator_id": excerpt.locator_id,
                    "source_name": excerpt.source_name,
                    "locator_label": excerpt.locator_label,
                    "text_snapshot": excerpt.text_snapshot,
                    "excerpt_fingerprint": json_digest({
                        "chunk_id": str(excerpt.chunk_id),
                        "locator_id": str(excerpt.locator_id),
                        "source_id": str(excerpt.source_id),
                        "source_name": excerpt.source_name,
                        "locator_label": excerpt.locator_label,
                        "text_snapshot": excerpt.text_snapshot,
                    }),
                })
            for ordinal, unit in enumerate(spec.units):
                unit_id = uuid4()
                id_by_key[unit.unit_key] = unit_id
                unit_fingerprint = json_digest({
                    "unit_key": unit.unit_key, "unit_kind": unit.unit_kind,
                    "input_snapshot": dict(unit.input_snapshot),
                    "source_ids": sorted(str(source_id) for source_id in unit.source_ids),
                    "depends_on": sorted(unit.depends_on),
                    "profile_id": str(unit.profile_id) if unit.profile_id else None,
                    "profile_revision": unit.profile_revision,
                })
                conn.execute(queries.get("insert_artifact_run_unit"), {
                    "unit_id": unit_id, "job_id": job_id, "ordinal": ordinal,
                    "unit_key": unit.unit_key, "unit_kind": unit.unit_kind,
                    "input_fingerprint": unit_fingerprint,
                    "input_snapshot": Jsonb(dict(unit.input_snapshot)),
                    "profile_id": unit.profile_id, "profile_revision": unit.profile_revision,
                })
                for source_id in sorted(unit.source_ids, key=str):
                    conn.execute(queries.get("insert_artifact_run_unit_source"), {
                        "job_id": job_id, "unit_id": unit_id, "source_id": source_id,
                    })
            for unit in spec.units:
                for dependency in unit.depends_on:
                    conn.execute(queries.get("insert_artifact_run_unit_dependency"), {
                        "job_id": job_id, "unit_id": id_by_key[unit.unit_key],
                        "depends_on_unit_id": id_by_key[dependency],
                    })
            return _plan_from_row(plan_row)

    def get_plan(self, job_id: UUID, workspace_key: str) -> ArtifactRunPlan:
        with self._transaction() as conn:
            row = conn.execute(queries.get("get_artifact_run_plan"), {
                "job_id": job_id, "workspace_key": workspace_key,
            }).fetchone()
            if row is None:
                raise ArtifactPlanNotFound()
            return _plan_from_row(row)

    def assert_publishable(
        self, conn, job_id: UUID, workspace_key: str, lease_token: UUID,
        artifact_kind: str, spec_data: Mapping[str, Any], *, check_target: bool = True,
    ) -> bool:
        """Fence planned publication against the frozen target and indexed evidence."""
        self._lock_active_job(conn, job_id, workspace_key, lease_token)
        plan = self._lock_plan(conn, job_id, workspace_key)
        if plan["artifact_kind"] != artifact_kind or plan["status"] != "validated":
            raise ArtifactBindingStale()
        units = conn.execute(queries.get("list_artifact_run_units"), {
            "job_id": job_id, "workspace_key": workspace_key,
        }).fetchall()
        if not units or any(unit["status"] != "validated" or unit["checkpoint"] is None
                            for unit in units):
            raise ArtifactBindingStale()
        assembly = next((unit for unit in units if unit["unit_key"] == "assemble"), None)
        if assembly is None or json_digest(assembly["checkpoint"].get("spec")) != json_digest(
            dict(spec_data)
        ):
            raise ArtifactBindingStale()
        if check_target:
            self._assert_target_current(
                conn, workspace_key, plan["target_artifact_id"], plan["target_revision_id"]
            )
        for unit in units:
            self._assert_unit_bindings_current(
                conn, job_id, workspace_key, plan, unit, check_target=check_target,
            )
        return True

    def list_units(self, job_id: UUID, workspace_key: str) -> tuple[ArtifactRunUnit, ...]:
        with self._transaction() as conn:
            rows = conn.execute(queries.get("list_artifact_run_units"), {
                "job_id": job_id, "workspace_key": workspace_key,
            }).fetchall()
            if not rows:
                # An empty list must not reveal whether another workspace owns the job.
                plan = conn.execute(queries.get("get_artifact_run_plan"), {
                    "job_id": job_id, "workspace_key": workspace_key,
                }).fetchone()
                if plan is None:
                    raise ArtifactPlanNotFound()
            source_rows = conn.execute(queries.get("list_artifact_run_unit_sources"), {
                "job_id": job_id, "workspace_key": workspace_key,
            }).fetchall()
            dependency_rows = conn.execute(
                queries.get("list_artifact_run_unit_dependencies"),
                {"job_id": job_id, "workspace_key": workspace_key},
            ).fetchall()
            sources: dict[UUID, list[UUID]] = {}
            dependencies: dict[UUID, list[UUID]] = {}
            for row in source_rows:
                sources.setdefault(row["unit_id"], []).append(row["source_id"])
            for row in dependency_rows:
                dependencies.setdefault(row["unit_id"], []).append(row["depends_on_unit_id"])
            return tuple(
                ArtifactRunUnit(
                    unit_id=row["unit_id"], job_id=row["job_id"], ordinal=row["ordinal"],
                    unit_key=row["unit_key"], unit_kind=row["unit_kind"], status=row["status"],
                    input_fingerprint=row["input_fingerprint"].strip(),
                    input_snapshot=dict(row["input_snapshot"]),
                    source_ids=tuple(sources.get(row["unit_id"], ())),
                    depends_on=tuple(dependencies.get(row["unit_id"], ())),
                    profile_id=row["profile_id"], profile_revision=row["profile_revision"],
                    checkpoint=dict(row["checkpoint"]) if row["checkpoint"] is not None else None,
                    checkpoint_fingerprint=(row["checkpoint_fingerprint"].strip()
                                            if row["checkpoint_fingerprint"] else None),
                    failure_code=row["failure_code"],
                )
                for row in rows
            )

    def list_excerpts(self, job_id: UUID, workspace_key: str) -> tuple[ArtifactRunExcerpt, ...]:
        with self._transaction() as conn:
            plan = conn.execute(queries.get("get_artifact_run_plan"), {
                "job_id": job_id, "workspace_key": workspace_key,
            }).fetchone()
            if plan is None:
                raise ArtifactPlanNotFound()
            rows = conn.execute(queries.get("list_artifact_run_plan_excerpts"), {
                "job_id": job_id, "workspace_key": workspace_key,
            }).fetchall()
            return tuple(ArtifactRunExcerpt(
                excerpt_id=row["excerpt_id"], source_id=row["source_id"],
                chunk_id=row["chunk_id"], locator_id=row["locator_id"],
                source_name=row["source_name"],
                locator_label=row["locator_label"],
                text_snapshot=row["text_snapshot"],
            ) for row in rows)

    def list_attempts(self, job_id: UUID, workspace_key: str) -> tuple[dict[str, Any], ...]:
        with self._transaction() as conn:
            plan = conn.execute(queries.get("get_artifact_run_plan"), {
                "job_id": job_id, "workspace_key": workspace_key,
            }).fetchone()
            if plan is None:
                raise ArtifactPlanNotFound()
            rows = conn.execute(queries.get("list_artifact_run_unit_attempts"), {
                "job_id": job_id, "workspace_key": workspace_key,
            }).fetchall()
            return tuple(dict(row) for row in rows)

    def mark_calling(
        self, job_id: UUID, workspace_key: str, lease_token: UUID,
        unit_key: str, attempt_no: int,
    ) -> ArtifactRunUnit:
        if type(attempt_no) is not int or attempt_no < 1:
            raise ValueError("operation attempt number must be positive")
        with self._transaction() as conn:
            self._lock_active_job(conn, job_id, workspace_key, lease_token)
            plan = self._lock_plan(conn, job_id, workspace_key)
            if plan["status"] not in {"ready", "running"}:
                raise ArtifactUnitConflict("The artifact plan is no longer executable.")
            unit = self._lock_unit(conn, job_id, workspace_key, unit_key)
            if unit["status"] not in {"pending", "needs_repair"}:
                raise ArtifactUnitConflict("Only an unstarted or repairable unit may begin a provider call.")
            attempts = conn.execute(queries.get("artifact_run_unit_next_attempt_no"), {
                "job_id": job_id, "unit_id": unit["unit_id"],
            }).fetchone()["attempt_no"] - 1
            if attempts >= 1 + int(plan["quotas"].get("max_repairs_per_unit", 0)):
                raise ArtifactUnitConflict("The unit repair quota was exhausted.")
            self._assert_unit_ready(conn, job_id, unit)
            self._assert_unit_bindings_current(conn, job_id, workspace_key, plan, unit)
            call = conn.execute(queries.get("get_reserved_operation_call_for_artifact_unit"), {
                "job_id": job_id, "attempt_no": attempt_no,
            }).fetchone()
            if (call is None or call["status"] != "reserved"
                    or call["workspace_key"] != workspace_key
                    or call["deadline_at"] <= call["observed_at"]):
                raise ArtifactUnitConflict("The provider reservation does not belong to this run.")
            try:
                conn.execute(queries.get("insert_artifact_run_unit_call"), {
                    "job_id": job_id, "unit_id": unit["unit_id"], "attempt_no": attempt_no,
                })
            except UniqueViolation as exc:
                raise ArtifactUnitConflict("The provider attempt is already assigned to a unit.") from exc
            updated = conn.execute(queries.get("update_artifact_run_unit_calling"), {
                "job_id": job_id, "unit_id": unit["unit_id"],
            }).fetchone()
            if updated is None:
                raise ArtifactUnitConflict()
            conn.execute(queries.get("update_artifact_run_plan_running"), {
                "job_id": job_id, "workspace_key": workspace_key,
            })
            return self._unit_with_links(conn, updated, workspace_key)

    def mark_invalid_attempt(
        self, job_id: UUID, workspace_key: str, lease_token: UUID, unit_key: str,
        attempt_no: int, candidate_text: str, validation_issues: list[str],
    ) -> ArtifactRunUnit:
        """Persist a known invalid response for a bounded repair; never clip it into acceptance."""
        if not isinstance(candidate_text, str) or not validation_issues:
            raise ValueError("invalid attempt needs candidate text and validation issues")
        if any(not isinstance(issue, str) or not 1 <= len(issue) <= 500 for issue in validation_issues):
            raise ValueError("validation issues must be bounded strings")
        issues = list(validation_issues[:20])
        if len(issues) != len(validation_issues):
            raise ValueError("too many validation issues")
        digest = hashlib.sha256(candidate_text.encode("utf-8")).hexdigest()
        encoded = candidate_text.encode("utf-8")
        retained = candidate_text if len(encoded) <= _MAX_CHECKPOINT_BYTES else None
        with self._transaction() as conn:
            self._lock_active_job(conn, job_id, workspace_key, lease_token)
            plan = self._lock_plan(conn, job_id, workspace_key)
            if plan["status"] not in {"ready", "running"}:
                raise ArtifactUnitConflict("The artifact plan is terminal.")
            unit = self._lock_unit(conn, job_id, workspace_key, unit_key)
            if unit["status"] != "calling":
                raise ArtifactUnitConflict("Only an active provider attempt can be repaired.")
            call = conn.execute(queries.get("get_reserved_operation_call_for_artifact_unit"), {
                "job_id": job_id, "attempt_no": attempt_no,
            }).fetchone()
            link = conn.execute(queries.get("artifact_run_call_attempt_exists"), {
                "job_id": job_id, "unit_id": unit["unit_id"], "attempt_no": attempt_no,
            }).fetchone()
            if call is None or call["status"] not in {"completed", "unknown"} or link is None:
                raise ArtifactUnitConflict("Only a completed, linked provider response can be repaired.")
            conn.execute(queries.get("insert_artifact_run_unit_attempt"), {
                "job_id": job_id, "unit_id": unit["unit_id"], "attempt_no": attempt_no,
                "outcome": "invalid", "candidate_text": retained,
                "candidate_sha256": digest, "candidate_truncated": retained is None,
                "validation_issues": Jsonb(issues),
            })
            invalid_count = conn.execute(queries.get("artifact_run_unit_invalid_attempts"), {
                "job_id": job_id, "unit_id": unit["unit_id"],
            }).fetchone()["n"]
            repair_limit = int(plan["quotas"].get("max_repairs_per_unit", 0))
            if invalid_count <= repair_limit:
                updated = conn.execute(queries.get("update_artifact_run_unit_needs_repair"), {
                    "job_id": job_id, "unit_id": unit["unit_id"],
                }).fetchone()
            else:
                updated = conn.execute(queries.get("update_artifact_run_unit_terminal"), {
                    "job_id": job_id, "unit_id": unit["unit_id"],
                    "status": "failed", "failure_code": "invalid_output",
                }).fetchone()
                conn.execute(queries.get("update_artifact_run_plan_terminal"), {
                    "job_id": job_id, "workspace_key": workspace_key, "status": "failed",
                })
            if updated is None:
                raise ArtifactUnitConflict()
            return self._unit_with_links(conn, updated, workspace_key)

    def mark_validated(
        self, job_id: UUID, workspace_key: str, lease_token: UUID,
        unit_key: str, checkpoint: Mapping[str, Any],
    ) -> ArtifactRunUnit:
        if not isinstance(checkpoint, Mapping):
            raise TypeError("validated checkpoint must be a JSON object")
        rendered, encoded = _canonical(dict(checkpoint))
        if len(encoded) > _MAX_CHECKPOINT_BYTES:
            raise ValueError("validated checkpoint exceeds its storage bound")
        with self._transaction() as conn:
            self._lock_active_job(conn, job_id, workspace_key, lease_token)
            plan = self._lock_plan(conn, job_id, workspace_key)
            if plan["status"] not in {"ready", "running"}:
                raise ArtifactUnitConflict("The artifact plan no longer accepts checkpoints.")
            unit = self._lock_unit(conn, job_id, workspace_key, unit_key)
            if unit["status"] not in {"pending", "calling"} or unit["checkpoint"] is not None:
                raise ArtifactUnitConflict("Only an unfinished unit may be validated once.")
            self._assert_unit_ready(conn, job_id, unit)
            self._assert_unit_bindings_current(conn, job_id, workspace_key, plan, unit)
            has_call = conn.execute(queries.get("artifact_run_call_link_exists"), {
                "job_id": job_id, "unit_id": unit["unit_id"],
            }).fetchone() is not None
            if unit["status"] == "calling" and not has_call:
                raise ArtifactUnitConflict("The provider call intent is missing.")
            if unit["status"] == "pending" and has_call:
                raise ArtifactUnitConflict("A provider attempt cannot be replayed as a local step.")
            json_size = conn.execute(queries.get("measure_artifact_run_json_bytes"), {
                "value": Jsonb(json.loads(rendered)),
            }).fetchone()["value_bytes"]
            current_bytes = conn.execute(queries.get("artifact_run_checkpoint_bytes"), {
                "job_id": job_id,
            }).fetchone()["checkpoint_bytes"]
            max_bytes = int(plan["quotas"]["max_checkpoint_bytes"])
            if current_bytes + json_size > max_bytes:
                raise ArtifactPlanError("checkpoint_quota", "Validated checkpoints exceed the plan quota.")
            raw_usage = checkpoint.get("quota_usage", {})
            if not isinstance(raw_usage, Mapping):
                raise ArtifactPlanError("invalid_checkpoint", "Checkpoint quota usage is invalid.")
            usage = dict(raw_usage)
            if set(usage) - {"blocks", "cells"} or any(
                type(value) is not int or value < 0 for value in usage.values()
            ):
                raise ArtifactPlanError("invalid_checkpoint", "Checkpoint quota usage is invalid.")
            current_usage = conn.execute(queries.get("artifact_run_checkpoint_usage"), {
                "job_id": job_id,
            }).fetchone()
            if (current_usage["blocks"] + usage.get("blocks", 0) > int(plan["quotas"]["max_blocks"])
                    or current_usage["cells"] + usage.get("cells", 0)
                    > int(plan["quotas"]["max_cells"])):
                raise ArtifactPlanError("content_quota", "Validated content exceeds the plan quota.")
            updated = conn.execute(queries.get("update_artifact_run_unit_validated"), {
                "job_id": job_id, "unit_id": unit["unit_id"],
                "checkpoint": Jsonb(json.loads(rendered)),
                "checkpoint_fingerprint": json_digest(checkpoint),
            }).fetchone()
            if updated is None:
                raise ArtifactUnitConflict()
            conn.execute(queries.get("update_artifact_run_plan_validated"), {
                "job_id": job_id, "workspace_key": workspace_key,
            })
            return self._unit_with_links(conn, updated, workspace_key)

    def mark_unknown(
        self, job_id: UUID, workspace_key: str, lease_token: UUID,
        unit_key: str, failure_code: str = "unknown_outcome",
    ) -> ArtifactRunUnit:
        return self._mark_terminal(
            job_id, workspace_key, lease_token, unit_key, "unknown", failure_code
        )

    def mark_failed(
        self, job_id: UUID, workspace_key: str, lease_token: UUID,
        unit_key: str, failure_code: str,
    ) -> ArtifactRunUnit:
        return self._mark_terminal(job_id, workspace_key, lease_token, unit_key, "failed", failure_code)

    def _mark_terminal(self, job_id, workspace_key, lease_token, unit_key, status, failure_code):
        if not _FAILURE_CODE.fullmatch(failure_code):
            raise ValueError("failure code is invalid")
        with self._transaction() as conn:
            self._lock_active_job(conn, job_id, workspace_key, lease_token)
            plan = self._lock_plan(conn, job_id, workspace_key)
            if plan["status"] not in {"ready", "running"}:
                raise ArtifactUnitConflict("The artifact plan is terminal.")
            unit = self._lock_unit(conn, job_id, workspace_key, unit_key)
            if unit["status"] not in {"pending", "calling", "needs_repair"}:
                raise ArtifactUnitConflict("The artifact unit is already terminal.")
            if status == "unknown" and unit["status"] != "calling":
                raise ArtifactUnitConflict("Unknown outcome requires a persisted provider call intent.")
            if unit["status"] == "calling":
                linked = conn.execute(queries.get("artifact_run_call_link_exists"), {
                    "job_id": job_id, "unit_id": unit["unit_id"],
                }).fetchone()
                if linked is None:
                    raise ArtifactUnitConflict("The provider call intent is missing.")
            updated = conn.execute(queries.get("update_artifact_run_unit_terminal"), {
                "job_id": job_id, "unit_id": unit["unit_id"],
                "status": status, "failure_code": failure_code,
            }).fetchone()
            if updated is None:
                raise ArtifactUnitConflict()
            conn.execute(queries.get("update_artifact_run_plan_terminal"), {
                "job_id": job_id, "workspace_key": workspace_key, "status": status,
            })
            return self._unit_with_links(conn, updated, workspace_key)

    def _lock_active_job(self, conn, job_id, workspace_key, lease_token):
        row = conn.execute(queries.get("lock_active_artifact_run_job"), {
            "job_id": job_id, "workspace_key": workspace_key, "lease_token": lease_token,
        }).fetchone()
        if row is None:
            raise LeaseLost(str(job_id))
        return row

    @staticmethod
    def _lock_plan(conn, job_id, workspace_key):
        row = conn.execute(queries.get("lock_artifact_run_plan"), {
            "job_id": job_id, "workspace_key": workspace_key,
        }).fetchone()
        if row is None:
            raise ArtifactPlanNotFound()
        return row

    @staticmethod
    def _lock_unit(conn, job_id, workspace_key, unit_key):
        row = conn.execute(queries.get("lock_artifact_run_unit"), {
            "job_id": job_id, "workspace_key": workspace_key, "unit_key": unit_key,
        }).fetchone()
        if row is None:
            raise ArtifactPlanNotFound()
        return row

    @staticmethod
    def _assert_unit_ready(conn, job_id, unit):
        dependencies = conn.execute(queries.get("artifact_run_unit_dependencies_ready"), {
            "job_id": job_id, "unit_id": unit["unit_id"],
        }).fetchall()
        if any(row["status"] != "validated" for row in dependencies):
            raise ArtifactUnitNotReady()

    @staticmethod
    def _assert_profile(conn, profile_id, profile_revision):
        if profile_id is None:
            return
        row = conn.execute(queries.get("get_connection_profile_revision_for_share"), {
            "profile_id": profile_id,
        }).fetchone()
        if row is None or not row["enabled"] or row["revision"] != profile_revision:
            raise ArtifactBindingStale()

    def _assert_source_bindings(self, conn, job_id, workspace_key, spec):
        expected = {source.source_id: source for source in spec.sources}
        if not expected:
            return
        actual_rows = conn.execute(queries.get("get_sources_for_artifact_run_plan"), {
            "source_ids": list(expected), "collection_id": spec.collection_id,
            "workspace_key": workspace_key,
        }).fetchall()
        actual = {row["source_id"]: row for row in actual_rows}
        for source_id, binding in expected.items():
            row = actual.get(source_id)
            if (
                row is None or row["file_hash"] != binding.file_hash
                or row["index_revision"] != binding.index_revision
                or row["collection_id"] != binding.collection_id
            ):
                raise ArtifactBindingStale()

    def _assert_excerpt_bindings(self, conn, spec):
        if not spec.excerpts:
            return
        expected = {excerpt.chunk_id: excerpt for excerpt in spec.excerpts}
        rows = conn.execute(queries.get("get_exact_excerpts_for_artifact_plan"), {
            "chunk_ids": list(expected), "collection_id": spec.collection_id,
            "workspace_key": spec.workspace_key,
        }).fetchall()
        actual = {row["chunk_id"]: row for row in rows}
        source_map = {source.source_id: source for source in spec.sources}
        for chunk_id, excerpt in expected.items():
            row = actual.get(chunk_id)
            source = source_map[excerpt.source_id]
            if (row is None or row["source_id"] != excerpt.source_id
                    or row["locator_id"] != excerpt.locator_id
                    or row["text"] != excerpt.text_snapshot
                    or row["locator_label"] != excerpt.locator_label
                    or row["source_name"] != excerpt.source_name
                    or row["file_hash"] != source.file_hash
                    or row["index_revision"] != source.index_revision):
                raise ArtifactBindingStale()

    def _assert_unit_bindings_current(
        self, conn, job_id, workspace_key, plan, unit, *, check_target=True,
    ):
        self._assert_profile(conn, unit["profile_id"], unit["profile_revision"])
        if check_target:
            self._assert_target_current(
                conn, workspace_key, plan["target_artifact_id"], plan["target_revision_id"]
            )
        linked_sources = conn.execute(queries.get("list_artifact_run_unit_sources"), {
            "job_id": job_id, "workspace_key": workspace_key,
        }).fetchall()
        expected_source_ids = {
            row["source_id"] for row in linked_sources if row["unit_id"] == unit["unit_id"]
        }
        source_locks = conn.execute(queries.get("artifact_run_plan_sources_lock_for_unit"), {
            "job_id": job_id, "unit_id": unit["unit_id"],
        }).fetchall()
        if len(source_locks) != len(expected_source_ids):
            raise ArtifactBindingStale()
        frozen_excerpts = plan["frozen_inputs"].get("excerpts", [])
        expected_excerpt_count = sum(
            1 for item in frozen_excerpts if UUID(item["source_id"]) in expected_source_ids
        )
        excerpt_locks = conn.execute(queries.get("artifact_run_plan_excerpts_lock_for_unit"), {
            "job_id": job_id, "unit_id": unit["unit_id"],
        }).fetchall()
        if len(excerpt_locks) != expected_excerpt_count:
            raise ArtifactBindingStale()
        rows = conn.execute(queries.get("artifact_run_plan_sources_current_for_unit"), {
            "job_id": job_id, "workspace_key": workspace_key, "unit_id": unit["unit_id"],
        }).fetchall()
        if len(rows) != len(expected_source_ids):
            raise ArtifactBindingStale()
        for row in rows:
            if (row["current_collection_id"] is None or
                row["current_collection_id"] != row["collection_id"]
                or row["current_file_hash"] != row["file_hash"]
                or row["current_index_revision"] != row["index_revision"]
                or row["current_status"] != "indexed"
                or not row["current_is_current"]
                or row["current_workspace_key"] not in (None, workspace_key)
            ):
                raise ArtifactBindingStale()
        excerpts = conn.execute(queries.get("artifact_run_plan_excerpts_current_for_unit"), {
            "job_id": job_id, "unit_id": unit["unit_id"],
        }).fetchall()
        if sum(1 for excerpt in excerpts if excerpt["excerpt_id"] is not None) != expected_excerpt_count:
            raise ArtifactBindingStale()
        for excerpt in excerpts:
            if (excerpt["excerpt_id"] is None or excerpt["current_source_id"] is None
                    or not excerpt["current_is_current"]
                    or excerpt["current_locator_id"] != excerpt["locator_id"]
                    or excerpt["current_text"] != excerpt["text_snapshot"]
                    or excerpt["current_locator_label"] != excerpt["locator_label"]
                    or excerpt["current_source_name"] != excerpt["source_name"]):
                raise ArtifactBindingStale()

    @staticmethod
    def _assert_target_current(conn, workspace_key, artifact_id, revision_id):
        if artifact_id is None:
            return
        row = conn.execute(queries.get("artifact_run_target_head_current"), {
            "workspace_key": workspace_key,
            "target_artifact_id": artifact_id,
            "target_revision_id": revision_id,
        }).fetchone()
        if row is None:
            raise ArtifactBindingStale()

    def _unit_with_links(self, conn, row, workspace_key):
        source_rows = conn.execute(queries.get("list_artifact_run_unit_sources"), {
            "job_id": row["job_id"], "workspace_key": workspace_key,
        }).fetchall()
        dependency_rows = conn.execute(queries.get("list_artifact_run_unit_dependencies"), {
            "job_id": row["job_id"], "workspace_key": workspace_key,
        }).fetchall()
        sources = tuple(item["source_id"] for item in source_rows if item["unit_id"] == row["unit_id"])
        dependencies = tuple(
            item["depends_on_unit_id"] for item in dependency_rows if item["unit_id"] == row["unit_id"]
        )
        return ArtifactRunUnit(
            unit_id=row["unit_id"], job_id=row["job_id"], ordinal=row["ordinal"],
            unit_key=row["unit_key"], unit_kind=row["unit_kind"], status=row["status"],
            input_fingerprint=row["input_fingerprint"].strip(),
            input_snapshot=dict(row["input_snapshot"]), source_ids=sources,
            depends_on=dependencies, profile_id=row["profile_id"],
            profile_revision=row["profile_revision"],
            checkpoint=dict(row["checkpoint"]) if row["checkpoint"] is not None else None,
            checkpoint_fingerprint=(row["checkpoint_fingerprint"].strip()
                                    if row["checkpoint_fingerprint"] else None),
            failure_code=row["failure_code"],
        )
