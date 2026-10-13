"""Native OpenAI Decisions and Cloudflare Clef adapters."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from typing import Any

from app.llm.adapters import _get, _retry_after, _usage_from_tokens
from app.llm.base import (
    AuthenticationError,
    InvalidRequestError,
    ProviderTimeoutError,
    RateLimitError,
    TransientProviderError,
    Usage,
)
from app.llm.decision import DecisionOutputError, DecisionRequest, DecisionResult


class OpenAIDecisionProvider:
    """Uses the official SDK's POST /v1/decisions resource."""

    def __init__(self, client: Any, model: str = "gpt-6-luna"):
        self.client, self.model = client, model

    def decide(self, request: DecisionRequest) -> DecisionResult:
        _validate_request(request)
        question: dict[str, Any] = {"name": "answer", "type": request.kind,
                                    "instructions": request.predicate or request.task}
        if request.kind == "choice":
            question["choices"] = [
                {"value": value, "description": (request.choice_descriptions or {}).get(value, str(value))}
                for value in request.choices]
        elif request.kind == "score":
            question["levels"] = [{"label": level.label, "description": level.description}
                                  for level in request.levels]
        provider_input = (request.input if isinstance(request.input, str) else
                          json.dumps(request.input, ensure_ascii=False, separators=(",", ":"),
                                     allow_nan=False))
        try:
            response = self.client.decisions.create(
                model=self.model, input=provider_input, questions=[question],
                **({"timeout": request.timeout_seconds}
                   if request.timeout_seconds is not None else {}))
        except Exception as exc:  # noqa: BLE001 - normalize SDK-specific errors
            raise _provider_error("openai", exc) from None
        answers = _get(response, "answers", ()) or ()
        usage = _usage_from_tokens(_get(response, "usage"))
        answer = next((item for item in answers if _get(item, "name") == "answer"), None)
        if answer is None:
            raise DecisionOutputError("decision response omitted the requested answer", provider="openai", usage=usage)
        kind = _get(answer, "type")
        if kind == "refusal":
            return DecisionResult(refused=True, usage=_usage_from_tokens(_get(response, "usage")))
        if kind != request.kind:
            raise DecisionOutputError("decision response type did not match request", provider="openai", usage=usage)
        if kind == "predicate":
            probability = _probability(_get(answer, "probability", _get(answer, "noul")), usage=usage, provider="openai")
            return DecisionResult(predicate=probability >= 0.5, probability=probability,
                                  usage=usage)
        if kind == "choice":
            choice = _get(answer, "choice")
            if choice not in request.choices:
                raise DecisionOutputError("decision returned a choice outside the allowed set", provider="openai", usage=usage)
            dist = _probabilities(_get(answer, "probabilities", ()), list(request.choices), usage=usage, provider="openai")
            return DecisionResult(choice=choice, confidence=_probability(_get(answer, "confidence"), usage=usage, provider="openai"),
                                  probabilities=dist, usage=usage)
        score = _number(_get(answer, "score"), usage=usage, provider="openai")
        if not 0 <= score <= len(request.levels) - 1:
            raise DecisionOutputError("decision score is outside its configured levels", provider="openai", usage=usage)
        dist = _probabilities(_get(answer, "probabilities", ()), list(range(len(request.levels))), usage=usage, provider="openai")
        return DecisionResult(score=score, confidence=_probability(_get(answer, "confidence"), usage=usage, provider="openai"),
                              probabilities=dist, usage=usage)


class CloudflareClefProvider:
    """Calls the documented Cloudflare Workers AI REST endpoint using an injected HTTP client."""

    def __init__(self, client: Any, *, account_id: str, api_token: str,
                 model: str = "clef", endpoint: str = "https://api.cloudflare.com/client/v4"):
        if not account_id or not re.fullmatch(r"[A-Za-z0-9_-]+", account_id):
            raise ValueError("invalid Cloudflare account id")
        if model not in {"clef", "clef-flash"}:
            raise ValueError("Cloudflare Clef model must be clef or clef-flash")
        self.client = client
        self._account_id = account_id
        self._api_token = api_token
        self.model = model
        self.endpoint = endpoint.rstrip("/")

    def decide(self, request: DecisionRequest) -> DecisionResult:
        question: dict[str, Any] = {"instructions": request.predicate or request.task}
        keymap: dict[str, str | bool | int] = {}
        if request.kind == "predicate":
            question["type"] = "noul"
        elif request.kind == "choice":
            question["type"] = "choice"
            criteria = {}
            for index, value in enumerate(request.choices):
                key = _choice_key(value)
                if key in criteria:
                    raise ValueError("Cloudflare choice values must have unique string forms")
                criteria[key] = (request.choice_descriptions or {}).get(value, str(value))
                keymap[key] = value
            question["criteria"] = criteria
        else:
            question["type"] = "score"
            question["criteria"] = [level.label for level in request.levels]
            keymap = {str(i): i for i in range(len(request.levels))}
        url = (f"{self.endpoint}/accounts/{self._account_id}/ai/run/"
               "@cf/cloudflare/clef")
        try:
            response = self.client.post(url, headers={"Authorization": f"Bearer {self._api_token}"},
                json={"model": self.model, "state": request.input, "questions": {"answer": question}},
                **({"timeout": request.timeout_seconds}
                   if request.timeout_seconds is not None else {}))
            response.raise_for_status()
            body = response.json()
        except Exception as exc:  # noqa: BLE001 - normalize HTTP client errors
            raise _provider_error("cloudflare", exc) from None
        body = _get(body, "result", body)
        usage = _usage_from_tokens(_get(body, "usage"))
        answer = _get(_get(body, "answers", {}), "answer")
        if answer is None:
            raise DecisionOutputError("Clef response omitted the requested answer", provider="cloudflare", usage=usage)
        if _get(answer, "type") == "refusal" or _get(body, "refused", False):
            return DecisionResult(refused=True, usage=usage)
        if request.kind == "predicate":
            probability = _probability(_get(answer, "probability", _get(answer, "noul")), usage=usage, provider="cloudflare")
            return DecisionResult(predicate=probability >= 0.5, probability=probability,
                                  confidence=probability, usage=usage)
        choice = _get(answer, "choice")
        if request.kind == "choice":
            if choice not in keymap:
                raise DecisionOutputError("Clef returned a choice outside the allowed set", provider="cloudflare", usage=usage)
            dist = _cf_probabilities(_get(answer, "probabilities"), keymap, usage=usage)
            return DecisionResult(choice=keymap[choice], confidence=_optional_probability(
                _get(answer, "confidence"), usage=usage, provider="cloudflare"), probabilities=dist, usage=usage)
        score = _number(_get(answer, "score"), usage=usage, provider="cloudflare")
        if not 0 <= score <= len(request.levels) - 1:
            raise DecisionOutputError("Clef score is outside its configured levels", provider="cloudflare", usage=usage)
        dist = _cf_probabilities(_get(answer, "probabilities"), keymap, usage=usage)
        return DecisionResult(score=score, confidence=_optional_probability(
            _get(answer, "confidence"), usage=usage, provider="cloudflare"), probabilities=dist, usage=usage)


