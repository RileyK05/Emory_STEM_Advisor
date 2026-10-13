"""Typed discrete-decision seam and provider-backed structured fallback."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from app.llm.base import GenerationRequest, InvalidRequestError, Provider, Usage

DecisionKind = Literal["predicate", "choice", "score"]


class DecisionOutputError(InvalidRequestError):
    """Provider output failed validation; preserve usage for durable accounting."""

    def __init__(self, message: str, *, provider: str, usage: Usage | None):
        super().__init__(message, provider=provider)
        self.usage = usage or Usage(source="unknown")


@dataclass(frozen=True)
class DecisionLevel:
    label: str
    description: str = ""


@dataclass(frozen=True)
class DecisionRequest:
    task: str
    input: str | Mapping[str, Any]
    choices: Sequence[str | bool] = ()
    predicate: str | None = None
    choice_descriptions: Mapping[str | bool, str] | None = None
    levels: Sequence[DecisionLevel] = ()
    timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.timeout_seconds is not None and (
            not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0
        ):
            raise ValueError("decision timeout_seconds must be positive and finite")

    @property
    def kind(self) -> DecisionKind:
        if self.levels:
            return "score"
        if self.choices:
            return "choice"
        return "predicate"


@dataclass(frozen=True)
class DecisionResult:
    predicate: bool | None = None
    probability: float | None = None
    choice: str | bool | None = None
    score: float | None = None
    confidence: float | None = None
    probabilities: Mapping[str | bool | int, float] | None = None
    refused: bool = False
    usage: Usage | None = None


class DecisionProvider(Protocol):
    def decide(self, request: DecisionRequest) -> DecisionResult: ...


class StructuredDecisionFallback:
    """Fallback backed by a normal provider's constrained JSON generation."""

    def __init__(self, provider: Provider, *, sampling_policy: str = "greedy",
                 reasoning_effort: str | None = None):
        if sampling_policy not in {"greedy", "model_default"}:
            raise ValueError("sampling_policy must be greedy or model_default")
        if sampling_policy == "greedy" and reasoning_effort not in {None, "none"}:
            raise ValueError("reasoning effort requires explicit model_default sampling policy")
        self.provider = provider
        self.sampling_policy = sampling_policy
        self.reasoning_effort = reasoning_effort

    def decide(self, request: DecisionRequest) -> DecisionResult:
        if request.kind == "choice":
            options = list(request.choices)
            if all(isinstance(value, bool) for value in options):
                choice_schema = {"type": "boolean", "enum": options}
            elif all(isinstance(value, str) for value in options):
                choice_schema = {"type": "string", "enum": options}
            else:
                choice_schema = {"anyOf": [
                    {"type": "boolean", "enum": [value]} if isinstance(value, bool)
                    else {"type": "string", "enum": [value]} for value in options]}
            schema = {"type": "object", "properties": {
                "choice": choice_schema,
                "confidence": {"type": "number", "minimum": 0, "maximum": 1}},
                "required": ["choice", "confidence"], "additionalProperties": False}
        elif request.kind == "score":
            schema = {"type": "object", "properties": {
                "score": {"type": "number", "minimum": 0, "maximum": len(request.levels) - 1},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1}},
                "required": ["score", "confidence"], "additionalProperties": False}
        else:
            schema = {"type": "object", "properties": {
                "predicate": {"type": "boolean"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1}},
                "required": ["predicate", "confidence"], "additionalProperties": False}
        generation_request = GenerationRequest(
            messages=[{"role": "system", "content": "Return only the requested classification."},
                      {"role": "user", "content": f"Task: {request.task}\nInput: {request.input}\nPredicate: {request.predicate or ''}"}],
            response_schema=schema, max_output_tokens=128,
            timeout_seconds=request.timeout_seconds,
            temperature=0.0 if self.sampling_policy == "greedy" else None,
            reasoning_effort=self.reasoning_effort)
        result = self.provider.generate(generation_request)
        if result.finish_reason == "refusal":
            return DecisionResult(refused=True, usage=result.usage)
        if result.finish_reason in {"length", "max_tokens", "content_filter", "tool_calls", "error"}:
            raise DecisionOutputError("decision provider returned an incomplete response",
                                      provider="fallback", usage=result.usage)
        try:
            parsed = json.loads(result.text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise DecisionOutputError("decision provider returned invalid structured output",
                                      provider="fallback", usage=result.usage) from exc
        if not isinstance(parsed, Mapping):
            raise DecisionOutputError("decision provider returned invalid structured output",
                                      provider="fallback", usage=result.usage)
        if request.kind == "choice":
            if parsed.get("choice") not in request.choices:
                raise DecisionOutputError("decision provider returned an unsupported choice",
                                          provider="fallback", usage=result.usage)
            return DecisionResult(choice=parsed["choice"], confidence=_confidence(parsed, result.usage), usage=result.usage)
        if request.kind == "score":
            score = parsed.get("score")
            if (isinstance(score, bool) or not isinstance(score, (int, float))
                    or not math.isfinite(score) or not 0 <= score <= len(request.levels) - 1):
                raise DecisionOutputError("decision provider returned an invalid score",
                                          provider="fallback", usage=result.usage)
            return DecisionResult(score=float(score), confidence=_confidence(parsed, result.usage), usage=result.usage)
        if not isinstance(parsed.get("predicate"), bool):
            raise DecisionOutputError("decision provider returned no boolean predicate",
                                      provider="fallback", usage=result.usage)
        confidence = _confidence(parsed, result.usage)
        return DecisionResult(predicate=parsed["predicate"], probability=None,
                              confidence=confidence, usage=result.usage)


def _confidence(data: Mapping[str, Any], usage: Usage | None) -> float:
    value = data.get("confidence")
    if (isinstance(value, bool) or not isinstance(value, (float, int))
            or not math.isfinite(value) or not 0 <= value <= 1):
        raise DecisionOutputError("decision provider returned an invalid confidence",
                                  provider="fallback", usage=usage)
    return float(value)
