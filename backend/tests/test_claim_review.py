import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from app.config import load_settings
from app.connections.models import EvaluationEvidence, ProfileCreate, ProfileUpdate
from app.connections.repository import ConnectionProfiles
from app.db import queries
from app.generation.claim_review import (
    ClaimReviewConflict,
    ClaimReviewNotFound,
    ClaimReviewService,
    ReviewRequest,
)
from app.llm.base import GenerationResult, Usage
from tests.fakes import FakeEmbedder, FakeProvider
from tests.helpers import api_client, collection_name, ingest_text, lenient_config


class ReviewProvider:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        return GenerationResult(
            text=json.dumps(self.payload), finish_reason="stop",
            usage=Usage(120, 40, source="provider"), model="review-fake",
        )


def _evaluated_profile(db):
    store = ConnectionProfiles(Fernet.generate_key())
    view = store.create(db, ProfileCreate(
        name="claim-review-test", provider="openai", model_id="review-fake",
        roles=frozenset({"standard"}), capabilities=frozenset({"structured_output"}),
        data_scopes=frozenset({"user_supplied_data", "private_workspace"}),
        secret={"api_key": "test-only"},
    ))
    suite = "a" * 64
    store.record_evaluation(db, EvaluationEvidence(
        evaluation_id=uuid4(), profile_id=view.profile_id, profile_revision=view.revision,
        provider=view.provider, model_id=view.model_id, suite_hash=suite,
        status="passed", metrics={"fixture": 1},
    ))
    db.commit()
    profile = store.list_routes(db, suite_hash=suite)[0]
    return store, profile


def _selection(store, profile, settings, provider):
    def assert_current(conn, *, lock=True, commit=False):
        current = store.get_current_revision(conn, profile.profile_id, lock=lock)
        if commit:
            conn.commit()
        if current != (True, profile.revision):
            raise RuntimeError("stale fixture profile")
    return SimpleNamespace(provider=provider, profile=profile, profile_id=profile.profile_id,
                           profile_revision=profile.revision, role="standard", settings=settings,
                           assert_current=assert_current)


def _make_answer(db, collection, tmp_path):
    from fastapi.testclient import TestClient

    from app.api import create_app

    ingest_text(db, collection, tmp_path, "claim-review-source.txt",
                "The course requires MATH 111.")
    settings = load_settings()
    app = create_app(allow_test_client=True, settings=settings, config=lenient_config(),
                     embedder=FakeEmbedder(dim=8), provider=FakeProvider("The course requires MATH 111 [1]."))
    app.state.override_conn = db
    response = TestClient(app).post("/api/query", json={
        "query": "What does the course require?",
        "collectionId": collection_name(db, collection),
    })
    assert response.status_code == 200 and response.json()["type"] == "answer", response.text
    trace_id = response.json()["trace"]["traceId"]
    row = db.execute(queries.get("get_response_for_trace"), {"trace_id": trace_id}).fetchone()
    operation_id = db.execute(queries.get("get_operation_for_trace"), {"trace_id": trace_id}).fetchone()
    return settings, row, operation_id


def test_claim_review_replay_and_measured_invalid_output_are_durable(db, collection, tmp_path):
    settings, response_row, operation_row = _make_answer(db, collection, tmp_path)
    store, profile = _evaluated_profile(db)
    provider = ReviewProvider({"segments": []})
    selection = _selection(store, profile, settings, provider)
    review_id = uuid4()
    request = ReviewRequest(review_id, response_row["response_id"], operation_row["operation_id"])
    service = ClaimReviewService(settings)
    result = service.submit(db, workspace_key="development", request=request,
                            selection_factory=lambda _snapshot: selection)
    assert result["state"] == "invalid"
    assert result["usage"] == {"source": "provider", "promptTokens": 120,
                                "completionTokens": 40}
    assert result["units"][0]["coverage"] == "unreviewed_gap"
    assert result["units"][0]["humanVerified"] is False
    assert provider.calls == 1
    replay = service.submit(db, workspace_key="development", request=request,
                            selection_factory=lambda _snapshot: (_ for _ in ()).throw(AssertionError()))
    assert replay == result
    assert provider.calls == 1
    with pytest.raises(ClaimReviewNotFound):
        service.get(db, "another-workspace", review_id)
    with pytest.raises(ClaimReviewNotFound):
        service.submit(db, workspace_key="another-workspace", request=request,
                       selection_factory=lambda _snapshot: (_ for _ in ()).throw(AssertionError()))
    with pytest.raises(ClaimReviewConflict):
        service.submit(db, workspace_key="development",
                       request=ReviewRequest(uuid4(), response_row["response_id"],
                                             operation_row["operation_id"]),
                       selection_factory=lambda _snapshot: (_ for _ in ()).throw(AssertionError()))


