from base64 import urlsafe_b64encode
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.connections.models import (
    CredentialUnreadable,
    EvaluationEvidence,
    MissingCredential,
    ProfileCreate,
    ProfileUpdate,
)
from app.connections.repository import ConnectionProfiles
from app.generation.decisions import (
    DecisionConflict,
    DecisionInput,
    DecisionNotFound,
    DecisionService,
    DecisionUnavailable,
)
from app.llm.base import Usage
from app.llm.decision import DecisionResult

SUITE = "a" * 64


class FakeDecisionProvider:
    def __init__(self, *, callback=None, result=None):
        self.calls = 0
        self.callback = callback
        self.result = result or DecisionResult(
            predicate=True, probability=0.9, confidence=0.8,
            usage=Usage(12, 4, source="provider"),
        )

    def decide(self, request):
        self.calls += 1
        if self.callback:
            self.callback()
        return self.result


class Runtime:
    def __init__(self, provider):
        self.provider = provider

    def decision_provider(self, profile, **_kwargs):
        return self.provider


def _service(db, *, provider=None, runtime=None, scopes=None):
    encryption_key = urlsafe_b64encode(b"x" * 32).decode("ascii")
    profiles = ConnectionProfiles(encryption_key)
    view = profiles.create(db, ProfileCreate(
        name="approved decision", provider="openai-decisions", model_id="decision-model",
        roles=frozenset({"decision"}), capabilities=frozenset({"decision"}),
        data_scopes=frozenset(scopes or {"user_supplied_data", "private_workspace"}),
        secret={"api_key": "not-a-real-key"},
    ))
    profiles.record_evaluation(db, EvaluationEvidence(
        evaluation_id=uuid4(), profile_id=view.profile_id, profile_revision=view.revision,
        provider=view.provider, model_id=view.model_id, suite_hash=SUITE,
        status="passed", metrics={"case_count": 3},
    ))
    db.commit()
    selected_provider = provider or FakeDecisionProvider()
    runtime = runtime or Runtime(selected_provider)
    settings = SimpleNamespace(
        routing_enabled=True, routing_eval_suite_hash=SUITE,
        profile_encryption_key=encryption_key, llm_provider="hf-local",
        llm_model_id="static-model", llm_sampling_policy="greedy",
        llm_reasoning_effort=None, operation_timeout_seconds=120,
        operation_token_budget=16_000, llm_max_output_tokens=1024,
        llm_timeout_seconds=60,
    )
    return DecisionService(settings=settings, profiles=profiles, runtime=runtime), selected_provider


def _body(decision_id=None, **changes):
    data = {"task": "Classify the request", "predicate": "Is eligible?", "input": {"x": "y"}}
    data.update(changes)
    return DecisionInput(decision_id or uuid4(), **data)


def test_decision_submission_audits_usage_and_replays_from_storage(db):
    service, provider = _service(db)
    workspace = "workspace-a"
    request = _body()

    first = service.submit(db, workspace, request, data_scope="private_workspace")
    assert first["state"] == "completed" and first["prediction"] is True
    assert first["usage"] == {"source": "provider", "promptTokens": 12, "completionTokens": 4}
    assert provider.calls == 1

    service.runtime = Runtime(SimpleNamespace(decide=lambda *_: pytest.fail("replay called provider")))
    assert service.submit(db, workspace, request, data_scope="private_workspace") == first
    assert service.get(db, workspace, request.decision_id) == first
    with pytest.raises(DecisionConflict):
        service.submit(db, workspace, replace(request, task="changed"), data_scope="private_workspace")
    with pytest.raises(DecisionNotFound):
        service.get(db, "workspace-b", request.decision_id)

    call = db.execute(
        "SELECT decision_id, operation_id, prompt_tokens, completion_tokens, usage_source "
        "FROM model_calls WHERE decision_id = %s", (request.decision_id,),
    ).fetchone()
    assert call["operation_id"] == request.decision_id
    assert (call["prompt_tokens"], call["completion_tokens"], call["usage_source"]) == (12, 4, "provider")
    assert db.execute("SELECT call_count, accounted_tokens FROM operations WHERE operation_id=%s",
                      (request.decision_id,)).fetchone()["call_count"] == 1


