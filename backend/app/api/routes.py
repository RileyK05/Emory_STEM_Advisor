"""HTTP routes. Thin: resolve collection, call the pipeline, serialize."""

import shutil
import tempfile
import time
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from psycopg import Connection

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
from app.api.serialize import answer_response, refusal_response
from app.config import Settings
from app.db import queries
from app.embeddings.base import Embedder
from app.generation.answer import answer_from_context
from app.ingest.pipeline import ingest_file
from app.llm.base import Provider
from app.retrieval.config import RetrievalConfig
from app.retrieval.retrieve import retrieve
from app.retrieval.types import Refusal
from app.simgraph.view import subgraph_view
from app.simgraph.walk import recommend

router = APIRouter()

Conn = Annotated[Connection, Depends(deps.get_conn)]
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


def _count_tokens(text: str) -> int:
    # The upload path approximates tokens; the scripts use the model tokenizer.
    # Chunking is token-bounded either way, just not identically.
    return len(text.split())


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/api/collections", response_model=list[CollectionDoc])
def list_collections(conn: Conn) -> list[CollectionDoc]:
    rows = conn.execute(queries.get("list_collections")).fetchall()
    return [CollectionDoc(id=str(row["collection_id"]), name=row["name"]) for row in rows]


@router.get("/api/sources", response_model=list[SourceDoc])
def list_sources(
    conn: Conn,
    settings: SettingsDep,
    collection: str | None = None,
) -> list[SourceDoc]:
    collection_id, _ = deps.resolve_collection(conn, settings, collection)
    rows = conn.execute(
        queries.get("list_sources"), {"collection_id": collection_id}
    ).fetchall()
    return [
        SourceDoc(
            id=str(row["source_id"]),
            name=row["filename"],
            locatorType=row["locator_type"],
            status=row["status"],
            chunkCount=row["chunk_count"],
        )
        for row in rows
    ]


@router.post("/api/query", response_model=AnswerResponse | RefusalResponse)
def query(
    body: QueryRequest,
    conn: Conn,
    settings: SettingsDep,
    config: ConfigDep,
    embedder: EmbedderDep,
    provider: ProviderDep,
) -> AnswerResponse | RefusalResponse:
    collection_id, _ = deps.resolve_collection(conn, settings, body.collectionId)
    started = time.perf_counter()
    outcome = retrieve(
        conn,
        collection_id=collection_id,
        query=body.query,
        embedder=embedder,
        embed_model=settings.embedding_model_id,
        config=config,
    )
    latency_ms = int((time.perf_counter() - started) * 1000)

    if isinstance(outcome, Refusal):
        return refusal_response(outcome, settings=settings, latency_ms=latency_ms)

    answer = answer_from_context(outcome.chunks, body.query, provider=provider)
    return answer_response(
        outcome,
        answer,
        settings=settings,
        trace_id=outcome.trace.trace_id,
        latency_ms=latency_ms,
    )


@router.post("/api/sources", response_model=SourceDoc)
def upload_source(
    conn: Conn,
    settings: SettingsDep,
    config: ConfigDep,
    embedder: EmbedderDep,
    file: Annotated[UploadFile, File()],
    collection: Annotated[str | None, Form()] = None,
) -> SourceDoc:
    collection_id, _ = deps.resolve_collection(conn, settings, collection)
    raw = file.file.read()
    filename = file.filename or "upload"
    content_type = file.content_type or _content_type(filename)
    if content_type == "application/octet-stream":
        content_type = _content_type(filename)

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
            count_tokens=_count_tokens,
            embedder=embedder,
            embed_model=settings.embedding_model_id,
            config=config,
        )
        rows = conn.execute(
            queries.get("list_sources"), {"collection_id": collection_id}
        ).fetchall()
        match = next(r for r in rows if r["source_id"] == source_id)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    return SourceDoc(
        id=str(match["source_id"]),
        name=match["filename"],
        locatorType=match["locator_type"],
        status=match["status"],
        chunkCount=match["chunk_count"],
    )


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


@router.get("/api/subgraph/{cluster_id}", response_model=SubgraphView)
def subgraph(cluster_id: str, conn: Conn) -> SubgraphView:
    try:
        parsed = UUID(cluster_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid cluster id") from exc
    nodes, edges = subgraph_view(conn, cluster_id=parsed)
    return SubgraphView(
        nodes=[
            CourseDoc(courseId=str(n["course_id"]), code=n["code"], title=n["title"])
            for n in nodes
        ],
        edges=[{k: str(v) for k, v in edge.items()} for edge in edges],
    )
