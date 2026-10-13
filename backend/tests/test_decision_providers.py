"""Native decision adapters (OpenAI decisions API, Cloudflare Clef): request and answer mapping."""

from types import SimpleNamespace

import pytest

from app.llm.base import AuthenticationError, InvalidRequestError, RateLimitError, Usage
from app.llm.decision import DecisionLevel, DecisionRequest
from app.llm.decision_adapters import CloudflareClefProvider, OpenAIDecisionProvider
from app.llm.factory import get_decision_provider


def test_openai_decisions_map_choice_probabilities_and_usage():
    class Decisions:
        def create(self, **kwargs):
            self.payload = kwargs
            return SimpleNamespace(answers=[{"type": "choice", "name": "answer", "choice": "yes",
                "confidence": 0.9, "probabilities": [
                    {"value": "yes", "probability": 0.9}, {"value": "no", "probability": 0.1}]}],
                model="gpt-6-luna", usage={"input_tokens": 10, "output_tokens": 0, "total_tokens": 10})

    sdk = Decisions()
    result = OpenAIDecisionProvider(SimpleNamespace(decisions=sdk)).decide(
        DecisionRequest("approve?", "evidence", choices=("yes", "no"),
                        choice_descriptions={"yes": "meets criteria", "no": "does not"}))
    assert result.choice == "yes" and result.confidence == 0.9
    assert result.probabilities == {"yes": 0.9, "no": 0.1}
    assert result.usage == Usage(10, 0, 10, "provider")
    assert sdk.payload["model"] == "gpt-6-luna"
    assert sdk.payload["questions"][0]["choices"][0]["description"] == "meets criteria"


def test_openai_decisions_score_rejects_unnormalized_probabilities():
    answer = {"type": "score", "name": "answer", "score": 1.25, "confidence": 0.8,
              "probabilities": [{"value": 0, "probability": 0.25},
                                {"value": 1, "probability": 0.25},
                                {"value": 2, "probability": 0.5}]}
    client = SimpleNamespace(decisions=SimpleNamespace(create=lambda **_kw:
        {"answers": [answer], "usage": {"input_tokens": 3, "output_tokens": 0}}))
    req = DecisionRequest("severity", "case", levels=(DecisionLevel("low"),
                             DecisionLevel("medium"), DecisionLevel("high")))
    assert OpenAIDecisionProvider(client).decide(req).score == 1.25
    answer["probabilities"][0]["probability"] = 0.9
    with pytest.raises(InvalidRequestError, match="unnormalized"):
        OpenAIDecisionProvider(client).decide(req)


def test_decision_factory_accepts_injected_native_client():
    fake = SimpleNamespace(decisions=SimpleNamespace(create=lambda **_kw: {"answers": [
        {"type": "predicate", "name": "answer", "probability": 0.2}]}))
    provider = get_decision_provider("openai", model="gpt-6-luna", client=fake)
    assert isinstance(provider, OpenAIDecisionProvider)
    assert provider.decide(DecisionRequest("condition", "input")).predicate is False


def test_provider_errors_are_typed_and_do_not_retain_sdk_messages():
    class Unauthorized(Exception):
        status_code = 401

    client = SimpleNamespace(decisions=SimpleNamespace(create=lambda **_kw:
        (_ for _ in ()).throw(Unauthorized("secret-bearing request body"))))
    with pytest.raises(AuthenticationError) as error:
        OpenAIDecisionProvider(client).decide(DecisionRequest("check", "private input"))
    assert "secret-bearing" not in str(error.value)
    assert "private input" not in str(error.value)

    class TooMany(Exception):
        status_code = 429
        response = SimpleNamespace(headers={"retry-after": "2.5"})

    client = SimpleNamespace(decisions=SimpleNamespace(create=lambda **_kw:
        (_ for _ in ()).throw(TooMany("opaque"))))
    with pytest.raises(RateLimitError) as error:
        OpenAIDecisionProvider(client).decide(DecisionRequest("check", "input"))
    assert error.value.retry_after == 2.5


class _Http:
    """Cloudflare transport double: returns one canned Clef answer and records the request."""

    def __init__(self, answer, result_wrapper=False):
        self.answer, self.result_wrapper = answer, result_wrapper

    def post(self, url, **kwargs):
        self.url, self.kwargs = url, kwargs
        body = {"answers": {"answer": self.answer}}
        if self.result_wrapper:
            body = {"result": {**body, "usage": {"input_tokens": 5, "output_tokens": 1}}}
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)


def _clef(answer, **kwargs):
    http = _Http(answer, **kwargs)
    return http, CloudflareClefProvider(http, account_id="acct-123", api_token="topsecret",
                                        model="clef-flash")


def test_cloudflare_clef_sends_documented_request_and_maps_noul():
    http, provider = _clef({"type": "noul", "noul": 0.8}, result_wrapper=True)
    result = provider.decide(DecisionRequest("relevant?", "evidence", predicate="is relevant"))
    assert result.predicate is True and result.probability == 0.8
    assert result.usage == Usage(5, 1, 6, "provider")
    assert http.url.endswith("/accounts/acct-123/ai/run/@cf/cloudflare/clef")
    assert http.kwargs["json"]["model"] == "clef-flash"
    assert http.kwargs["json"]["questions"]["answer"]["type"] == "noul"
    assert "topsecret" not in repr(provider)


_LEVELS = (DecisionLevel("low"), DecisionLevel("high"))


@pytest.mark.parametrize(("answer", "request_", "expected"), [
    ({"type": "choice", "choice": "a", "confidence": 0.7, "probabilities": {"a": 0.7, "b": 0.3}},
     DecisionRequest("pick", "input", choices=("a", "b")),
     {"choice": "a", "probabilities": {"a": 0.7, "b": 0.3}}),
    ({"type": "score", "score": 0.6, "confidence": 0.8, "probabilities": {"0": 0.4, "1": 0.6}},
     DecisionRequest("rate", "input", levels=_LEVELS),
     {"score": 0.6, "probabilities": {0: 0.4, 1: 0.6}}),
    # Boolean choices round-trip through canonical "true"/"false" keys.
    ({"type": "choice", "choice": "true", "confidence": 0.9,
      "probabilities": {"true": 0.9, "false": 0.1}},
     DecisionRequest("approve?", "state", choices=(True, False)),
     {"choice": True, "probabilities": {True: 0.9, False: 0.1}}),
], ids=["choice", "score", "boolean-choice"])
def test_cloudflare_clef_maps_answers(answer, request_, expected):
    _, provider = _clef(answer)
    result = provider.decide(request_)
    for field, value in expected.items():
        assert getattr(result, field) == value


def test_cloudflare_clef_rejects_a_choice_outside_the_request():
    _, provider = _clef({"type": "choice", "choice": "extra"})
    with pytest.raises(InvalidRequestError, match="outside"):
        provider.decide(DecisionRequest("pick", "input", choices=("a", "b")))
