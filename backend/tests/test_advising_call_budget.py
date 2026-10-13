"""Independent native advising tools can share a bounded provider turn."""

import json
from dataclasses import replace

from fastapi.testclient import TestClient

from app.api import create_app
from app.config import load_settings
from app.llm.base import GenerationResult, ToolCall, ToolResultMessage, Usage
from tests.helpers import collection_name, lenient_config
from tests.test_query_advising import _AdvisingProvider, _import_atlas, _PlantedAtlasEmbedder


def test_three_independent_advising_tools_fit_in_two_provider_calls(db, collection, tmp_path):
    source_id = _import_atlas(db, collection, tmp_path)

    class Provider:
        calls = 0

        def generate(self, request):
            self.calls += 1
            if self.calls == 1:
                resources = _AdvisingProvider._resource_pattern.findall(
                    request.messages[1]["content"]
                )
                codes = ["CS 110", "CS 120", "CS 220"]
                return GenerationResult(tool_calls=(
                    ToolCall("topics", "advisor_explore_topics", {"resource_ids": resources}),
                    ToolCall("requirements", "advisor_read_course_requirements", {
                        "resource_ids": resources, "course_codes": codes,
                    }),
                    ToolCall("sequence", "advisor_plan_course_sequence", {
                        "resource_ids": resources, "selected_codes": codes,
                    }),
                ), finish_reason="tool_calls", usage=Usage(100, 10, source="provider"))
            assert self.calls == 2
            results = {message.name: json.loads(message.content) for message in request.messages
                       if isinstance(message, ToolResultMessage)}
            assert len(results) == 3
            plan = results["advisor_plan_course_sequence"]
            assert plan["stages"] == [["CS 110"], ["CS 120"], ["CS 220"]]
            citations = sorted({number for course in plan["evidence"]
                                for number in course["citation_numbers"]})
            return GenerationResult(
                text="Dependency order: CS 110, CS 120, CS 220 "
                     + " ".join(f"[{number}]" for number in citations) + ".",
                finish_reason="stop", usage=Usage(100, 10, source="provider"),
            )

    provider = Provider()
    settings = replace(load_settings(), tools_enabled=True, llm_provider="openai",
                       llm_model_id="offline-advising-tools", operation_max_calls=2,
                       database_url=db.info.dsn)
    app = create_app(settings=settings, allow_test_client=True, config=lenient_config(),
                     embedder=_PlantedAtlasEmbedder(), provider=provider)
    app.state.override_conn = db
    response = TestClient(app).post("/api/query", json={
        "query": "Compare CS 110, CS 120 and CS 220 course topics and prerequisite order.",
        "collectionId": collection_name(db, collection), "sourceIds": [str(source_id)],
    })
    assert response.status_code == 200, response.text
    assert provider.calls == 2
    assert db.execute("SELECT call_count FROM operations").fetchone()["call_count"] == 2
    assert db.execute("SELECT count(*) AS n FROM tool_executions "
                      "WHERE status='completed'").fetchone()["n"] == 3
