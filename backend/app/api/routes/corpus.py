"""HTTP routes. Thin: resolve collection, call the pipeline, serialize."""

import hashlib
import tempfile
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from psycopg import Connection
from pydantic import BaseModel, ConfigDict, Field

from app.api import deps
from app.api.schemas import (
    AnswerResponse,
    CollectionDoc,
    CourseDoc,
    QueryRequest,
    RecommendationDoc,
    RefusalResponse,
    SourceDoc,
    SubgraphView,
)
from app.auth.dependencies import get_workspace_key
from app.config import Settings
from app.db import queries
from app.debug.dependencies import get_debug
from app.debug.store import AppliedDebug
from app.embeddings.base import Embedder
from app.ingest.pipeline import ingest_file
from app.llm.base import Provider
from app.retrieval.config import RetrievalConfig
from app.simgraph.view import subgraph_view
from app.simgraph.walk import recommend

router = APIRouter()


class ReindexRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedIndexRevision: int = Field(ge=0)

Conn = Annotated[Connection, Depends(deps.get_conn)]
WorkspaceDep = Annotated[str, Depends(get_workspace_key)]
SettingsDep = Annotated[Settings, Depends(deps.get_settings)]
ConfigDep = Annotated[RetrievalConfig, Depends(deps.get_config)]
EmbedderDep = Annotated[Embedder, Depends(deps.get_embedder_dep)]
ProviderDep = Annotated[Provider, Depends(deps.get_provider_dep)]


def _content_type(filename: str) -> str:
    lowered = filename.lower()
    if lowered.endswith((".md", ".markdown")):
        return "text/markdown"
    if lowered.endswith(".pdf"):
        return "application/pdf"
    return "text/plain"


def _source_doc(row, *, can_reindex: bool) -> SourceDoc:
    return SourceDoc(
        id=str(row["source_id"]),
        name=row["filename"],
        locatorType=row["locator_type"],
        status=row["status"],
        chunkCount=row["chunk_count"],
        errorMessage=row["error_message"],
        warnings=row["warnings"],
        indexRevision=row["index_revision"],
        canReindex=can_reindex,
    )




@router.get("/api/collections", response_model=list[CollectionDoc])
def list_collections(conn: Conn) -> list[CollectionDoc]:
    rows = conn.execute(queries.get("list_collections")).fetchall()
    return [CollectionDoc(id=str(row["collection_id"]), name=row["name"]) for row in rows]


@router.get("/api/sources", response_model=list[SourceDoc])
def list_sources(
    conn: Conn,
    workspace: WorkspaceDep,
    settings: SettingsDep,
    collection: str | None = None,
) -> list[SourceDoc]:
    collection_id, _ = deps.resolve_collection(conn, settings, collection)
    rows = conn.execute(
        queries.get("list_sources_for_workspace"), {"collection_id": collection_id, "workspace_key": workspace}
    ).fetchall()
    return [_source_doc(row, can_reindex=row["workspace_key"] == workspace) for row in rows]


@router.post("/api/query", response_model=AnswerResponse | RefusalResponse)
def query(
    body: QueryRequest, request: Request, conn: Conn, settings: SettingsDep, config: ConfigDep,
    embedder: EmbedderDep, provider: ProviderDep, workspace: WorkspaceDep,
    debug: Annotated[AppliedDebug | None, Depends(get_debug)],
) -> AnswerResponse | RefusalResponse:
    from app.api.query_service import query_response
    from app.quotas.service import QuotaService

    QuotaService.check_and_consume(conn, workspace_key=workspace)
    effective_settings = deps.execution_settings(
        request, conn, workspace, debug.settings if debug is not None else settings)
    effective_config = debug.config if debug is not None else config
    return query_response(body, conn, effective_settings, effective_config, embedder, provider,
                          workspace_key=workspace,
                          connection_profiles=request.app.state.connection_profiles,
                          connection_runtime=request.app.state.connection_runtime,
                          system_prompt=debug.system_prompt if debug is not None else None)


@router.post("/api/sources", response_model=SourceDoc)
def upload_source(
    conn: Conn,
    workspace: WorkspaceDep,
    settings: SettingsDep,
    config: ConfigDep,
    embedder: EmbedderDep,
    file: Annotated[UploadFile, File()],
    collection: Annotated[str | None, Form()] = None,
) -> SourceDoc:
    collection_id, _ = deps.resolve_collection(conn, settings, collection)
    raw = file.file.read(settings.upload_max_bytes + 1)
    if len(raw) > settings.upload_max_bytes:
        raise HTTPException(status_code=413, detail="upload exceeds configured size limit")
    filename = file.filename or "upload.txt"
    if (filename in {".", ".."} or len(filename) > 200
            or any(ord(c) < 32 or c in '/\\<>:"|?*' for c in filename)):
        raise HTTPException(status_code=400, detail="invalid upload filename")
    if Path(filename).suffix.lower() not in {".txt", ".md", ".markdown", ".pdf"}:
        raise HTTPException(status_code=415, detail="supported sources: PDF, text, Markdown")
    content_type = _content_type(filename)
    count_tokens = getattr(embedder, "count_tokens", None)
    if count_tokens is None:
        raise HTTPException(status_code=503, detail="embedding tokenizer is unavailable")
    conn.commit()

    # ingest_file uses path.name as the stored filename, so the temp copy must
    # keep the original name. The raw bytes still land under data/raw.
    temp_dir = Path(tempfile.mkdtemp(prefix="advisor_upload_"))
    temp_path = temp_dir / filename
    temp_path.write_bytes(raw)
    try:
        source_id = ingest_file(
            conn,
            collection_id=collection_id,
            path=temp_path,
            content_type=content_type,
            count_tokens=count_tokens,
            embedder=embedder,
            embed_model=settings.embedding_model_id,
            config=config,
            workspace_key=workspace,
        )
        rows = conn.execute(
            queries.get("list_sources"), {"collection_id": collection_id}
        ).fetchall()
        match = next(r for r in rows if r["source_id"] == source_id)
    except Exception as exc:
        conn.rollback()
        raise HTTPException(
            status_code=422, detail="source indexing failed; inspect source status for details"
        ) from exc
    finally:
        temp_path.unlink(missing_ok=True)
        temp_dir.rmdir()
    return _source_doc(match, can_reindex=True)


