"""FastAPI application factory.

Providers are constructed once at startup and stored on `app.state`:
- `embedder`, `provider` — real ones by default; tests overwrite both.
- `settings`, `config` — env + retrieval.toml.
- `override_conn` — optional; tests set it to borrow their DB fixture.
"""

from fastapi import FastAPI

from app.config import Settings, load_settings
from app.embeddings import get_embedder
from app.llm import get_provider
from app.retrieval.config import RetrievalConfig, load_retrieval_config


def create_app(
    *,
    settings: Settings | None = None,
    config: RetrievalConfig | None = None,
    embedder=None,
    provider=None,
) -> FastAPI:
    from app.api.routes import router

    app = FastAPI(title="Emory STEM Advisor API")
    app.state.settings = settings or load_settings()
    app.state.config = config or load_retrieval_config(settings=app.state.settings)
    app.state.embedder = embedder or get_embedder(app.state.settings)
    app.state.provider = provider or get_provider(app.state.settings)
    app.state.override_conn = None
    app.include_router(router)
    return app


def app() -> FastAPI:  # uvicorn target: `uvicorn app.api:app`
    return create_app()
