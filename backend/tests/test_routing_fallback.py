"""Deterministic routing and one-budget integrity escalation with fake providers."""

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import load_settings
from app.connections.models import EvaluationEvidence, ProfileCreate
from app.db.connection import connect
from app.llm.base import GenerationResult, Usage
from app.llm.routing import (
    Candidate,
    RoutingConfig,
    choose_route,
    classify_role,
    quality_fallback,
)
from tests.fakes import FakeEmbedder
from tests.helpers import collection_name, ingest_text, lenient_config


class SequenceProvider:
    def __init__(self, model, responses):
        self.provider = "openai"
        self.model = model
        self.responses = list(responses)
        self.calls = 0
        self.on_call = None

    def generate(self, request):
        self.calls += 1
        if self.on_call:
            self.on_call()
        if not self.responses:
            raise AssertionError("provider received an unexpected extra call")
        return self.responses.pop(0)


class RoutingRuntime:
    def __init__(self, providers):
        self.providers = providers
        self.selected_roles = []

    def generation_provider(self, profile, *, role, **_kwargs):
        self.selected_roles.append(role)
        return self.providers[profile.model_id]


def _result(text, model, *, finish="stop", prompt=20, completion=10):
    return GenerationResult(
        text=text, model=model, finish_reason=finish,
        usage=Usage(prompt_tokens=prompt, completion_tokens=completion, source="provider"),
    )


def _setup(db, collection, migrated_dsn, tmp_path, *, tools=False,
           operation_max_calls=2, operation_token_budget=16_000):
    suite_hash = "a" * 64
    settings = replace(
        load_settings(), database_url=migrated_dsn, data_dir=tmp_path,
        default_collection=collection_name(db, collection), routing_enabled=True,
        routing_eval_suite_hash=suite_hash, profile_encryption_key=Fernet.generate_key().decode(),
        tools_enabled=tools, operation_max_calls=operation_max_calls,
        operation_token_budget=operation_token_budget, llm_max_output_tokens=128,
    )
    standard = SequenceProvider("standard-model", [])
    complex_provider = SequenceProvider("complex-model", [])
    runtime = RoutingRuntime({"standard-model": standard, "complex-model": complex_provider})
    app = create_app(settings=settings, allow_test_client=True, config=lenient_config(),
                     embedder=FakeEmbedder(dim=8), provider=object())
    app.state.override_conn = db
    app.state.connection_runtime = runtime
    profiles = {}
    caps = {"structured_output", "tools"} if tools else {"structured_output"}
    with connect(migrated_dsn) as conn:
        for role, model in (("standard", standard.model), ("complex", complex_provider.model)):
            profile = app.state.connection_profiles.create(conn, ProfileCreate(
                name=f"{role} test route", provider="openai", model_id=model,
                roles=frozenset({role}), capabilities=frozenset(caps),
                data_scopes=frozenset({"institutional_corpus", "user_supplied_data"}),
                secret={"api_key": f"not-a-real-{role}-key"}, priority=10,
            ))
            app.state.connection_profiles.record_evaluation(conn, EvaluationEvidence(
                evaluation_id=uuid4(), profile_id=profile.profile_id,
                profile_revision=profile.revision, provider=profile.provider,
                model_id=profile.model_id, suite_hash=suite_hash, status="passed",
                metrics={"test_fixture": 1.0},
            ))
            profiles[role] = profile
        conn.commit()
    ingest_text(db, collection, tmp_path, "math.txt",
            "MATH 111 prerequisites include MATH 100. This is the course description.")
    return app, runtime, profiles, standard, complex_provider


def _post_query(client, collection, db, query="What are the prerequisites for MATH 111?"):
    return client.post("/api/query", json={
        "query": query, "collectionId": collection_name(db, collection),
    })


def _route_config():
    return RoutingConfig.load(__import__("pathlib").Path(__file__).parents[1] / "configs" / "routing.toml")


def test_explicit_course_lookup_patterns_route_simple_and_leave_uncertainty_complex():
    config = _route_config()
    assert classify_role(config, query="What are the prerequisites for MATH 111?")[0] == "standard"
    assert classify_role(config, query="MATH 111")[0] == "standard"
    assert classify_role(config, query="Compare MATH 111 and MATH 112")[0] == "complex"
    assert classify_role(config, query="What are prerequisites for a math major?")[0] == "complex"
    assert classify_role(config, query="What is MATH 111 and how does it compare to MATH 112?")[0] == "complex"
    assert classify_role(config, query="x", job_kind="batch_generation")[0] == "fast_generation"
    assert classify_role(config, query="x", job_kind="binary_decision")[0] == "decision"


