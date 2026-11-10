"""Real job transactions around the artifact stage tools and staged publication."""
import json
from dataclasses import replace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import load_settings
from app.db.connection import connect, connection_dsn
from app.generation.artifacts import build_artifact_handlers
from app.jobs.runtime import RunExecutor
from app.jobs.store import JobStore
from app.jobs.worker import run_once
from app.llm.base import GenerationResult, Usage
from tests.fakes import ArtifactStageProvider, FakeEmbedder
from tests.helpers import collection_name, ingest_text, lenient_config, start_run
from tests.test_artifact_generation import outline, section


def message_text(message) -> str:
    return message["content"] if isinstance(message, dict) else message.content


def runtime(db, collection, tmp_path, provider):
    settings = replace(load_settings(), llm_provider="openai", llm_model_id="fake-native",
                       tools_enabled=True, artifact_operation_token_budget=64_000,
                       default_collection=collection_name(db, collection),
                       database_url=connection_dsn(db), data_dir=tmp_path)
    config = lenient_config()
    connect_factory = lambda: connect(connection_dsn(db))
    store = JobStore(connect_factory)
    handlers = build_artifact_handlers(settings, config, FakeEmbedder(8), provider, connect_factory)
    app = create_app(settings=settings, config=config, provider=provider, embedder=FakeEmbedder(8),
                     allow_test_client=True)
    app.state.override_conn, app.state.job_store = db, store
    return TestClient(app), store, RunExecutor(handlers, data_dir=tmp_path)


def test_stage_tools_are_audited_and_published_once(db, collection, tmp_path):
    source = ingest_text(db, collection, tmp_path, "lecture.txt", "Alpha facts.")
    provider = ArtifactStageProvider(outline=outline(), sections={"s1": [section()]})
    client, store, executor = runtime(db, collection, tmp_path, provider)
    inputs = {"prompt": "Alpha", "sourceIds": [str(source)]}
    run_id = start_run(client, "document", inputs, "stage-tools-once")
    finished = run_once(store, "stage-tools", executor)
    assert finished.status.value == "succeeded", finished.error
    assert len(provider.requests) == 2
    assert all(request.temperature == 0 for request in provider.requests)
    assert all(request.response_schema is None for request in provider.requests)
    assert all({tool.name for tool in request.tools} == {"advisor_stage_outline"}
               or {tool.name for tool in request.tools} == {"advisor_stage_section"}
               for request in provider.requests)
    artifact_id = client.get("/api/runs/" + run_id).json()["artifactIds"][0]
    artifact = client.get("/api/artifacts/" + artifact_id).json()
    assert artifact["spec"]["evidence"]["s1:p1"][0]["source_id"] == str(source)
    assert start_run(client, "document", inputs, "stage-tools-once") == run_id
    calls = db.execute("SELECT * FROM model_calls ORDER BY created_at").fetchall()
    assert len(calls) == 2
    assert all(c["sampling_policy"] == "greedy" and c["requested_temperature"] == 0 for c in calls)
    cap = load_settings().artifact_max_output_tokens
    assert all(c["requested_output_tokens"] == cap for c in calls)
    tools = db.execute("SELECT * FROM tool_executions").fetchall()
    assert {t["tool_id"] for t in tools} == {"advisor_stage_outline", "advisor_stage_section"}
    assert all(t["status"] == "completed" for t in tools)
    operation = db.execute("SELECT * FROM operations").fetchone()
    assert operation["operation_id"] == UUID(run_id)
    assert operation["accounted_tokens"] == 260 and operation["call_count"] == 2
    assert len(client.get("/api/artifacts").json()) == 1


@pytest.mark.parametrize("stage", ["section", "outline"])
def test_a_rejected_stage_goes_back_to_the_model_in_the_same_conversation(
        db, collection, tmp_path, stage):
    source = ingest_text(db, collection, tmp_path, "lecture.txt", "Alpha facts.")
    title = "Keep this title"
    if stage == "outline":
        bad = outline(slots=(99,), title=title)
        # The correction sends just the changed field. The accepted title survives.
        provider = ArtifactStageProvider(
            outline=[bad, {"sections": outline()["sections"]}], sections={"s1": [section()]})
    else:
        bad = section(text="Bad PSYC 999.", slots=(99,))
        provider = ArtifactStageProvider(
            outline=outline(title=title), sections={"s1": [bad, section()]})
    client, store, executor = runtime(db, collection, tmp_path, provider)
    run_id = start_run(client, "document", {"prompt": "Alpha", "sourceIds": [str(source)]},
                       "stage-corrected")
    finished = run_once(store, "stage-tools", executor)
    assert finished.status.value == "succeeded", finished.error
    refusal = next(json.loads(message_text(request.messages[-1]))
                   for request in provider.requests
                   if "not accepted" in message_text(request.messages[-1]))
    assert "slots" in refusal["error"]
    assert refusal["previous_arguments"] == bad
    artifact_id = client.get("/api/runs/" + run_id).json()["artifactIds"][0]
    spec = client.get("/api/artifacts/" + artifact_id).json()["spec"]
    assert spec["title"] == title
    assert spec["blocks"][-1]["text"] == "Alpha facts."


def test_an_answer_without_a_stage_tool_never_publishes(db, collection, tmp_path):
    source = ingest_text(db, collection, tmp_path, "lecture.txt", "Alpha facts.")

    class TextOnly:
        def generate(self, request):
            return GenerationResult(text="I wrote the whole artifact here.",
                                    finish_reason="stop",
                                    usage=Usage(100, 20, source="provider"))

    client, store, executor = runtime(db, collection, tmp_path, TextOnly())
    store.create(kind="artifact.run", scope_key="development", idempotency_key="no-stage",
                 max_attempts=1, payload={"kind": "document", "inputs": {
                     "prompt": "Alpha", "sourceIds": [str(source)]}})
    finished = run_once(store, "stage-tools", executor)
    assert finished.status.value == "failed"
    assert finished.error["type"] == "invalid_output"
    assert client.get("/api/artifacts").json() == []
    assert db.execute("SELECT count(*) AS n FROM model_calls").fetchone()["n"] == 1


def test_a_long_outline_purpose_is_shortened_not_refused():
    """A model that writes the section into its purpose still gets its outline accepted."""
    from app.generation.native_tools import OutlineSection

    long = "Cover the first term and every course in it. " * 40
    section = OutlineSection(id="s1", heading="Term 1", purpose=long, block_types=["paragraph"])
    assert 0 < len(section.purpose) <= 600
    assert section.purpose.endswith(".") and long.startswith(section.purpose)
