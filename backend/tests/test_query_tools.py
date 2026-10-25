"""Grounded query tools use the durable ledger and remain citation scoped."""
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.api.query_service import query_response
from app.api.schemas import QueryRequest
from app.config import load_settings
from app.db.connection import connection_dsn
from app.flags.service import FeatureFlagService
from app.llm.base import GenerationResult, StreamEvent, ToolCall, Usage
from tests.fakes import FakeEmbedder
from tests.helpers import collection_name, ingest_text, lenient_config


def api_client(db, provider):
    settings = replace(load_settings(), tools_enabled=True, llm_provider="openai",
                       llm_model_id="mock-tools", database_url=connection_dsn(db))
    app = create_app(settings=settings, allow_test_client=True, config=lenient_config(),
                     embedder=FakeEmbedder(8), provider=provider)
    app.state.override_conn = db
    return TestClient(app)


class _QueryProvider:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def generate(self, request):
        self.calls.append(request)
        return next(self.responses)


@pytest.mark.parametrize("stop_tools", [False, True])
def test_query_calculator_then_final_answer_accounts_and_persists_each_call(db, collection, tmp_path,
                                                                          monkeypatch, stop_tools):
    ingest_text(db, collection, tmp_path, "alpha.txt", "Alpha has value 0.3.\n")
    provider = _QueryProvider([
        GenerationResult(tool_calls=(ToolCall("query-calc", "advisor_calculate",
            {"expression": "0.1+0.2"}),), finish_reason="tool_calls",
            usage=Usage(100, 10, source="provider")),
        GenerationResult(text="The value is 0.3 [1].", finish_reason="stop",
            usage=Usage(120, 15, source="provider")),
    ])
    generate = provider.generate

    def maybe_stop(request):
        result = generate(request)
        if stop_tools:
            FeatureFlagService.set_kill_switch(db, "tools", True, "test stop during call")
        return result

    monkeypatch.setattr(provider, "generate", maybe_stop)
    response = api_client(db, provider).post("/api/query", json={
        "query": "alpha", "collectionId": collection_name(db, collection),
    })
    if stop_tools:
        assert response.status_code == 409, response.text
        row = db.execute("SELECT sum(prompt_tokens) AS tokens, count(*) AS calls FROM model_calls").fetchone()
        assert (row["tokens"], row["calls"]) == (100, 1)
        assert db.execute("SELECT count(*) AS calls FROM tool_executions").fetchone()["calls"] == 0
        return
    assert response.status_code == 200
    assert response.json()["answer"] == "The value is 0.3 [1]."
    assert len(provider.calls) == 2
    assert provider.calls[0].temperature == 0
    tool_names = {tool.name for tool in provider.calls[0].tools}
    assert tool_names == {"advisor_calculate", "advisor_read_source"}
    tool_result = next(m for m in provider.calls[1].messages
                       if getattr(m, "tool_call_id", None) == "query-calc")
    assert json.loads(tool_result.content)["result"] == "0.3"
    assert provider.calls[1].tools == ()  # the last allowed call answers from results
    rows = db.execute("SELECT * FROM model_calls ORDER BY created_at").fetchall()
    assert len(rows) == 2
    assert [row["prompt_tokens"] for row in rows] == [100, 120]
    tool = db.execute("SELECT * FROM tool_executions").fetchone()
    assert tool["tool_id"] == "advisor_calculate" and tool["status"] == "completed"
    operation = db.execute("SELECT * FROM operations").fetchone()
    assert operation["accounted_tokens"] == 245
    assert operation["call_count"] == 2


def test_query_tool_cannot_read_a_chunk_outside_exact_citation_scope(db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "alpha.txt", "Alpha fact.\n")
    provider = _QueryProvider([
        GenerationResult(tool_calls=(ToolCall(
            "query-read", "advisor_read_source", {"resource_id": "not-authorized"}),),
            finish_reason="tool_calls", usage=Usage(50, 4, source="provider")),
        GenerationResult(text="Alpha fact [1].", finish_reason="stop",
                         usage=Usage(60, 5, source="provider")),
    ])
    response = api_client(db, provider).post("/api/query", json={
        "query": "alpha", "collectionId": collection_name(db, collection),
    })
    # The out-of-scope request is refused before any read; the model sees the
    # refusal and must answer from its own context.
    assert response.status_code == 200
    refusal = next(m for m in provider.calls[1].messages
                   if getattr(m, "tool_call_id", None) == "query-read")
    assert json.loads(refusal.content) == {
        "error": "Tool request references a resource outside its scope."}
    tool = db.execute("SELECT * FROM tool_executions").fetchone()
    assert tool["tool_id"] == "advisor_read_source" and tool["status"] == "completed"
    operation = db.execute("SELECT * FROM operations").fetchone()
    assert operation["accounted_tokens"] == 119