@pytest.mark.parametrize(("first_text", "finish"), [
    ("MATH 111 requires MATH 100 [99].", "stop"),
    ("MATH 111 requires MATH 100 [1.", "stop"),
    ("MATH 111 requires MATH 100.", "stop"),
    ("MATH 111 requires MATH 100 [1].", "length"),
])
def test_standard_integrity_rejection_escalates_once_under_same_operation(
        db, collection, migrated_dsn, tmp_path, first_text, finish):
    app, runtime, _profiles, standard, complex_provider = _setup(
        db, collection, migrated_dsn, tmp_path)
    standard.responses = [_result(first_text, standard.model, finish=finish)]
    complex_provider.responses = [_result("MATH 111 requires MATH 100 [1].", complex_provider.model)]
    response = _post_query(TestClient(app), collection, db)
    assert response.status_code == 200, response.text
    assert response.json()["type"] == "answer"
    assert response.json()["answer"] == "MATH 111 requires MATH 100 [1]."
    assert runtime.selected_roles == ["standard", "complex"]
    assert standard.calls == complex_provider.calls == 1
    trace_id = response.json()["trace"]["traceId"]
    with connect(migrated_dsn) as conn:
        calls = conn.execute(
            "SELECT provider,model_id,prompt_tokens,completion_tokens,operation_id "
            "FROM model_calls WHERE trace_id=%s ORDER BY model_id", (trace_id,),
        ).fetchall()
        assert len(calls) == 2
        assert {row["model_id"] for row in calls} == {standard.model, complex_provider.model}
        assert {(row["prompt_tokens"], row["completion_tokens"]) for row in calls} == {(20, 10)}
        assert len({row["operation_id"] for row in calls}) == 1
        operation = conn.execute(
            "SELECT call_count,accounted_tokens,reserved_tokens FROM operations WHERE operation_id=%s",
            (calls[0]["operation_id"],),
        ).fetchone()
    assert operation["call_count"] == 2
    assert operation["accounted_tokens"] == 60 and operation["reserved_tokens"] == 0


@pytest.mark.parametrize("limit_kind", ["calls", "tokens", "deadline"])
def test_operation_limits_withhold_escalation_and_keep_first_usage(
        db, collection, migrated_dsn, tmp_path, limit_kind):
    kwargs = {"operation_max_calls": 1 if limit_kind == "calls" else 2,
              "operation_token_budget": 5_000}
    app, _runtime, _profiles, standard, complex_provider = _setup(
        db, collection, migrated_dsn, tmp_path, **kwargs)

    def expire_operation():
        if limit_kind == "deadline":
            with connect(migrated_dsn) as conn:
                conn.execute(
                    "UPDATE operations SET started_at=clock_timestamp()-interval '10 seconds', "
                    "deadline_at=clock_timestamp()-interval '1 second'"
                )
                conn.commit()

    usage_prompt = 5_001 if limit_kind == "tokens" else 20
    standard.responses = [_result("MATH 111 requires MATH 100 [99].", standard.model,
                                  prompt=usage_prompt)]
    standard.on_call = expire_operation
    complex_provider.responses = [_result("MATH 111 requires MATH 100 [1].", complex_provider.model)]
    response = _post_query(TestClient(app), collection, db)
    assert response.status_code == 422, response.text
    assert standard.calls == 1 and complex_provider.calls == 0
    with connect(migrated_dsn) as conn:
        call = conn.execute(
            "SELECT status,measured_prompt_tokens,measured_completion_tokens "
            "FROM operation_calls ORDER BY operation_id DESC,attempt_no LIMIT 1"
        ).fetchone()
    if limit_kind == "calls":
        assert call["measured_prompt_tokens"] == 20 and call["status"] == "completed"
    elif limit_kind == "tokens":
        assert call["measured_prompt_tokens"] == 5_001 and call["status"] == "completed"
    else:
        assert call["measured_prompt_tokens"] == 20 and call["status"] == "completed"


