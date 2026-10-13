"""Real job transactions around native provider tools and staged artifacts."""
import json
from dataclasses import replace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.api import create_app
from app.db.connection import connect
from app.generation.artifacts import build_artifact_handlers
from app.jobs.runtime import RunExecutor
from app.jobs.store import JobStore
from app.jobs.worker import run_once
from app.llm.base import GenerationResult, ToolCall, ToolResultMessage, Usage
from tests.fakes import FakeEmbedder
from tests.helpers import collection_name, ingest_text, lenient_config
from tests.test_artifact_generation import SPEC, candidate


def runtime(db, collection, tmp_path, provider):
    from app.config import load_settings
    settings = replace(load_settings(), llm_provider="openai", llm_model_id="fake-native",
                       tools_enabled=True, operation_token_budget=64_000,
                       default_collection=collection_name(db, collection),
                       database_url=db.info.dsn, data_dir=tmp_path)
    config = lenient_config()
    connect_factory = lambda: connect(db.info.dsn)
    store = JobStore(connect_factory)
    handlers = build_artifact_handlers(settings, config, FakeEmbedder(8), provider, connect_factory)
    app = create_app(settings=settings, config=config, provider=provider, embedder=FakeEmbedder(8),
                     allow_test_client=True)
    app.state.override_conn, app.state.job_store = db, store
    return TestClient(app), store, RunExecutor(handlers, data_dir=tmp_path)


def test_calculator_then_terminal_stage_is_audited_and_published_once(db, collection, tmp_path):
    source = ingest_text(db, collection, tmp_path, "lecture.txt", "Alpha facts.")
    class Provider:
        def __init__(self):
            self.calls = []
        def generate(self, request):
            self.calls.append(request)
            assert request.temperature == 0
            assert request.response_schema is None
            assert "advisor_stage_artifact" in {t.name for t in request.tools}
            assert db.execute("SELECT count(*) AS n FROM artifacts").fetchone()["n"] == 0
            db.commit()
            if len(self.calls) == 1:
                return GenerationResult(tool_calls=(ToolCall(
                    "calculate-1", "advisor_calculate", {"expression": "0.1+0.2"}),),
                    finish_reason="tool_calls", usage=Usage(100, 10, source="provider"))
            result_message = request.messages[-1]
            assert isinstance(result_message, ToolResultMessage)
            assert json.loads(result_message.content)["result"] == "0.3"
            return GenerationResult(tool_calls=(ToolCall(
                "stage-1", "advisor_stage_artifact", {"candidate": json.loads(candidate(
                    SPEC, [{"element_id": "p1", "source_slots": [1]}]))}),),
                finish_reason="tool_calls", usage=Usage(120, 30, source="provider"), model="fake-native")
    provider = Provider()
    client, store, executor = runtime(db, collection, tmp_path, provider)
    body = {"kind": "document", "inputs": {"prompt": "Alpha", "sourceIds": [str(source)]},
            "idempotencyKey": "native-tools-once"}
    queued = client.post("/api/runs", json=body).json()
    finished = run_once(store, "native-tools", executor)
    assert finished.status.value == "succeeded"
    assert len(provider.calls) == 2
    artifact_id = client.get("/api/runs/" + queued["runId"]).json()["artifactIds"][0]
    artifact = client.get("/api/artifacts/" + artifact_id).json()
    assert artifact["spec"]["evidence"]["p1"][0]["source_id"] == str(source)
    assert client.post("/api/runs", json=body).json()["runId"] == queued["runId"]
    calls = db.execute("SELECT * FROM model_calls ORDER BY created_at").fetchall()
    assert len(calls) == 2
    assert all(c["sampling_policy"] == "greedy" and c["requested_temperature"] == 0 for c in calls)
    assert all(c["requested_output_tokens"] == 1024 for c in calls)
    tools = db.execute("SELECT * FROM tool_executions").fetchall()
    assert {t["tool_id"] for t in tools} == {"advisor_calculate", "advisor_stage_artifact"}
    assert all(t["status"] == "completed" for t in tools)
    operation = db.execute("SELECT * FROM operations").fetchone()
    assert operation["operation_id"] == UUID(queued["runId"])
    assert operation["accounted_tokens"] == 260 and operation["call_count"] == 2
    assert len(client.get("/api/artifacts").json()) == 1


def test_tool_cannot_read_an_unselected_resource_or_publish(db, collection, tmp_path):
    source = ingest_text(db, collection, tmp_path, "lecture.txt", "Alpha facts.")
    class Provider:
        def generate(self, request):
            return GenerationResult(tool_calls=(ToolCall("bad-read", "advisor_read_source",
                {"resource_id": str(uuid4())}),), finish_reason="tool_calls",
                usage=Usage(100, 10, source="provider"), model="fake-native")
    client, store, executor = runtime(db, collection, tmp_path, Provider())
    job = store.create(kind="artifact.run", scope_key="development", idempotency_key="bad-read",
                       max_attempts=1, payload={"kind": "document", "inputs": {
                           "prompt": "Alpha", "sourceIds": [str(source)]}})
    finished = run_once(store, "native-tools", executor)
    assert finished.status.value == "failed" and finished.error["type"] == "tool_failed"
    assert client.get("/api/artifacts").json() == []
    # The out-of-scope read is refused before any read and recorded as that
    # refusal; the model's retry of the same call cannot publish either.
    assert db.execute("SELECT count(*) AS n FROM model_calls").fetchone()["n"] == 2
    tool = db.execute("SELECT status, result_json FROM tool_executions").fetchone()
    assert tool["status"] == "completed" and "outside its scope" in tool["result_json"]
    assert db.execute("SELECT accounted_tokens FROM operations WHERE operation_id=%s",
                      (job.job_id,)).fetchone()["accounted_tokens"] == 220