def test_abandon_during_provider_call_withholds_prediction_and_keeps_usage(db, migrated_dsn):
    from app.db.connection import connect

    service, provider = _service(db)
    request = _body()

    def abandon_from_another_connection():
        other = connect(migrated_dsn)
        try:
            assert service.abandon(other, "workspace-a", request.decision_id)["state"] == "abandoned"
        finally:
            other.close()

    provider.callback = abandon_from_another_connection
    result = service.submit(db, "workspace-a", request, data_scope="private_workspace")
    assert result["state"] == "abandoned" and result["prediction"] is None
    assert result["usage"]["promptTokens"] == 12
    audited = db.execute("SELECT count(*) AS n FROM model_calls WHERE decision_id=%s",
                         (request.decision_id,)).fetchone()
    assert audited["n"] == 1


def test_decision_requires_both_private_and_user_scopes(db):
    service, provider = _service(db, scopes={"private_workspace"})
    request = _body()
    from app.generation.decisions import DecisionUnavailable
    with pytest.raises(DecisionUnavailable):
        service.submit(db, "workspace-a", request, data_scope="private_workspace")
    assert provider.calls == 0


@pytest.mark.parametrize("error_type", [MissingCredential, CredentialUnreadable])
def test_unavailable_credentials_fail_before_decision_intent_or_provider_call(db, error_type):
    class UnavailableRuntime:
        def decision_provider(self, _profile, **_kwargs):
            raise error_type("private credential diagnostic")

    service, provider = _service(db, runtime=UnavailableRuntime())
    body = _body()
    with pytest.raises(DecisionUnavailable) as failure:
        service.submit(db, "workspace-a", body, data_scope="private_workspace")
    assert "private credential diagnostic" not in str(failure.value)
    assert provider.calls == 0
    assert db.execute("SELECT count(*) AS n FROM decisions").fetchone()["n"] == 0


def test_invalid_provider_output_retains_observed_usage(db):
    provider = FakeDecisionProvider(result=DecisionResult(
        predicate=None, probability=1.5, usage=Usage(23, 5, source="provider"),
    ))
    service, _ = _service(db, provider=provider)
    request = _body()
    result = service.submit(db, "workspace-a", request, data_scope="private_workspace")
    assert result["state"] == "invalid" and result["prediction"] is None
    assert result["usage"] == {"source": "provider", "promptTokens": 23, "completionTokens": 5}
    assert db.execute("SELECT accounted_tokens FROM operations WHERE operation_id=%s",
                      (request.decision_id,)).fetchone()["accounted_tokens"] == 28


def test_unknown_provider_outcome_keeps_token_reservation(db):
    class TimedOutProvider:
        def decide(self, _request):
            raise TimeoutError("hidden provider detail")

    from app.generation.decisions import DecisionUnavailable

    service, _ = _service(db, provider=TimedOutProvider())
    request = _body()
    with pytest.raises(DecisionUnavailable):
        service.submit(db, "workspace-a", request, data_scope="private_workspace")
    op = db.execute("SELECT reserved_tokens, accounted_tokens FROM operations WHERE operation_id=%s",
                    (request.decision_id,)).fetchone()
    assert op["reserved_tokens"] > 0 and op["accounted_tokens"] == 0
    row = service.get(db, "workspace-a", request.decision_id)
    assert row["state"] == "unknown" and row["usage"]["source"] == "unknown"
    with pytest.raises(DecisionConflict):
        service.submit(db, "workspace-a", request, data_scope="private_workspace")


def test_measured_over_budget_result_is_audited_but_withheld(db):
    provider = FakeDecisionProvider(result=DecisionResult(
        predicate=True, probability=0.9, confidence=0.8,
        usage=Usage(20_000, 1, source="provider"),
    ))
    service, _ = _service(db, provider=provider)
    request = _body()
    with pytest.raises(DecisionUnavailable):
        service.submit(db, "workspace-a", request, data_scope="private_workspace")
    row = service.get(db, "workspace-a", request.decision_id)
    assert row["state"] == "failed" and row["prediction"] is None
    assert row["usage"]["promptTokens"] == 20_000