@pytest.mark.parametrize("rotated_role", ["standard", "complex"])
def test_rotation_of_either_attempt_profile_withholds_answer_but_keeps_usage(
        db, collection, migrated_dsn, tmp_path, rotated_role):
    app, _runtime, profiles, standard, complex_provider = _setup(
        db, collection, migrated_dsn, tmp_path)
    standard.responses = [_result("MATH 111 requires MATH 100 [99].", standard.model)]
    complex_provider.responses = [_result("MATH 111 requires MATH 100 [1].", complex_provider.model)]

    def rotate_used_profile():
        profile = profiles[rotated_role]
        with connect(migrated_dsn) as conn:
            app.state.connection_profiles.clear_secret(
                conn, profile.profile_id, expected_revision=profile.revision)
            conn.commit()

    # Rotate after both routes were resolved and the complex response is returned.
    complex_provider.on_call = rotate_used_profile
    response = _post_query(TestClient(app), collection, db)
    assert response.status_code == 409
    assert standard.calls == complex_provider.calls == 1
    with connect(migrated_dsn) as conn:
        calls = conn.execute(
            "SELECT model_id,prompt_tokens,completion_tokens,operation_id FROM model_calls"
        ).fetchall()
    assert len(calls) == 2
    assert all(row["prompt_tokens"] == 20 and row["completion_tokens"] == 10 for row in calls)
    assert len({row["operation_id"] for row in calls}) == 1


def test_tools_enabled_integrity_rejection_does_not_cross_profile_ledger(
        db, collection, migrated_dsn, tmp_path):
    app, runtime, _profiles, standard, complex_provider = _setup(
        db, collection, migrated_dsn, tmp_path, tools=True)
    standard.responses = [_result("MATH 111 requires MATH 100 [99].", standard.model)]
    complex_provider.responses = [_result("MATH 111 requires MATH 100 [1].", complex_provider.model)]
    response = _post_query(TestClient(app), collection, db)
    assert response.status_code == 200, response.text
    assert response.json()["type"] == "refusal"
    assert standard.calls == 1 and complex_provider.calls == 0
    assert runtime.selected_roles == ["standard"]
    trace = response.json()["trace"]
    assert trace["modelCall"]["modelId"] == standard.model
    assert any("citation" in warning.lower() for warning in trace["warnings"])
    with connect(migrated_dsn) as conn:
        calls = conn.execute(
            "SELECT model_id,prompt_tokens,completion_tokens FROM model_calls"
        ).fetchall()
        ledger = conn.execute(
            "SELECT call_count,accounted_tokens,reserved_tokens FROM operations"
        ).fetchall()
    assert len(calls) == 1 and calls[0]["model_id"] == standard.model
    assert calls[0]["prompt_tokens"] == 20 and calls[0]["completion_tokens"] == 10
    assert len(ledger) == 1 and ledger[0]["call_count"] == 1
    assert ledger[0]["accounted_tokens"] == 30 and ledger[0]["reserved_tokens"] == 0


def test_refusal_does_not_trigger_quality_escalation(db, collection, migrated_dsn, tmp_path):
    app, runtime, _profiles, standard, complex_provider = _setup(
        db, collection, migrated_dsn, tmp_path)
    standard.responses = [GenerationResult(
        refusal="I cannot answer from this context.", model=standard.model,
        usage=Usage(prompt_tokens=20, completion_tokens=10, source="provider"),
    )]
    complex_provider.responses = [_result("MATH 111 requires MATH 100 [1].", complex_provider.model)]
    response = _post_query(TestClient(app), collection, db)
    assert response.status_code == 200
    assert response.json()["type"] == "refusal"
    assert standard.calls == 1 and complex_provider.calls == 0
    assert runtime.selected_roles == ["standard"]


def test_router_requires_eval_passed_profile_and_defaults_uncertain_to_complex():
    config = RoutingConfig.load(Path(__file__).resolve().parents[1] / "configs" / "routing.toml")
    candidates = [Candidate("cheap", "standard", "openai", "small", eval_passed=True),
                 Candidate("strong", "complex", "anthropic", "large", eval_passed=True)]
    decision = choose_route(config, candidates, query="what is this?")
    assert decision.candidate.profile_id == "strong"
    assert quality_fallback(config, candidates[0], candidates).profile_id == "strong"


def test_router_cannot_select_nonpassing_or_wrong_scope_profile():
    config = RoutingConfig.load(Path(__file__).resolve().parents[1] / "configs" / "routing.toml")
    candidates = [Candidate("unsafe", "complex", "x", "m", data_scopes=frozenset({"internal"}), eval_passed=True),
                 Candidate("untested", "complex", "x", "m", data_scopes=frozenset({"user"}), eval_passed=False)]
    with pytest.raises(LookupError):
        choose_route(config, candidates, query="uncertain", data_scope="user")


def test_router_config_rejects_unknown_keys(tmp_path):
    path = tmp_path / "routing.toml"
    path.write_text("version=1\nsurprise=true\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown routing keys"):
        RoutingConfig.load(path)
