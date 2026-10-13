"""Shared helpers for API and ingest-backed tests (fake provider/embedder, real Postgres)."""

from dataclasses import replace

from fastapi.testclient import TestClient

from app.api import create_app
from app.config import load_settings
from app.retrieval.config import load_retrieval_config
from tests.fakes import FakeEmbedder, FakeProvider, whitespace_tokens

MODEL = "fake-model"


def lenient_config():
    """Embeddings on with no floors, so keyword routing is deterministic.

    FakeEmbedder similarity is arbitrary; a real floor would make tests flaky.
    """
    config = load_retrieval_config()
    seams = replace(config.seams, embed=replace(config.seams.embed, min_similarity=0.0))
    return replace(config, seams=seams, fusion=replace(config.fusion, min_score=0.0))


def api_client(db, collection, provider=None, embedder=None):
    app = create_app(
        settings=load_settings(),
        allow_test_client=True,
        config=lenient_config(),
        embedder=embedder or FakeEmbedder(dim=8),
        provider=provider or FakeProvider(),
    )
    app.state.override_conn = db
    return TestClient(app)


def ingest_text(
    db,
    collection,
    tmp_path,
    name,
    text,
    *,
    embedder=None,
    model=MODEL,
    content_type="text/plain",
    workspace_key=None,
    commit=False,
):
    """Ingest `text` as a file under `tmp_path`; returns the source id."""
    from app.ingest.pipeline import ingest_file

    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    source_id = ingest_file(
        db,
        collection_id=collection,
        path=path,
        content_type=content_type,
        count_tokens=whitespace_tokens,
        embedder=embedder or FakeEmbedder(dim=8),
        embed_model=model,
        config=lenient_config(),
        workspace_key=workspace_key,
    )
    if commit:
        db.commit()
    return source_id


def collection_name(db, collection):
    return db.execute(
        "SELECT name FROM collections WHERE collection_id = %(c)s", {"c": collection}
    ).fetchone()["name"]
