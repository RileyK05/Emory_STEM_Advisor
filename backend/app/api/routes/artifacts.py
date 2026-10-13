"""Native artifacts: validated import, immutable revisions, previews, and exports."""

from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api import deps
from app.api.schemas import (
    ArtifactPreview,
    ArtifactRevisionSummary,
    NativeArtifactRecord,
    NativeArtifactSummary,
)
from app.artifacts.errors import ArtifactError
from app.artifacts.models import DocumentSpec, WorkbookSpec
from app.artifacts.revisions import manualize_spec, revise_document, revise_workbook
from app.artifacts.service import (
    MIME_TYPES,
    export_artifact,
    import_artifact,
    preview_artifact,
    render_artifact_pdf,
)
from app.artifacts.storage import ArtifactStore, public_record, validate_spec
from app.auth.dependencies import get_workspace_key
from app.config import Settings

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])
Conn = Annotated[object, Depends(deps.get_conn)]
SettingsDep = Annotated[Settings, Depends(deps.get_settings)]
RevisionParam = Annotated[UUID | None, Query(alias="revisionId")]
WorkspaceDep = Annotated[str, Depends(get_workspace_key)]


class CreateArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["document", "workbook", "html"]
    spec: dict


class SaveRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedParentRevisionId: UUID
    operations: list[dict] | None = Field(default=None, min_length=1, max_length=1000)
    spec: dict | None = None
    draftId: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def exactly_one_change(self):
        if (self.spec is None) == (self.operations is None):
            raise ValueError("provide exactly one of spec or operations")
        return self


def _store(conn, settings: Settings, workspace_key: str) -> ArtifactStore:
    return ArtifactStore(conn, workspace_key=workspace_key, data_dir=settings.data_dir)


@router.get("", response_model=list[NativeArtifactSummary], response_model_exclude_unset=True)
def list_artifacts(conn: Conn, settings: SettingsDep, workspace: WorkspaceDep) -> list[dict]:
    return [public_record(row) for row in _store(conn, settings, workspace).list()]


@router.post("", response_model=NativeArtifactRecord, response_model_exclude_unset=True)
def create_artifact(body: CreateArtifact, conn: Conn, settings: SettingsDep,
                    workspace: WorkspaceDep) -> dict:
    spec = manualize_spec(validate_spec(body.kind, body.spec))
    return public_record(_store(conn, settings, workspace).create(spec))


@router.post("/import", response_model=NativeArtifactRecord, response_model_exclude_unset=True)
def import_file(conn: Conn, settings: SettingsDep, workspace: WorkspaceDep,
                file: Annotated[UploadFile, File()]) -> dict:
    data = file.file.read(min(settings.upload_max_bytes, 20 * 1024 * 1024) + 1)
    if len(data) > min(settings.upload_max_bytes, 20 * 1024 * 1024):
        raise ArtifactError("artifact_limit", "Imported file exceeds the configured size limit.")
    filename = file.filename or "upload"
    if len(filename) > 200 or any(ord(char) < 32 for char in filename):
        raise ArtifactError("invalid_filename", "Invalid import filename.")
    try:
        imported = import_artifact(data, filename)
    except ArtifactError:
        raise
    except ValueError as exc:
        raise ArtifactError("invalid_file", "File could not be imported safely.") from exc
    spec = manualize_spec(imported if isinstance(imported, DocumentSpec) else imported.spec)
    return public_record(_store(conn, settings, workspace).create(spec, original=data, filename=filename))


@router.get("/{artifact_id}", response_model=NativeArtifactRecord,
            response_model_exclude_unset=True)
def get_artifact(artifact_id: UUID, conn: Conn, settings: SettingsDep, workspace: WorkspaceDep,
                 revision_id: RevisionParam = None) -> dict:
    return public_record(_store(conn, settings, workspace).get(artifact_id, revision_id))


@router.get("/{artifact_id}/revisions", response_model=list[ArtifactRevisionSummary])
def revision_history(artifact_id: UUID, conn: Conn, settings: SettingsDep,
                     workspace: WorkspaceDep) -> list[dict]:
    return [
        {"revisionId": str(row["revision_id"]),
         "parentRevisionId": str(row["parent_revision_id"]) if row["parent_revision_id"] else None,
         "title": row["title"], "createdAt": row["created_at"].isoformat()}
        for row in _store(conn, settings, workspace).history(artifact_id)
    ]


