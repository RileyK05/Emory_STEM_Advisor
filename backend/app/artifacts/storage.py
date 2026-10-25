"""Immutable validated revisions, scoped lookup, and compare-and-swap publication."""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from app.artifacts.errors import ArtifactLimitError
from app.artifacts.models import DocumentSpec, HtmlArtifactSpec, WorkbookSpec
from app.db import queries
from app.privacy.fence import require_live_workspace
from app.util.localpath import io_path

Spec = DocumentSpec | WorkbookSpec | HtmlArtifactSpec
SPEC_TYPES = {"document": DocumentSpec, "workbook": WorkbookSpec, "html": HtmlArtifactSpec}
MAX_ARTIFACTS_PER_WORKSPACE = 500
MAX_REVISIONS_PER_ARTIFACT = 500


class ArtifactNotFound(ValueError):
    pass


class RevisionConflict(ValueError):
    pass


def validate_spec(kind: str, payload: dict) -> Spec:
    spec = SPEC_TYPES[kind].model_validate(payload)
    if len(spec.model_dump_json().encode("utf-8")) > 16_000_000:
        raise ArtifactLimitError("artifact spec exceeds 16 MB")
    return spec


class ArtifactStore:
    def __init__(self, conn, *, workspace_key: str, data_dir: Path,
                 max_artifacts: int = MAX_ARTIFACTS_PER_WORKSPACE,
                 max_revisions: int = MAX_REVISIONS_PER_ARTIFACT):
        self.conn = conn
        self.workspace_key = workspace_key
        self.data_dir = data_dir
        self.max_artifacts = max_artifacts
        self.max_revisions = max_revisions

    def _reject_deleted_workspace(self) -> None:
        require_live_workspace(self.conn, self.workspace_key, lock=True)

    def _check_artifact_cap(self) -> None:
        row = self.conn.execute(
            queries.get("count_artifacts_for_workspace"),
            {"workspace_key": self.workspace_key},
        ).fetchone()
        if row is not None and row["count"] >= self.max_artifacts:
            raise ArtifactLimitError("workspace artifact limit reached")

    def _check_revision_cap(self, artifact_id: UUID) -> None:
        row = self.conn.execute(
            queries.get("count_artifact_revisions_for_artifact"),
            {"artifact_id": artifact_id, "workspace_key": self.workspace_key},
        ).fetchone()
        if row is not None and row["count"] >= self.max_revisions:
            raise ArtifactLimitError("artifact revision limit reached")

    def get(self, artifact_id: UUID, revision_id: UUID | None = None) -> dict:
        row = self.conn.execute(
            queries.get("get_artifact_revision"),
            {
                "artifact_id": artifact_id,
                "revision_id": revision_id,
                "workspace_key": self.workspace_key,
            },
        ).fetchone()
        if row is None:
            raise ArtifactNotFound("artifact or revision not found")
        return row

    def list(self) -> list[dict]:
        return self.conn.execute(
            queries.get("list_artifacts"), {"workspace_key": self.workspace_key}
        ).fetchall()

    def history(self, artifact_id: UUID) -> list[dict]:
        self.get(artifact_id)
        return self.conn.execute(
            queries.get("list_artifact_revisions"),
            {
                "artifact_id": artifact_id,
                "workspace_key": self.workspace_key,
            },
        ).fetchall()

    def create_uncommitted(self, spec: Spec) -> dict:
        """Insert an artifact using the caller's transaction; do not commit."""
        self._reject_deleted_workspace()
        self._check_artifact_cap()
        artifact_id, revision_id = uuid4(), uuid4()
        self.conn.execute(
            queries.get("insert_artifact"),
            {
                "artifact_id": artifact_id,
                "workspace_key": self.workspace_key,
                "kind": spec.kind,
                "revision_id": revision_id,
                "original_filename": None,
                "original_sha256": None,
                "original_path": None,
            },
        )
        self.conn.execute(
            queries.get("insert_artifact_revision"),
            {
                "artifact_id": artifact_id,
                "revision_id": revision_id,
                "parent_revision_id": None,
                "draft_id": None,
                "spec": Jsonb(spec.model_dump(mode="json")),
            },
        )
        return self.get(artifact_id, revision_id)

    def revise_uncommitted(
        self,
        artifact_id: UUID,
        expected_revision_id: UUID,
        spec: Spec,
        *,
        draft_id: str | None = None,
    ) -> dict:
        """CAS a revision using the caller's transaction; do not commit."""
        self._reject_deleted_workspace()
        if draft_id:
            existing = self.conn.execute(
                queries.get("get_artifact_draft_revision"),
                {
                    "artifact_id": artifact_id,
                    "workspace_key": self.workspace_key,
                    "draft_id": draft_id,
                },
            ).fetchone()
            if existing:
                if existing["parent_revision_id"] != expected_revision_id or existing[
                    "spec"
                ] != spec.model_dump(mode="json"):
                    raise RevisionConflict("draft ID already has different revision content")
                return existing
        self._check_revision_cap(artifact_id)
        current = self.get(artifact_id)
        if current["kind"] != spec.kind:
            raise ValueError("a revision cannot change artifact kind")
        revision_id = uuid4()
        updated = self.conn.execute(
            queries.get("advance_artifact_head"),
            {
                "artifact_id": artifact_id,
                "workspace_key": self.workspace_key,
                "expected_revision_id": expected_revision_id,
                "revision_id": revision_id,
            },
        ).fetchone()
        if updated is None:
            raise RevisionConflict("artifact changed; reload before saving")
        self.conn.execute(
            queries.get("insert_artifact_revision"),
            {
                "artifact_id": artifact_id,
                "revision_id": revision_id,
                "parent_revision_id": expected_revision_id,
                "draft_id": draft_id,
                "spec": Jsonb(spec.model_dump(mode="json")),
            },
        )
        return self.get(artifact_id, revision_id)

    def create(
        self, spec: Spec, *, original: bytes | None = None, filename: str | None = None
    ) -> dict:
        self._reject_deleted_workspace()
        artifact_id, revision_id = uuid4(), uuid4()
        digest, path = None, None
        if original is not None:
            if len(original) > 20 * 1024 * 1024:
                raise ValueError("original file exceeds 20 MB")
            digest = hashlib.sha256(original).hexdigest()
            workspace_hash = hashlib.sha256(self.workspace_key.encode()).hexdigest()
            directory = self.data_dir / "artifacts" / "originals" / workspace_hash
            io_path(directory).mkdir(parents=True, exist_ok=True)
            path = directory / digest
            stored = io_path(path)
            if stored.exists():
                if hashlib.sha256(stored.read_bytes()).hexdigest() != digest:
                    raise ValueError("stored original failed integrity check")
            else:
                temporary = directory / (str(uuid4()) + ".tmp")
                staged = io_path(temporary)
                with staged.open("xb") as handle:
                    handle.write(original)
                try:
                    if (
                        temporary.resolve().parent != directory.resolve()
                        or path.resolve().parent != directory.resolve()
                    ):
                        raise ValueError("original storage path escaped configured directory")
                    staged.replace(stored)  # Publish complete bytes atomically.
                finally:
                    staged.unlink(missing_ok=True)
        with self.conn.transaction():
            self._reject_deleted_workspace()
            self._check_artifact_cap()
            self.conn.execute(
                queries.get("insert_artifact"),
                {
                    "artifact_id": artifact_id,
                    "workspace_key": self.workspace_key,
                    "kind": spec.kind,
                    "revision_id": revision_id,
                    "original_filename": filename,
                    "original_sha256": digest,
                    "original_path": str(path) if path else None,
                },
            )
            self.conn.execute(
                queries.get("insert_artifact_revision"),
                {
                    "artifact_id": artifact_id,
                    "revision_id": revision_id,
                    "parent_revision_id": None,
                    "draft_id": None,
                    "spec": Jsonb(spec.model_dump(mode="json")),
                },
            )
        self.conn.commit()
        return self.get(artifact_id)

    def revise(
        self,
        artifact_id: UUID,
        expected_revision_id: UUID,
        spec: Spec,
        *,
        draft_id: str | None = None,
    ) -> dict:
        # Same draft replay is idempotent; a changed replay must not replace its result.
        existing = None
        if draft_id:
            existing = self.conn.execute(
                queries.get("get_artifact_draft_revision"),
                {
                    "artifact_id": artifact_id,
                    "workspace_key": self.workspace_key,
                    "draft_id": draft_id,
                },
            ).fetchone()
        if existing:
            if existing["parent_revision_id"] != expected_revision_id or existing[
                "spec"
            ] != spec.model_dump(mode="json"):
                raise RevisionConflict("draft ID already has different revision content")
            return existing
        current = self.get(artifact_id)
        if current["kind"] != spec.kind:
            raise ValueError("a revision cannot change artifact kind")
        revision_id = uuid4()
        # Finish read transactions before the short CAS transaction.
        self.conn.commit()
        with self.conn.transaction():
            self._check_revision_cap(artifact_id)
            updated = self.conn.execute(
                queries.get("advance_artifact_head"),
                {
                    "artifact_id": artifact_id,
                    "workspace_key": self.workspace_key,
                    "expected_revision_id": expected_revision_id,
                    "revision_id": revision_id,
                },
            ).fetchone()
            if updated is None:
                raise RevisionConflict("artifact changed; reload before saving")
            self.conn.execute(
                queries.get("insert_artifact_revision"),
                {
                    "artifact_id": artifact_id,
                    "revision_id": revision_id,
                    "parent_revision_id": expected_revision_id,
                    "draft_id": draft_id,
                    "spec": Jsonb(spec.model_dump(mode="json")),
                },
            )
        self.conn.commit()
        return self.get(artifact_id, revision_id)

    def original(self, artifact_id: UUID) -> tuple[str, bytes]:
        row = self.get(artifact_id)
        if not row["original_path"]:
            raise ArtifactNotFound("artifact has no imported original")
        expected_dir = (
            self.data_dir
            / "artifacts"
            / "originals"
            / hashlib.sha256(self.workspace_key.encode()).hexdigest()
        ).resolve()
        path = Path(row["original_path"]).resolve()
        if path.parent != expected_dir or path.name != row["original_sha256"]:
            raise ValueError("original storage path failed validation")
        content = io_path(path).read_bytes()
        if hashlib.sha256(content).hexdigest() != row["original_sha256"]:
            raise ValueError("original file failed integrity check")
        return row["original_filename"], content


def public_record(row: dict) -> dict:
    spec = validate_spec(row["kind"], row["spec"])
    formulas = isinstance(spec, WorkbookSpec) and any(
        cell.formula for sheet in spec.sheets for cell in sheet.cells
    )
    original = None
    if row["original_sha256"]:
        original = {
            "filename": row["original_filename"],
            "sha256": row["original_sha256"],
            "preserved": True,
        }
    return {
        "artifactId": str(row["artifact_id"]),
        "revisionId": str(row["revision_id"]),
        "parentRevisionId": str(row["parent_revision_id"]) if row["parent_revision_id"] else None,
        "kind": row["kind"],
        "title": spec.title,
        "spec": spec.model_dump(mode="json"),
        "original": original,
        "status": "needs_recalculation" if formulas else "ready",
        "message": "Formula values require isolated recalculation." if formulas else None,
    }
