"""Offline answer-disposition evaluation over grounded generation outcomes.

A small, model-free harness: it runs `answer_from_context` for each case with a
caller-supplied provider and scores the coarse disposition (answered / refused /
withheld) against the case's expected label. It never touches the network or a
database, so it runs in the fast test suite. It evaluates disposition only; it
does not establish that a cited passage entails a claim.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.generation.answer import (
    GeneratedAnswer,
    GenerationRejected,
    answer_from_context,
)
from app.llm.base import Provider
from app.retrieval.types import CitedChunk

ANSWERED = "answered"
REFUSED = "refused"
WITHHELD = "withheld"
ERROR = "error"


@dataclass(frozen=True)
class AnswerCase:
    id: str
    query: str
    chunks: tuple[CitedChunk, ...]
    expected: str


@dataclass
class AnswerEvalSummary:
    total: int = 0
    correct: int = 0
    outcomes: dict[str, str] = field(default_factory=dict)
    mismatches: list[str] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0


def classify_outcome(outcome: object) -> str:
    """Map a generation outcome to its coarse disposition.

    A returned `GeneratedAnswer` is `answered`. A `GenerationRejected` the
    query path may retry once is `withheld`; a clean, non-recoverable rejection
    (a provider refusal or a filtered/incomplete output) is `refused`.
    """
    if isinstance(outcome, GeneratedAnswer):
        return ANSWERED
    if isinstance(outcome, GenerationRejected):
        return WITHHELD if outcome.recoverable_integrity else REFUSED
    raise TypeError("unrecognized generation outcome")


def run_answer_eval(
    cases: list[AnswerCase],
    *,
    provider_factory: Callable[[AnswerCase], Provider],
) -> AnswerEvalSummary:
    """Run each case once and score its disposition against `case.expected`."""
    summary = AnswerEvalSummary(total=len(cases))
    for case in cases:
        try:
            outcome = answer_from_context(
                list(case.chunks), case.query, provider=provider_factory(case)
            )
        except GenerationRejected as exc:
            result = classify_outcome(exc)
        except Exception:  # noqa: BLE001 - a provider/runtime error is not an answer
            result = ERROR
        else:
            result = classify_outcome(outcome)
        summary.outcomes[case.id] = result
        if result == case.expected:
            summary.correct += 1
        else:
            summary.mismatches.append(case.id)
    return summary