def test_incomplete_query_tool_response_is_accounted_without_execution(db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "alpha.txt", "Alpha fact.\n")
    provider = _QueryProvider([GenerationResult(tool_calls=(ToolCall(
        "partial-calc", "advisor_calculate", {"expression": "1+1"}),),
        finish_reason="length", usage=Usage(40, 10, source="provider"))])
    response = api_client(db, provider).post("/api/query", json={
        "query": "alpha", "collectionId": collection_name(db, collection),
    })
    assert response.status_code == 422
    assert len(provider.calls) == 1
    assert db.execute("SELECT count(*) AS n FROM tool_executions").fetchone()["n"] == 0
    assert db.execute("SELECT accounted_tokens FROM operations").fetchone()["accounted_tokens"] == 50


def test_plain_query_stream_callback_path_does_not_pass_tool_reset_keyword(db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "alpha.txt", "Alpha is a documented concept.\n")
    settings = replace(load_settings(), tools_enabled=False, llm_provider="openai",
                       llm_model_id="offline-stream", database_url=connection_dsn(db))

    class StreamProvider:
        def stream(self, request):
            yield StreamEvent("text_delta", text="Alpha is documented [1].")
            yield StreamEvent("completed", result=GenerationResult(
                text="Alpha is documented [1].", finish_reason="stop",
                usage=Usage(20, 8, source="provider"),
            ))

    events = []
    answer = query_response(
        QueryRequest(query="alpha", collectionId=collection_name(db, collection)),
        db, settings, lenient_config(), FakeEmbedder(8), StreamProvider(),
        on_provisional_text=lambda text: events.append(("text", text)),
        on_provisional_reset=lambda: events.append(("reset", None)),
    )
    assert answer.answer == "Alpha is documented [1]."
    assert events == [("text", "Alpha is documented [1].")]
    assert db.execute("SELECT count(*) AS n FROM model_calls WHERE status='completed'").fetchone()["n"] == 1


def test_tool_query_stream_callbacks_reset_tool_attempt_output(db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "alpha.txt", "Alpha has value 0.3.\n")
    settings = replace(load_settings(), tools_enabled=True, llm_provider="openai",
                       llm_model_id="offline-tools", database_url=connection_dsn(db))

    class StreamToolProvider:
        def __init__(self):
            self.calls = 0

        def stream(self, request):
            self.calls += 1
            if self.calls == 1:
                yield StreamEvent("text_delta", text="tool planning")
                yield StreamEvent("completed", result=GenerationResult(
                    tool_calls=(ToolCall("query-stream-calc", "advisor_calculate",
                                         {"expression": "0.1+0.2"}),),
                    finish_reason="tool_calls", usage=Usage(22, 6, source="provider"),
                ))
            else:
                yield StreamEvent("text_delta", text="The value is 0.3 [1].")
                yield StreamEvent("completed", result=GenerationResult(
                    text="The value is 0.3 [1].", finish_reason="stop",
                    usage=Usage(28, 10, source="provider"),
                ))

    events = []
    answer = query_response(
        QueryRequest(query="alpha", collectionId=collection_name(db, collection)),
        db, settings, lenient_config(), FakeEmbedder(8), StreamToolProvider(),
        on_provisional_text=lambda text: events.append(("text", text)),
        on_provisional_reset=lambda: events.append(("reset", None)),
    )
    assert answer.answer == "The value is 0.3 [1]."
    assert events == [("text", "tool planning"), ("reset", None),
                      ("text", "The value is 0.3 [1].")]
    calls = db.execute("SELECT status, prompt_tokens, completion_tokens "
                       "FROM model_calls ORDER BY created_at").fetchall()
    assert len(calls) == 2
    assert [(item["status"], item["prompt_tokens"], item["completion_tokens"])
            for item in calls] == [("completed", 22, 6), ("completed", 28, 10)]