def test_deadline_crossing_after_provider_return_withholds_prediction(db):
    import time

    provider = FakeDecisionProvider(callback=lambda: time.sleep(0.22))
    service, _ = _service(db, provider=provider)
    service.settings.operation_timeout_seconds = 0.1
    request = _body()
    with pytest.raises(DecisionUnavailable):
        service.submit(db, "workspace-a", request, data_scope="private_workspace")
    row = service.get(db, "workspace-a", request.decision_id)
    assert row["state"] == "failed" and row["prediction"] is None
    assert row["usage"]["promptTokens"] == 12


def test_profile_revision_change_withholds_result_but_keeps_usage(db, migrated_dsn):
    from app.db.connection import connect

    service, provider = _service(db)
    profile_id = db.execute("SELECT profile_id FROM connection_profiles").fetchone()["profile_id"]

    def rotate_profile_revision():
        other = connect(migrated_dsn)
        try:
            current = service.profiles.get(other, profile_id)
            service.profiles.update(other, profile_id, ProfileUpdate(
                expected_revision=current.revision, name=current.name,
                provider=current.provider, model_id=current.model_id,
                roles=current.roles, capabilities=current.capabilities,
                data_scopes=current.data_scopes, base_url=current.base_url,
                account_id=current.account_id,
                provider_options=current.provider_options, enabled=current.enabled,
                priority=current.priority,
            ))
            other.commit()
        finally:
            other.close()

    provider.callback = rotate_profile_revision
    request = _body()
    with pytest.raises(DecisionUnavailable):
        service.submit(db, "workspace-a", request, data_scope="private_workspace")
    result = service.get(db, "workspace-a", request.decision_id)
    assert result["state"] == "failed" and result["prediction"] is None
    assert result["usage"]["promptTokens"] == 12


def test_refusal_is_persisted_without_prediction(db):
    provider = FakeDecisionProvider(result=DecisionResult(
        refused=True, usage=Usage(7, 3, source="provider"),
    ))
    service, _ = _service(db, provider=provider)
    result = service.submit(db, "workspace-a", _body(), data_scope="private_workspace")
    assert result["state"] == "refused" and result["refused"] is True
    assert result["prediction"] is None


def test_native_invalid_probability_error_keeps_provider_usage():
    from types import SimpleNamespace

    from app.llm.decision import DecisionOutputError, DecisionRequest
    from app.llm.decision_adapters import OpenAIDecisionProvider

    response = {"answers": [{"name": "answer", "type": "predicate", "probability": 1.25}],
                "usage": {"input_tokens": 9, "output_tokens": 2}}
    client = SimpleNamespace(decisions=SimpleNamespace(create=lambda **_kwargs: response))
    with pytest.raises(DecisionOutputError) as exc:
        OpenAIDecisionProvider(client, model="configured-model").decide(
            DecisionRequest(task="classify", input="x", predicate="is true?"))
    assert exc.value.usage == Usage(9, 2, source="provider")


def test_http_decision_request_is_workspace_scoped_idempotent_and_bounded(db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import deps
    from app.api.routes.decisions import router
    from app.auth.dependencies import get_principal
    from app.auth.models import Principal

    service, provider = _service(db)
    app = FastAPI()
    app.state.settings = service.settings
    app.state.connection_profiles = service.profiles
    app.state.connection_runtime = service.runtime
    app.include_router(router)
    app.dependency_overrides[deps.get_conn] = lambda: db
    current = {"principal": Principal("workspace-a", "issuer", "subject")}
    app.dependency_overrides[get_principal] = lambda: current["principal"]
    client = TestClient(app)
    decision_id = str(uuid4())
    body = {"decisionId": decision_id, "task": "Classify", "predicate": "is valid?", "input": {"x": 1}}

    first = client.post("/api/decisions", json=body)
    assert first.status_code == 200 and first.json()["prediction"] is True
    assert client.get(f"/api/decisions/{decision_id}").json() == first.json()
    assert client.post("/api/decisions", json=body).json() == first.json()
    assert provider.calls == 1

    current["principal"] = Principal("workspace-b", "issuer", "other")
    assert client.get(f"/api/decisions/{decision_id}").status_code == 404
    current["principal"] = Principal("workspace-a", "issuer", "subject")
    too_large = dict(body, decisionId=str(uuid4()), input="x" * 12_001)
    assert client.post("/api/decisions", json=too_large).status_code == 422
    assert provider.calls == 1
