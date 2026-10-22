"""Typed discrete-decision seam and provider-backed structured fallback."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from app.llm.base import InvalidRequestError, Usage

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


def _confidence(data: Mapping[str, Any], usage: Usage | None) -> float:
    value = data.get("confidence")
    if (isinstance(value, bool) or not isinstance(value, (float, int))
            or not math.isfinite(value) or not 0 <= value <= 1):
        raise DecisionOutputError("decision provider returned an invalid confidence",
                                  provider="fallback", usage=usage)
    return float(value)