@router.post("/api/sources/{source_id}/reindex", response_model=SourceDoc)
def reindex_source(
    source_id: UUID,
    body: ReindexRequest,
    conn: Conn,
    workspace: WorkspaceDep,
    settings: SettingsDep,
    config: ConfigDep,
    embedder: EmbedderDep,
) -> SourceDoc:
    row = conn.execute(queries.get("get_owned_source_for_reindex"), {
        "source_id": source_id, "workspace_key": workspace,
    }).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="owned source not found")
    raw_path = Path(row["raw_path"]).resolve()
    raw_root = (settings.data_dir / "raw" / str(row["collection_id"]) / "private"
                / hashlib.sha256(workspace.encode("utf-8")).hexdigest()).resolve()
    if raw_path.parent != raw_root or raw_path.name != row["file_hash"]:
        raise HTTPException(status_code=409, detail="stored source failed integrity validation")
    if hashlib.sha256(raw_path.read_bytes()).hexdigest() != row["file_hash"]:
        raise HTTPException(status_code=409, detail="stored source failed integrity validation")
    count_tokens = getattr(embedder, "count_tokens", None)
    if count_tokens is None:
        raise HTTPException(status_code=503, detail="embedding tokenizer is unavailable")
    conn.commit()
    try:
        ingest_file(
            conn, collection_id=row["collection_id"], path=raw_path,
            content_type=row["content_type"], count_tokens=count_tokens,
            embedder=embedder, embed_model=settings.embedding_model_id, config=config,
            workspace_key=workspace, force=True,
            expected_index_revision=body.expectedIndexRevision,
        )
    except ValueError as exc:
        conn.rollback()
        if "revision changed" in str(exc):
            raise HTTPException(status_code=409, detail="source index revision changed") from exc
        raise HTTPException(status_code=422, detail="source reindex failed") from exc
    except Exception as exc:
        conn.rollback()
        raise HTTPException(status_code=422, detail="source reindex failed") from exc
    updated = conn.execute(queries.get("list_sources_for_workspace"), {
        "collection_id": row["collection_id"], "workspace_key": workspace,
    }).fetchall()
    match = next(item for item in updated if item["source_id"] == source_id)
    return _source_doc(match, can_reindex=True)


@router.get("/api/recommendations", response_model=list[RecommendationDoc])
def recommendations(
    conn: Conn,
    settings: SettingsDep,
    config: ConfigDep,
    embedder: EmbedderDep,
    query: str,
    collection: str | None = None,
) -> list[RecommendationDoc]:
    collection_id, _ = deps.resolve_collection(conn, settings, collection)
    query_vec = embedder.embed_query(query)
    results = recommend(
        conn,
        collection_id=collection_id,
        query_vec=query_vec,
        simgraph=config.simgraph,
    )
    return [
        RecommendationDoc(
            clusterId=str(item.cluster_id),
            path=list(item.path),
            courses=[
                CourseDoc(courseId=str(cid), code=code, title=title)
                for cid, code, title in item.courses
            ],
        )
        for item in results
    ]


@router.get("/api/retired-courses", response_model=list[CourseDoc])
def retired_courses(
    conn: Conn,
    settings: SettingsDep,
    query: str = "",
    collection: str | None = None,
) -> list[CourseDoc]:
    collection_id, _ = deps.resolve_collection(conn, settings, collection)
    pattern = f"%{query.strip()}%" if query.strip() else None
    rows = conn.execute(queries.get("list_retired_courses"), {
        "collection_id": collection_id, "pattern": pattern,
    }).fetchall()
    return [
        CourseDoc(courseId=str(row["course_id"]), code=row["code"],
                  title=row["title"], lifecycle="retired")
        for row in rows
    ]


@router.get("/api/subgraph/{cluster_id}", response_model=SubgraphView)
def subgraph(cluster_id: str, conn: Conn) -> SubgraphView:
    try:
        parsed = UUID(cluster_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid cluster id") from exc
    nodes, edges = subgraph_view(conn, cluster_id=parsed)
    return SubgraphView(
        nodes=nodes,
        edges=edges,
    )
