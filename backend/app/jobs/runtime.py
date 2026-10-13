"""Injected run handlers and atomic artifact publication at job completion."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from psycopg import Connection

from app.artifacts.storage import ArtifactStore, validate_spec
from app.jobs.store import SafeJobError
from app.jobs.types import Job
from app.jobs.worker import JobContext
from app.util.workspace import is_valid_workspace_key

_VALID_KINDS = frozenset({"document", "workbook", "html"})


@dataclass(frozen=True)
class StagedArtifact:
    """Validated artifact data, held only in worker memory until fenced publication."""

    kind: Literal["document", "workbook", "html"]
    spec: Mapping[str, Any]
    operation: Literal["create", "revise"] = "create"
    artifact_id: UUID | None = None
    expected_revision_id: UUID | None = None
    draft_id: str | None = None
    profile_id: UUID | None = None
    profile_revision: int | None = None
    operation_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.operation_id is not None and not isinstance(self.operation_id, UUID):
            raise ValueError("operation_id must be a UUID")
        if (self.profile_id is None) != (self.profile_revision is None):
            raise ValueError("profile_id and profile_revision must be supplied together")
        if self.profile_revision is not None and self.profile_revision < 1:
            raise ValueError("profile_revision must be positive")
        if self.kind not in _VALID_KINDS:
            raise ValueError("unsupported artifact kind")
        typed = validate_spec(self.kind, dict(self.spec))
        object.__setattr__(self, "spec", typed.model_dump(mode="json"))
        if self.operation == "create":
            if (
                self.artifact_id is not None
                or self.expected_revision_id is not None
                or self.draft_id
            ):
                raise ValueError("create artifacts cannot name an existing revision")
        elif self.operation == "revise":
            if self.artifact_id is None or self.expected_revision_id is None:
                raise ValueError("revision requires artifact and expected revision IDs")
            if self.draft_id is not None and (not self.draft_id or len(self.draft_id) > 128):
                raise ValueError("draft_id must contain 1 to 128 characters")
        else:
            raise ValueError("unsupported artifact operation")


@dataclass(frozen=True)
class StagedRunResult:
    artifacts: tuple[StagedArtifact, ...]

    def __post_init__(self) -> None:
        if not self.artifacts or len(self.artifacts) > 100:
            raise ValueError("run must contain 1 to 100 staged artifacts")


RunHandler = Callable[[Job, JobContext], StagedRunResult]


class RunExecutor:
    """Dispatch by native artifact kind and publish only under the valid job lease."""

    def __init__(
        self,
        handlers: Mapping[str, RunHandler],
        *,
        data_dir: Path = Path("data"),
        workspace_key_resolver: Callable[[Job], str] = lambda job: job.scope_key,
    ):
        if not isinstance(handlers, Mapping) or not handlers:
            raise ValueError("run handlers must be a non-empty mapping by artifact kind")
        if any(
            kind not in _VALID_KINDS or not callable(handler) for kind, handler in handlers.items()
        ):
            raise ValueError("run handlers contain an unsupported kind or non-callable handler")
        self._handlers = dict(handlers)
        self._data_dir = data_dir
        self._workspace_key_resolver = workspace_key_resolver

    def __call__(self, job: Job, context: JobContext) -> StagedRunResult:
        if job.kind != "artifact.run":
            raise SafeJobError("unsupported_run", "This run type is not supported.")
        kind = job.payload.get("kind")
        handler = self._handlers.get(kind)
        if handler is None:
            raise SafeJobError("unsupported_run", "This run type is not supported.")
        if not isinstance(job.payload.get("inputs"), dict):
            raise SafeJobError("invalid_run", "Run input could not be processed.")
        context.check_cancelled()
        result = handler(job, context)
        context.check_cancelled()
        if not isinstance(result, StagedRunResult):
            raise SafeJobError(
                "invalid_run_result", "Run did not produce a validated artifact draft."
            )
        if any(item.kind != kind for item in result.artifacts):
            raise SafeJobError("invalid_run_result", "Run produced an artifact of the wrong type.")
        return result

    def publish_success(self, conn: Connection, job: Job, value: Any) -> dict[str, Any]:
        """Publish drafts and return public IDs in the same transaction as job success."""
        if not isinstance(value, StagedRunResult):
            raise SafeJobError(
                "invalid_run_result", "Run did not produce a validated artifact draft."
            )
        workspace_key = self._workspace_key_resolver(job)
        store = ArtifactStore(conn, workspace_key=workspace_key, data_dir=self._data_dir)
        from app.db import queries
        from app.jobs.artifact_plans import ArtifactPlanError, ArtifactRunPlanStore

        plan_store = ArtifactRunPlanStore(lambda: conn)
        planned = conn.execute(queries.get("get_artifact_run_plan"), {
            "job_id": job.job_id, "workspace_key": workspace_key,
        }).fetchone() is not None
        if planned and len(value.artifacts) != 1:
            raise SafeJobError(
                "invalid_run_result", "A dependent output plan can publish exactly one artifact."
            )
        artifact_ids = []
        artifact_revisions = []
        for staged in value.artifacts:
            if staged.operation_id is not None:
                if staged.operation_id != job.job_id:
                    raise SafeJobError(
                        "operation_mismatch", "Generation operation identity changed; output was withheld."
                    )
                from app.db import queries

                _check_operation_budget(conn, staged.operation_id, workspace_key)
            if staged.profile_id is not None:
                from app.db import queries

                current = conn.execute(
                    queries.get("get_connection_profile_revision_for_share"),
                    {"profile_id": staged.profile_id},
                ).fetchone()
                if (
                    current is None
                    or not current["enabled"]
                    or current["revision"] != staged.profile_revision
                ):
                    raise SafeJobError(
                        "stale_connection",
                        "The selected model connection changed during generation.",
                    )
            if planned:
                if staged.operation_id != job.job_id:
                    raise SafeJobError(
                        "operation_mismatch", "The planned artifact operation identity changed."
                    )
                try:
                    plan_store.assert_publishable(
                        conn, job.job_id, workspace_key, job.lease_token,
                        staged.kind, staged.spec,
                    )
                except ArtifactPlanError as exc:
                    raise SafeJobError("stale_plan", "Frozen artifact evidence changed before publication.") from exc
            spec = validate_spec(staged.kind, dict(staged.spec))
            if staged.operation == "create":
                record = store.create_uncommitted(spec)
            else:
                assert staged.artifact_id is not None and staged.expected_revision_id is not None
                record = store.revise_uncommitted(
                    staged.artifact_id,
                    staged.expected_revision_id,
                    spec,
                    draft_id=staged.draft_id,
                )
            if planned:
                try:
                    plan_store.assert_publishable(
                        conn, job.job_id, workspace_key, job.lease_token,
                        staged.kind, staged.spec, check_target=False,
                    )
                except ArtifactPlanError as exc:
                    raise SafeJobError("stale_plan", "Frozen artifact evidence changed during publication.") from exc
            artifact_ids.append(str(record["artifact_id"]))
            artifact_revisions.append({
                "artifactId": str(record["artifact_id"]),
                "revisionId": str(record["revision_id"]),
            })
        for staged in value.artifacts:
            if staged.operation_id is not None:
                from app.db import queries

                _check_operation_budget(conn, staged.operation_id, workspace_key)
        return {"artifactIds": artifact_ids, "artifactRevisions": artifact_revisions}


def _check_operation_budget(conn: Connection, operation_id: UUID, workspace_key: str) -> None:
    from app.db import queries
    from app.operations.budget import fence_violation

    budget = conn.execute(
        queries.get("get_operation_budget_for_update"), {"operation_id": operation_id}
    ).fetchone()
    if fence_violation(budget, workspace_key) is not None:
        raise SafeJobError(
            "operation_budget",
            "Generation exceeded its time or token budget; output was withheld.",
        )


class ArtifactPersistence:
    """Typed standalone create/revise access through the immutable ArtifactStore."""

    def __init__(self, connect: Callable[[], Connection], *, data_dir: Path, workspace_key: str):
        if not is_valid_workspace_key(workspace_key):
            raise ValueError("workspace_key must contain 1 to 200 characters")
        self._connect = connect
        self.data_dir = data_dir
        self.workspace_key = workspace_key

    def create(self, spec_data: Mapping[str, Any]) -> dict[str, Any]:
        return self._persist(spec_data, "create")

    def revise(
        self,
        artifact_id: UUID,
        expected_revision_id: UUID,
        spec_data: Mapping[str, Any],
        *,
        draft_id: str | None = None,
    ) -> dict[str, Any]:
        return self._persist(spec_data, "revise", artifact_id=artifact_id,
                             expected_revision_id=expected_revision_id, draft_id=draft_id)

    def _persist(self, spec_data: Mapping[str, Any], operation: str, *, artifact_id=None,
                 expected_revision_id=None, draft_id=None) -> dict[str, Any]:
        if not isinstance(spec_data, Mapping):
            raise TypeError("artifact spec must be an object")
        spec = validate_spec(str(spec_data.get("kind", "")), dict(spec_data))
        conn = self._connect()
        try:
            store = ArtifactStore(
                conn, workspace_key=self.workspace_key, data_dir=self.data_dir
            )
            if operation == "revise":
                record = store.revise(artifact_id, expected_revision_id, spec, draft_id=draft_id)
            else:
                record = store.create(spec)
            from app.artifacts.storage import public_record

            return public_record(record)
        finally:
            conn.close()
