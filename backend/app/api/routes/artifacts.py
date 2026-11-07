"""Native artifacts: immutable revisions, previews, and exports."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api import deps
from app.api.schemas import (
    ArtifactPreview,
    ArtifactRevisionSummary,
    NativeArtifactRecord,
    NativeArtifactSummary,
)
from app.artifacts.service import (
    MIME_TYPES,
    export_artifact,
    preview_artifact,
)
from app.artifacts.storage import ArtifactStore, public_record, validate_spec
from app.auth.dependencies import get_workspace_key
from app.config import Settings

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])
Conn = Annotated[object, Depends(deps.get_conn)]
SettingsDep = Annotated[Settings, Depends(deps.get_settings)]
RevisionParam = Annotated[UUID | None, Query(alias="revisionId")]
WorkspaceDep = Annotated[str, Depends(get_workspace_key)]


def _store(conn, settings: Settings, workspace_key: str) -> ArtifactStore:
    return ArtifactStore(conn, workspace_key=workspace_key, data_dir=settings.data_dir)


@router.get("", response_model=list[NativeArtifactSummary], response_model_exclude_unset=True)
def list_artifacts(conn: Conn, settings: SettingsDep, workspace: WorkspaceDep) -> list[dict]:
    return [public_record(row) for row in _store(conn, settings, workspace).list()]


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

