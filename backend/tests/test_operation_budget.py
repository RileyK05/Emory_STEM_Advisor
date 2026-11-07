import json
import math
from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import load_settings
from app.db import queries
from app.db.connection import connection_dsn
from app.generation.evidence import record_attempt
from app.generation.operation import GenerationOperation
from app.llm.base import (
    AssistantToolMessage,
    GenerationRequest,
    GenerationResult,
    TextMessage,
    ToolCall,
    ToolResultMessage,
    Usage,
)
from app.operations.budget import (
    BudgetExceeded,
    OperationBudgetController,
    OperationBudgetError,
    OperationLimits,
    OperationLimitsConflict,
    estimate_prompt_reservation,
)
from tests.helpers import collection_name, lenient_config
from tests.test_query_advising import _AdvisingProvider, _import_atlas, _PlantedAtlasEmbedder


def test_operation_limits_require_finite_positive_budgets():
    with pytest.raises(ValueError):
        OperationLimits(active_budget_seconds=math.inf)
    with pytest.raises(ValueError):
        OperationLimits(per_call_timeout_seconds=0)
    with pytest.raises(ValueError):
        OperationLimits(max_calls=0)
    with pytest.raises(ValueError):
        Usage(prompt_tokens=-1, completion_tokens=1, source="provider")
    with pytest.raises(ValueError):
        Usage(prompt_tokens=True, completion_tokens=1, source="provider")
    with pytest.raises(ValueError):
        Usage(source="bogus")


def test_prompt_reservation_is_a_bounded_token_estimate():
    from app.operations.budget import estimate_prompt_bytes

    req = GenerationRequest(messages=({"role": "user", "content": "hello 🧪"},))
    estimate = estimate_prompt_reservation(req)
    prompt_bytes = estimate_prompt_bytes(req)
    # A token estimate is positive and never larger than the byte count it is
    # derived from, so the reservation stays conservative (EMO-005).
    assert 1 <= estimate <= prompt_bytes
    with pytest.raises(BudgetExceeded, match="operation input budget"):
        estimate_prompt_reservation(req, max_serialized_bytes=1)
    conversation = GenerationRequest((TextMessage("user", "calculate"),
        AssistantToolMessage((ToolCall("c1", "calculator", {"expression": "2+2"}),)),
        ToolResultMessage("c1", "calculator", '{"result":4}')))
    assert estimate_prompt_reservation(conversation) > estimate


def test_operation_budget_reservation_usage_and_immutable_retry(db):
    from uuid import uuid4

    controller = OperationBudgetController()
    operation_id = uuid4()
    limits = OperationLimits(active_budget_seconds=90, max_calls=2,
                             total_token_budget=200, per_call_output_cap=80,
                             per_call_timeout_seconds=30)
    initial = controller.ensure(db, operation_id, "workspace-a", limits)
    retried = controller.ensure(db, operation_id, "workspace-a", limits)
    assert retried.started_at == initial.started_at
    assert retried.deadline_at == initial.deadline_at
    with pytest.raises(OperationLimitsConflict):
        controller.ensure(db, operation_id, "workspace-a", OperationLimits())

    reservation = controller.reserve_before_call(
        db, operation_id, prompt_reserved_tokens=100, requested_output_tokens=90,
    )
    assert reservation.attempt_no == 1
    assert reservation.output_grant == 80
    assert reservation.timeout_seconds <= 30
    measured = controller.account_usage(
        db, reservation, usage=Usage(prompt_tokens=44, completion_tokens=17, source="provider"),
    )
    assert measured.accounted_tokens == 61
    assert measured.reserved_tokens == 0
    with pytest.raises(OperationBudgetError, match="already accounted"):
        controller.account_usage(db, reservation, usage=None)


def test_unknown_usage_keeps_reservation_and_exhausted_budget_cannot_retry(db):
    from uuid import uuid4

    controller = OperationBudgetController()
    operation_id = uuid4()
    limits = OperationLimits(active_budget_seconds=90, max_calls=2,
                             total_token_budget=100, per_call_output_cap=40,
                             per_call_timeout_seconds=30)
    controller.ensure(db, operation_id, "workspace-b", limits)
    reservation = controller.reserve_before_call(db, operation_id, prompt_reserved_tokens=60)
    assert reservation.output_grant == 40
    status = controller.account_usage(db, reservation, usage=None)
    assert status.reserved_tokens == 100
    assert status.remaining_tokens == 0
    with pytest.raises(BudgetExceeded, match="token budget"):
        controller.reserve_before_call(db, operation_id, prompt_reserved_tokens=1)