def _validate_request(request: DecisionRequest) -> None:
    if request.kind == "choice" and not request.choices:
        raise ValueError("decision choice requests require at least one allowed option")
    if request.kind == "score" and len(request.levels) < 2:
        raise ValueError("decision score requests require at least two ordered levels")


def _choice_key(value: str | bool) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _number(value: Any, *, usage: Usage | None = None, provider: str = "decision") -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise DecisionOutputError("decision response contained a nonnumeric score", provider=provider, usage=usage)
    return float(value)


def _probability(value: Any, *, usage: Usage | None = None, provider: str = "decision") -> float:
    result = _number(value, usage=usage, provider=provider)
    if not 0 <= result <= 1:
        raise DecisionOutputError("decision probability is outside 0..1", provider=provider, usage=usage)
    return result


def _optional_probability(value: Any, *, usage: Usage | None = None,
                          provider: str = "decision") -> float | None:
    return None if value is None else _probability(value, usage=usage, provider=provider)


def _probabilities(items: Any, expected: list[Any], *, usage: Usage | None = None,
                   provider: str = "decision") -> Mapping[Any, float]:
    values = {}
    for item in items or ():
        key = _get(item, "value")
        if key not in expected or key in values:
            raise DecisionOutputError("decision response has invalid probability options", provider=provider, usage=usage)
        values[key] = _probability(_get(item, "probability"), usage=usage, provider=provider)
    if set(values) != set(expected) or abs(sum(values.values()) - 1) > 0.03:
        raise DecisionOutputError("decision probabilities are incomplete or unnormalized", provider=provider, usage=usage)
    return values


def _cf_probabilities(items: Any, keymap: Mapping[str, Any], *,
                      usage: Usage | None = None) -> Mapping[Any, float] | None:
    if items is None:
        return None
    if isinstance(items, Mapping):
        pairs = items.items()
    else:
        pairs = ((_get(item, "value"), _get(item, "probability")) for item in items)
    out = {}
    for key, probability in pairs:
        key = str(key)
        if key not in keymap or keymap[key] in out:
            raise DecisionOutputError("Clef response has invalid probability options", provider="cloudflare", usage=usage)
        out[keymap[key]] = _probability(probability, usage=usage, provider="cloudflare")
    if not out or abs(sum(out.values()) - 1) > 0.03:
        raise DecisionOutputError("Clef probabilities are incomplete or unnormalized",
                                  provider="cloudflare", usage=usage)
    return out


def _provider_error(provider: str, exc: Exception):
    if "timeout" in type(exc).__name__.lower() or isinstance(exc, TimeoutError):
        return ProviderTimeoutError(f"{provider} request timed out", provider=provider,
                                    request_id=getattr(exc, "request_id", None))
    response = getattr(exc, "response", None)
    status = (getattr(exc, "status_code", None) or getattr(exc, "status", None)
              or getattr(response, "status_code", None))
    reqid = getattr(exc, "request_id", None)
    if status in (401, 403):
        return AuthenticationError(f"{provider} credentials rejected", provider=provider, request_id=reqid)
    if status == 429:
        headers = getattr(response, "headers", {}) or {}
        return RateLimitError(f"{provider} rate limited the request", provider=provider,
                              retry_after=_retry_after(headers), request_id=reqid)
    if status in (408, 409, 425) or isinstance(status, int) and status >= 500:
        return TransientProviderError(f"{provider} temporarily unavailable", provider=provider,
                                      request_id=reqid)
    if isinstance(status, int) and 400 <= status < 500:
        return InvalidRequestError(f"{provider} rejected the decision request", provider=provider,
                                   request_id=reqid)
    return TransientProviderError(f"{provider} request failed", provider=provider, request_id=reqid)