@router.post("/{artifact_id}/revisions", response_model=NativeArtifactRecord,
             response_model_exclude_unset=True)
def save_revision(artifact_id: UUID, body: SaveRevision, conn: Conn,
                  settings: SettingsDep, workspace: WorkspaceDep) -> dict:
    store = _store(conn, settings, workspace)
    row = store.get(artifact_id, body.expectedParentRevisionId)
    prior = validate_spec(row["kind"], row["spec"])
    conn.commit()
    if body.spec is not None:
        spec = validate_spec(row["kind"], body.spec)
    else:
        if isinstance(prior, DocumentSpec):
            spec = revise_document(prior, body.operations)
        elif isinstance(prior, WorkbookSpec):
            spec = revise_workbook(prior, body.operations)
        else:
            raise ArtifactError("invalid_operation", "HTML revisions require a complete HTML spec.")
    spec = manualize_spec(spec, prior)
    return public_record(store.revise(
        artifact_id, body.expectedParentRevisionId, spec, draft_id=body.draftId,
    ))


@router.get("/{artifact_id}/preview", response_model=ArtifactPreview,
            response_model_exclude_unset=True)
def preview(artifact_id: UUID, conn: Conn, settings: SettingsDep, workspace: WorkspaceDep,
            revision_id: RevisionParam = None) -> dict:
    row = _store(conn, settings, workspace).get(artifact_id, revision_id)
    conn.commit()
    return preview_artifact(validate_spec(row["kind"], row["spec"]))


@router.get("/{artifact_id}/download", response_class=Response, responses={
    200: {"description": "The artifact exported in the requested format.",
          "content": {mime.split(";")[0]: {} for mime in MIME_TYPES.values()}}})
def download(artifact_id: UUID, conn: Conn, settings: SettingsDep,
             workspace: WorkspaceDep, request: Request,
             format: Literal["md", "docx", "xlsx", "html"],
             revision_id: RevisionParam = None) -> Response:
    row = _store(conn, settings, workspace).get(artifact_id, revision_id)
    conn.commit()
    exported = export_artifact(
        validate_spec(row["kind"], row["spec"]), format,
        office_runner=getattr(request.app.state, "office_runner", None),
        office_timeout_seconds=int(settings.office_timeout_seconds),
    )
    headers = {
        "Content-Disposition": f'attachment; filename="{exported.filename}"',
        "X-Artifact-Revision": str(row["revision_id"]),
        "X-Recalculation-Status": exported.recalculation_status,
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
    }
    return Response(exported.content, media_type=exported.mime_type, headers=headers)


@router.get("/{artifact_id}/preview.pdf", response_class=Response, responses={
    200: {"description": "Inline PDF preview (Office fidelity unverified).",
          "content": {"application/pdf": {}}}})
def preview_pdf(artifact_id: UUID, conn: Conn, settings: SettingsDep,
                workspace: WorkspaceDep, request: Request,
                revision_id: RevisionParam = None) -> Response:
    row = _store(conn, settings, workspace).get(artifact_id, revision_id)
    conn.commit()
    office_runner = getattr(request.app.state, "office_runner", None)
    exported = render_artifact_pdf(
        validate_spec(row["kind"], row["spec"]),
        office_runner=office_runner,
        timeout_seconds=int(settings.office_timeout_seconds),
    )
    return Response(exported.content, media_type="application/pdf", headers={
        "Content-Disposition": f'inline; filename="{exported.filename}"',
        "X-Artifact-Revision": str(row["revision_id"]),
        "X-Recalculation-Status": exported.recalculation_status,
        "X-Office-Fidelity": "unverified",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
    })


@router.get("/{artifact_id}/original", response_class=Response, responses={
    200: {"description": "The original uploaded file, byte for byte.",
          "content": {"application/octet-stream": {}}}})
def original(artifact_id: UUID, conn: Conn, settings: SettingsDep,
             workspace: WorkspaceDep) -> Response:
    filename, content = _store(conn, settings, workspace).original(artifact_id)
    return Response(content, media_type="application/octet-stream", headers={
        "Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename, safe=""),
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
    })