@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled"])
def test_late_measured_usage_settles_retained_capacity_exactly_once(db, outcome):
    from uuid import uuid4

    controller = OperationBudgetController()
    operation_id = uuid4()
    controller.ensure(db, operation_id, "late-usage", OperationLimits(
        active_budget_seconds=90, max_calls=1, total_token_budget=100,
        per_call_output_cap=40, per_call_timeout_seconds=30,
    ))
    reservation = controller.reserve_before_call(db, operation_id, prompt_reserved_tokens=60)
    pending = controller.account_usage(db, reservation, usage=None, outcome=outcome)
    assert pending.reserved_tokens == 100
    with pytest.raises(OperationBudgetError, match="already accounted"):
        controller.account_usage(db, reservation, usage=Usage(source="estimated"))

    measured = Usage(prompt_tokens=13, completion_tokens=7, source="provider")
    settled = controller.account_usage(db, reservation, usage=measured, outcome=outcome)
    assert settled.accounted_tokens == 20
    assert settled.reserved_tokens == 0
    assert settled.call_count == 1
    with pytest.raises(OperationBudgetError, match="already accounted"):
        controller.account_usage(db, reservation, usage=measured, outcome=outcome)
    unchanged = controller.get(db, operation_id)
    assert unchanged.accounted_tokens == 20 and unchanged.reserved_tokens == 0
    with pytest.raises(BudgetExceeded):
        controller.reserve_before_call(db, operation_id, prompt_reserved_tokens=1)


def test_concurrent_reservations_cannot_overspend(db, migrated_dsn):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from uuid import uuid4

    from app.db.connection import connect

    controller = OperationBudgetController()
    operation_id = uuid4()
    limits = OperationLimits(active_budget_seconds=90, max_calls=2,
                             total_token_budget=100, per_call_output_cap=40,
                             per_call_timeout_seconds=30)
    with connect(migrated_dsn) as setup:
        controller.ensure(setup, operation_id, "workspace-c", limits)
    barrier = Barrier(2)

    def reserve():
        with connect(migrated_dsn) as conn:
            barrier.wait()
            try:
                return controller.reserve_before_call(
                    conn, operation_id, prompt_reserved_tokens=60,
                )
            except BudgetExceeded:
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reserve(), range(2)))
    assert sum(result is not None for result in results) == 1
    with connect(migrated_dsn) as conn:
        status = controller.get(conn, operation_id)
        assert status.call_count == 1
        assert status.reserved_tokens == 100


@pytest.mark.parametrize("observed", [Usage(11, 7, source="provider"), None, {"tokens": 18}])
def test_failed_generation_usage_audited_and_reserved_conservatively(db, observed):
    operation_id = uuid4()
    settings = load_settings()
    operation = GenerationOperation(db, settings, operation_id, "development")
    request = operation.prepare(GenerationRequest(
        messages=[{"role": "user", "content": "classify this input"}], max_output_tokens=50,
    ))
    error = ValueError("provider output failed validation")
    error.usage = observed
    record_attempt(db, None, settings, None, error, 10,
                   operation_id=operation_id, request=request)
    db.commit()
    operation.account(None, error)
    status = db.execute(queries.get("get_operation_budget_for_update"), {
        "operation_id": operation_id,
    }).fetchone()
    # Test inspection may use SQL; application statements remain named queries.
    audit = db.execute("SELECT * FROM model_calls WHERE operation_id = %s", (operation_id,)).fetchone()
    assert audit["status"] == "failed"
    if isinstance(observed, Usage):
        assert status["accounted_tokens"] == 18
        assert status["reserved_tokens"] == 0
        assert (audit["prompt_tokens"], audit["completion_tokens"], audit["usage_source"]) == (11, 7, "provider")
    else:
        assert status["accounted_tokens"] == 0
        assert status["reserved_tokens"] > 0
        assert audit["usage_source"] == "unknown"
        assert audit["prompt_tokens"] is None and audit["completion_tokens"] is None


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
                       database_url=connection_dsn(db))
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