def test_unknown_provider_outcome_keeps_intent_and_never_retries(db, collection, tmp_path):
    settings, response_row, operation_row = _make_answer(db, collection, tmp_path)
    store, profile = _evaluated_profile(db)

    class TimeoutProvider:
        calls = 0

        def generate(self, _request):
            self.calls += 1
            raise TimeoutError("transport timeout")

    provider = TimeoutProvider()
    selection = _selection(store, profile, settings, provider)
    request = ReviewRequest(uuid4(), response_row["response_id"], operation_row["operation_id"])
    service = ClaimReviewService(settings)
    result = service.submit(db, workspace_key="development", request=request,
                            selection_factory=lambda _snapshot: selection)
    assert result["state"] == "unknown"
    assert result["usage"]["source"] == "unknown"
    assert result["units"][0]["coverage"] == "unreviewed_gap"
    with pytest.raises(ClaimReviewConflict):
        service.submit(db, workspace_key="development", request=request,
                       selection_factory=lambda _snapshot: (_ for _ in ()).throw(AssertionError()))
    assert provider.calls == 1


def test_profile_stale_before_sdk_call_never_invokes_provider(db, collection, tmp_path):
    settings, response_row, operation_row = _make_answer(db, collection, tmp_path)
    store, profile = _evaluated_profile(db)
    provider = ReviewProvider({"segments": []})
    selection = _selection(store, profile, settings, provider)

    def stale(*_args, **_kwargs):
        raise RuntimeError("stale profile")

    selection.assert_current = stale
    request = ReviewRequest(uuid4(), response_row["response_id"], operation_row["operation_id"])
    result = ClaimReviewService(settings).submit(db, workspace_key="development", request=request,
        selection_factory=lambda _snapshot: selection)
    assert result["state"] == "failed"
    assert result["units"][0]["coverage"] == "unreviewed_gap"
    assert provider.calls == 0
    assert db.execute(queries.get("count_claim_review_audits"), {
        "review_id": request.review_id,
    }).fetchone()["count"] == 0


def test_abandon_is_idempotent_and_late_measured_result_only_updates_usage(db, collection, tmp_path):
    settings, response_row, operation_row = _make_answer(db, collection, tmp_path)
    store, profile = _evaluated_profile(db)
    service = ClaimReviewService(settings)
    request = ReviewRequest(uuid4(), response_row["response_id"], operation_row["operation_id"])
    answer = response_row["text"]
    context = response_row["context_snapshot"][0]
    provider = ReviewProvider({"segments": [{
        "start": 0, "end": len(answer), "text": answer, "kind": "claim",
        "assessment": "supported", "confidence": 0.9,
        "evidence": [{"chunkId": context["chunk_id"], "start": 0,
                      "end": len(context["text"]), "excerpt": context["text"]}],
    }]})

    def abandon_during_call(_request):
        service.abandon(db, "development", request.review_id)
        return ReviewProvider.generate(provider, _request)

    provider.generate = abandon_during_call
    result = service.submit(db, workspace_key="development", request=request,
                            selection_factory=lambda _snapshot: _selection(store, profile, settings, provider))
    first = service.abandon(db, "development", request.review_id)
    second = service.abandon(db, "development", request.review_id)
    assert result["state"] == "abandoned"
    assert result["usage"]["promptTokens"] == 120
    assert len(result["units"]) == len(first["units"]) == len(second["units"]) == 1
    assert result["units"][0]["coverage"] == "unreviewed_gap"
    assert provider.calls == 1


def test_profile_change_after_call_withholds_model_assessment_but_keeps_usage(db, collection, tmp_path):
    settings, response_row, operation_row = _make_answer(db, collection, tmp_path)
    store, profile = _evaluated_profile(db)
    answer = response_row["text"]
    context = response_row["context_snapshot"][0]
    provider = ReviewProvider({"segments": [{
        "start": 0, "end": len(answer), "text": answer, "kind": "claim",
        "assessment": "supported", "confidence": 0.9,
        "evidence": [{"chunkId": context["chunk_id"], "start": 0,
                      "end": len(context["text"]), "excerpt": context["text"]}],
    }]})

    def disable_profile():
        store.update(db, profile.profile_id, ProfileUpdate(
            expected_revision=profile.revision, name=profile.name, provider=profile.provider,
            model_id=profile.model_id, roles=profile.roles, capabilities=profile.capabilities,
            data_scopes=profile.data_scopes, base_url=profile.base_url,
            account_id=profile.account_id, provider_options=profile.provider_options,
            enabled=False,
        ))
        db.commit()

    provider.generate = lambda request: (
        disable_profile(), ReviewProvider.generate(provider, request)
    )[1]
    selection = _selection(store, profile, settings, provider)
    result = ClaimReviewService(settings).submit(db, workspace_key="development",
        request=ReviewRequest(uuid4(), response_row["response_id"], operation_row["operation_id"]),
        selection_factory=lambda _snapshot: selection)
    assert result["state"] == "failed"
    assert result["usage"]["promptTokens"] == 120
    assert result["units"][0]["coverage"] == "unreviewed_gap"


def test_claim_review_valid_model_assessment_is_not_promoted_to_fact(db, collection, tmp_path):
    settings, response_row, operation_row = _make_answer(db, collection, tmp_path)
    store, profile = _evaluated_profile(db)
    answer = response_row["text"]
    context = response_row["context_snapshot"][0]
    segment = {"start": 0, "end": len(answer), "text": answer, "kind": "claim",
               "assessment": "supported", "confidence": 0.9,
               "evidence": [{"chunkId": context["chunk_id"], "start": 0,
                             "end": len(context["text"]), "excerpt": context["text"]}]}
    provider = ReviewProvider({"segments": [segment]})
    selection = _selection(store, profile, settings, provider)
    result = ClaimReviewService(settings).submit(db, workspace_key="development",
        request=ReviewRequest(uuid4(), response_row["response_id"], operation_row["operation_id"]),
        selection_factory=lambda _snapshot: selection)
    assert result["state"] == "completed"
    assert result["units"][0]["modelAssessment"] == "supported"
    assert result["units"][0]["humanVerified"] is False


def test_claim_review_route_resolves_the_answer_and_fails_closed_without_a_profile(
        db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "a.txt", "alpha beta gamma")
    client = api_client(db, collection, provider=FakeProvider(text="The answer is alpha [1]."))
    answer = client.post("/api/query", json={
        "query": "alpha", "collectionId": collection_name(db, collection),
    })
    trace_id = answer.json()["trace"]["traceId"]
    missing = client.post("/api/claim-reviews", json={"reviewId": str(uuid4()),
                                                      "traceId": str(uuid4())})
    assert missing.status_code == 404
    # Routing is off by default, so no evaluated profile exists: refuse, write nothing.
    unavailable = client.post("/api/claim-reviews", json={"reviewId": str(uuid4()),
                                                          "traceId": trace_id})
    assert unavailable.status_code == 503, unavailable.text
    assert db.execute("SELECT count(*) AS n FROM claim_reviews").fetchone()["n"] == 0
    assert client.get(f"/api/claim-reviews/{uuid4()}").status_code == 404
