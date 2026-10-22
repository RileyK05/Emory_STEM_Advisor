"""Explicit, bounded model-assessment seam for claims in a saved answer.

This module does not run from query handling. Callers must opt in with an
existing operation, workspace, and lazy evaluated-profile selector.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from app.db import queries
from app.llm.base import GenerationRequest, Usage, observed_usage
from app.operations.budget import (
    OperationBudgetController,
    estimate_prompt_reservation,
    fence_violation,
)
from app.pricing import PricingService
from app.util.digest import json_digest

REVIEW_VERSION = 1
MAX_ANSWER_BYTES = 40_000
MAX_CONTEXT_BYTES = 120_000
MAX_CONTEXT_ITEMS = 32
MAX_CLAIM_UNITS = 32
MAX_EVIDENCE_SPAN = 500
ASSESSMENTS = frozenset({
    "supported", "contradicted", "insufficient_evidence", "conflicting_evidence",
    "uncertain", "abstained",
})


class ClaimReviewError(RuntimeError):
    pass


class ClaimReviewNotFound(ClaimReviewError):
    pass


class ClaimReviewConflict(ClaimReviewError):
    pass


class ClaimReviewUnavailable(ClaimReviewError):
    pass


@dataclass(frozen=True)
class ReviewRequest:
    review_id: UUID
    response_id: UUID
    operation_id: UUID


def _insert_invalid_units(conn, review_id: UUID, answer: str) -> None:
    for unit in _invalid_units(answer):
        conn.execute(queries.get("insert_claim_review_unit_idempotent"), {
            "review_id": review_id,
            **{key: value for key, value in unit.items() if key != "_invalid"},
            "evidence_spans": Jsonb([]),
        })


def _context_map(value) -> dict[str, dict]:
    if not isinstance(value, list) or len(value) > MAX_CONTEXT_ITEMS:
        raise ClaimReviewUnavailable("The saved answer context is unavailable for review.")
    result = {}
    total = 0
    for item in value:
        if not isinstance(item, dict):
            raise ClaimReviewUnavailable("The saved answer context is unavailable for review.")
        try:
            chunk_id = str(UUID(str(item["chunk_id"])))
            source_id = str(UUID(str(item["source_id"])))
        except (KeyError, ValueError, TypeError):
            raise ClaimReviewUnavailable("The saved answer context is unavailable for review.") from None
        text = item.get("text")
        if not isinstance(text, str) or not text:
            raise ClaimReviewUnavailable("The saved answer context is unavailable for review.")
        total += len(text.encode("utf-8"))
        if total > MAX_CONTEXT_BYTES or chunk_id in result:
            raise ClaimReviewUnavailable("The saved answer context exceeds the review limit.")
        result[chunk_id] = {
            "chunk_id": chunk_id, "source_id": source_id,
            "source_name": str(item.get("source_name", ""))[:200],
            "locator_label": str(item.get("locator_label", ""))[:500],
            "text": text,
        }
    return result


def _schema() -> dict:
    evidence = {"type": "object", "properties": {
        "chunkId": {"type": "string"}, "start": {"type": "integer"},
        "end": {"type": "integer"}, "excerpt": {"type": "string"},
    }, "required": ["chunkId", "start", "end", "excerpt"], "additionalProperties": False}
    segment = {"type": "object", "properties": {
        "start": {"type": "integer"}, "end": {"type": "integer"},
        "text": {"type": "string"}, "kind": {"type": "string", "enum": ["claim", "unreviewed_gap"]},
        "assessment": {"type": "string", "enum": sorted(ASSESSMENTS | {"unreviewed"})},
        "confidence": {"type": ["number", "null"]},
        "evidence": {"type": "array", "items": evidence, "maxItems": 4},
    }, "required": ["start", "end", "text", "kind", "assessment", "confidence", "evidence"],
        "additionalProperties": False}
    return {"type": "object", "properties": {
        "segments": {"type": "array", "items": segment, "maxItems": MAX_CLAIM_UNITS},
    }, "required": ["segments"], "additionalProperties": False}


def _is_punctuation(char: str) -> bool:
    return unicodedata.category(char).startswith("P")


def _invalid_units(answer: str) -> list[dict]:
    if not answer:
        return []
    return [{"ordinal": 0, "answer_start": 0, "answer_end": len(answer),
             "answer_text": answer, "coverage_kind": "unreviewed_gap",
             "model_assessment": "unreviewed", "confidence": None,
             "evidence_spans": [], "_invalid": True}]


class _InvalidReview(ValueError):
    """The model's review output does not satisfy the contract."""


def _require(condition: bool) -> None:
    if not condition:
        raise _InvalidReview


def _substantive(char: str) -> bool:
    """Text that must be covered by a reviewed segment: not whitespace or punctuation."""
    return not char.isspace() and not _is_punctuation(char)


_COMPOUND_CLAIM = re.compile(r"\b(?:and|but|or|nor|yet|whereas|although)\b|;", re.IGNORECASE)


def _evidence_spans(spans: list, context: dict[str, dict]) -> list[dict]:
    evidence = []
    for span in spans:
        _require(isinstance(span, dict))
        chunk_id = span.get("chunkId")
        source = context.get(chunk_id) if isinstance(chunk_id, str) else None
        left, right, excerpt = span.get("start"), span.get("end"), span.get("excerpt")
        _require(
            source is not None
            and type(left) is int
            and type(right) is int
            and 0 <= left < right <= len(source["text"])
            and isinstance(excerpt, str)
            and right - left <= MAX_EVIDENCE_SPAN
            and excerpt == source["text"][left:right]
        )
        evidence.append({"chunk_id": chunk_id, "source_id": source["source_id"],
                         "start": left, "end": right, "excerpt": excerpt,
                         "source_name": source["source_name"],
                         "locator_label": source["locator_label"]})
    return evidence


def _segment_unit(answer: str, context: dict[str, dict], item, ordinal: int, previous_end: int) -> dict:
    """One validated review unit; segments must be ordered, exact, and gap-free apart from filler."""
    _require(isinstance(item, dict))
    start, end = item.get("start"), item.get("end")
    _require(
        type(start) is int
        and type(end) is int
        and previous_end <= start
        and 0 <= start < end <= len(answer)
        and item.get("text") == answer[start:end]
    )
    _require(not any(_substantive(char) for char in answer[previous_end:start]))
    kind, assessment = item.get("kind"), item.get("assessment")
    _require(kind in {"claim", "unreviewed_gap"})
    confidence = item.get("confidence")
    _require(confidence is None or (
        not isinstance(confidence, bool) and isinstance(confidence, (int, float)) and 0 <= confidence <= 1))
    spans = item.get("evidence")
    _require(isinstance(spans, list) and len(spans) <= 4)
    if kind == "unreviewed_gap":
        _require(assessment == "unreviewed" and not spans and confidence is None)
        model_assessment, evidence = "unreviewed", []
    else:
        _require(assessment in ASSESSMENTS)
        _require(not _COMPOUND_CLAIM.search(item["text"]))  # one proposition per claim unit
        evidence = _evidence_spans(spans, context)
        _require(assessment not in {"supported", "contradicted"} or bool(evidence))
        _require(assessment != "conflicting_evidence" or len({row["chunk_id"] for row in evidence}) >= 2)
        model_assessment = assessment
    return {"ordinal": ordinal, "answer_start": start, "answer_end": end,
            "answer_text": answer[start:end], "coverage_kind": kind,
            "model_assessment": model_assessment,
            "confidence": float(confidence) if confidence is not None else None,
            "evidence_spans": evidence}


def _validated_units(answer: str, context: dict[str, dict], payload) -> list[dict]:
    _require(isinstance(payload, dict) and set(payload) == {"segments"})
    raw = payload["segments"]
    _require(isinstance(raw, list) and len(raw) <= MAX_CLAIM_UNITS)
    units: list[dict] = []
    covered = [False] * len(answer)
    previous_end = 0
    for ordinal, item in enumerate(raw):
        unit = _segment_unit(answer, context, item, ordinal, previous_end)
        for position in range(unit["answer_start"], unit["answer_end"]):
            covered[position] = True
        previous_end = unit["answer_end"]
        units.append(unit)
    # Everything substantive in the answer must fall inside some reviewed segment.
    _require(not any(not is_covered and _substantive(char)
                     for is_covered, char in zip(covered, answer, strict=True)))
    _require(bool(units) or not answer.strip())
    return units


def validate_model_review(answer: str, context: dict[str, dict], payload) -> list[dict]:
    """Validate exact answer/evidence spans; invalid output becomes one safe gap."""
    try:
        return _validated_units(answer, context, payload)
    except (KeyError, TypeError, ValueError, OverflowError):  # includes _InvalidReview
        return _invalid_units(answer)


def _public_review(header, units) -> dict:
    return {
        "reviewId": str(header["review_id"]), "responseId": str(header["response_id"]),
        "state": header["state"], "reviewVersion": header["review_version"],
        "profileRevision": header["profile_revision"], "provider": header["provider"],
        "model": header["model_id"], "usage": {
            "source": header["usage_source"], "promptTokens": header["prompt_tokens"],
            "completionTokens": header["completion_tokens"],
        }, "units": [{
            "ordinal": row["ordinal"], "answerStart": row["answer_start"],
            "answerEnd": row["answer_end"], "text": row["answer_text"],
            "coverage": row["coverage_kind"],
            "modelAssessment": row["model_assessment"],
            "modelConfidence": row["confidence"], "evidence": row["evidence_spans"],
            "humanVerified": False,
        } for row in units],
    }


_STORED_STATES = frozenset({"completed", "refused", "invalid", "failed", "abandoned"})
_INCOMPLETE_FINISH = frozenset({"length", "max_tokens", "tool_calls", "content_filter", "error"})
_MAX_SNAPSHOT_BYTES = 1_000_000
_SYSTEM_PROMPT = (
    "Assess each atomic factual claim against only the supplied evidence. "
    "Return exact answer spans and exact evidence excerpts with character offsets. "
    "Do not repair quotes, invent offsets, or imply human verification. Use "
    "unreviewed_gap segments for anything you cannot atomize confidently."
)


def _unit_row(unit: dict) -> dict:
    return {key: value for key, value in unit.items() if key != "_invalid"}


def _insert_unreviewed_gap(conn, review_id: UUID, answer: str) -> None:
    """Cover the whole answer with one unreviewed unit."""
    conn.execute(queries.get("insert_unreviewed_claim_unit"), {
        "review_id": review_id, "answer_text": answer, "answer_end": len(answer),
    })


def _raise_if_operation_reviewed(conn, operation_id: UUID, workspace_key: str) -> None:
    """One review per operation; another workspace's review reads as not found."""
    existing = conn.execute(queries.get("get_claim_review_for_operation"), {
        "operation_id": operation_id,
    }).fetchone()
    if existing is not None:
        if existing["workspace_key"] != workspace_key:
            raise ClaimReviewNotFound()
        raise ClaimReviewConflict("this operation already has a claim review")


def _raise_if_rebound(existing, request_fingerprint: str) -> None:
    if existing["request_fingerprint"].strip() != request_fingerprint:
        raise ClaimReviewConflict("review ID is already bound to another request")


def _eligible(selection) -> bool:
    profile = getattr(selection, "profile", None)
    return (
        profile is not None and profile.enabled and profile.eval_passed
        and "structured_output" in profile.capabilities
        and {"user_supplied_data", "private_workspace"} <= profile.data_scopes
        and selection.role in {"standard", "complex"}
    )


@dataclass
class _ProviderCall:
    """What happened when the review request went to the provider."""

    request: GenerationRequest
    result: object | None = None
    error: Exception | None = None
    usage: Usage = field(default_factory=lambda: Usage(source="unknown"))
    latency_ms: int = 0
    started: bool = False


@dataclass(frozen=True)
class _Verdict:
    state: str
    failure_code: str | None
    units: list[dict]


class ClaimReviewService:
    def __init__(self, settings, *, budget_controller=None):
        self.settings = settings
        self.budgets = budget_controller or OperationBudgetController()

    def get(self, conn, workspace_key: str, review_id: UUID) -> dict:
        header = conn.execute(queries.get("get_claim_review"), {
            "review_id": review_id, "workspace_key": workspace_key,
        }).fetchone()
        if header is None:
            raise ClaimReviewNotFound()
        units = conn.execute(queries.get("list_claim_review_units"), {
            "review_id": review_id,
        }).fetchall()
        return _public_review(header, units)

    def abandon(self, conn, workspace_key: str, review_id: UUID) -> dict:
        with conn.transaction():
            header = conn.execute(queries.get("abandon_claim_review"), {
                "review_id": review_id, "workspace_key": workspace_key,
            }).fetchone()
            if header is None:
                header = conn.execute(queries.get("get_claim_review"), {
                    "review_id": review_id, "workspace_key": workspace_key,
                }).fetchone()
                if header is None:
                    raise ClaimReviewNotFound()
                if header["state"] in {"pending", "unknown"}:
                    raise ClaimReviewConflict("claim review state changed")
            if header["state"] == "abandoned":
                snapshot = header["input_snapshot"]
                _insert_invalid_units(conn, review_id, snapshot["answer"])
        conn.commit()
        return self.get(conn, workspace_key, review_id)

    def submit(self, conn, *, workspace_key: str, request: ReviewRequest,
               selection_factory):
        """Run one explicit structured review using an existing operation budget.

        `selection_factory(snapshot)` is lazy: stored replay and foreign-ID checks
        happen before any credentials are decrypted or client is constructed.
        """
        request_fingerprint = json_digest({
            "review_id": str(request.review_id), "response_id": str(request.response_id),
            "operation_id": str(request.operation_id), "version": REVIEW_VERSION,
        })
        stored = self._stored_review(conn, workspace_key, request, request_fingerprint)
        if stored is not None:
            return stored
        source = conn.execute(queries.get("load_claim_review_input"), {
            "response_id": request.response_id, "operation_id": request.operation_id,
            "workspace_key": workspace_key,
        }).fetchone()
        if source is None:
            raise ClaimReviewNotFound()
        answer, context, snapshot = self._review_input(source)
        operation = self.budgets.get(conn, request.operation_id)
        if (operation.workspace_key != workspace_key or not operation.remaining_calls
                or not operation.remaining_tokens):
            raise ClaimReviewUnavailable("The existing answer operation has no review budget remaining.")
        selection = selection_factory(snapshot)
        if not _eligible(selection):
            raise ClaimReviewUnavailable("No evaluated scoped structured-output profile is available.")
        self._record_intent(conn, workspace_key, request, request_fingerprint, selection, snapshot)

        usage = Usage(source="unknown")
        try:
            call = self._call_provider(conn, source["trace_id"], request, selection, answer, context,
                                       research_blocks=snapshot.get("research_blocks"))
            usage = call.usage
            verdict = self._judge(call, answer, context)
            try:
                self._publish(conn, workspace_key, request, selection, answer, call.usage, verdict)
            except Exception:  # noqa: BLE001 - preserve accounting but fail closed
                self._seal_unreviewed(conn, workspace_key, request, answer, call.usage)
        except Exception:
            # An unexpected failure after the intent committed must not leave the
            # review pending (and its operation permanently unreviewable).
            self._seal_unreviewed(conn, workspace_key, request, answer, usage)
            raise
        return self.get(conn, workspace_key, request.review_id)

    def _stored_review(self, conn, workspace_key, request, request_fingerprint):
        """The finished review for a replayed request, None for a fresh one.

        A review that is pending, unknown, or bound to a different request conflicts.
        """
        existing = conn.execute(queries.get("get_claim_review"), {
            "review_id": request.review_id, "workspace_key": workspace_key,
        }).fetchone()
        if existing is not None:
            _raise_if_rebound(existing, request_fingerprint)
            if existing["state"] in _STORED_STATES:
                return self.get(conn, workspace_key, request.review_id)
            raise ClaimReviewConflict("claim review is pending or has an unknown outcome")
        owner = conn.execute(queries.get("get_claim_review_owner"), {
            "review_id": request.review_id,
        }).fetchone()
        if owner is not None:
            raise ClaimReviewNotFound()
        _raise_if_operation_reviewed(conn, request.operation_id, workspace_key)
        conn.commit()
        return None

    @staticmethod
    def _review_input(source) -> tuple[str, dict[str, dict], dict]:
        answer = source["answer_text"]
        if not isinstance(answer, str) or not answer or len(answer.encode("utf-8")) > MAX_ANSWER_BYTES:
            raise ClaimReviewUnavailable("Saved answer exceeds the review limit.")
        context = _context_map(source["context_snapshot"])
        snapshot = {"answer": answer, "context": list(context.values())}
        if source.get("research_decision"):
            snapshot["research_blocks"] = source["research_decision"]["blocks"]
        if len(json.dumps(snapshot, ensure_ascii=False).encode("utf-8")) > _MAX_SNAPSHOT_BYTES:
            raise ClaimReviewUnavailable("Saved answer context exceeds the review limit.")
        return answer, context, snapshot

    @staticmethod
    def _record_intent(conn, workspace_key, request, request_fingerprint, selection, snapshot) -> None:
        profile = selection.profile
        binding_fingerprint = json_digest({
            "input": json_digest(snapshot), "profile_id": str(selection.profile_id),
            "profile_revision": selection.profile_revision, "provider": profile.provider,
            "model": profile.model_id, "sampling_policy": selection.settings.llm_sampling_policy,
            "reasoning_effort": selection.settings.llm_reasoning_effort,
            "version": REVIEW_VERSION,
        })
        with conn.transaction():
            inserted = conn.execute(queries.get("insert_claim_review_intent"), {
                "review_id": request.review_id, "response_id": request.response_id,
                "workspace_key": workspace_key, "operation_id": request.operation_id,
                "profile_id": selection.profile_id, "profile_revision": selection.profile_revision,
                "provider": profile.provider, "model_id": profile.model_id,
                "review_version": REVIEW_VERSION, "request_fingerprint": request_fingerprint,
                "binding_fingerprint": binding_fingerprint,
                "input_snapshot": Jsonb(snapshot),
            }).fetchone()
            if inserted is not None:
                return
            # Lost a race: report what the winner left behind.
            existing = conn.execute(queries.get("get_claim_review_for_update"), {
                "review_id": request.review_id, "workspace_key": workspace_key,
            }).fetchone()
            if existing is None:
                _raise_if_operation_reviewed(conn, request.operation_id, workspace_key)
                raise ClaimReviewNotFound()
            _raise_if_rebound(existing, request_fingerprint)
            raise ClaimReviewConflict("claim review is pending or has an unknown outcome")

    def _generation_request(self, selection, answer, context, *, research_blocks=None) -> GenerationRequest:
        payload = {"answer": answer, "evidence_context": list(context.values())}
        system = _SYSTEM_PROMPT
        if research_blocks is not None:
            payload["research_blocks"] = research_blocks
            system += (" Research blocks distinguish facts from advising judgments. Review factual "
                       "assertions inside judgments and the facts supporting their rationale. Treat "
                       "subjective preferences, suitability judgments, assumptions and questions as "
                       "unreviewed gaps; never label a recommendation verified or entailed merely "
                       "because its supporting catalog facts are accurate.")
        user_input = json.dumps(payload,
                                ensure_ascii=False, separators=(",", ":"))
        return GenerationRequest(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_input},
            ], response_schema=_schema(),
            max_output_tokens=min(int(self.settings.llm_max_output_tokens), 4096),
            temperature=0.0 if selection.settings.llm_sampling_policy == "greedy" else None,
            reasoning_effort=selection.settings.llm_reasoning_effort,
        )

    def _call_provider(self, conn, trace_id, request, selection, answer, context, *,
                       research_blocks=None) -> _ProviderCall:
        """Reserve budget, call the provider, then account and audit the outcome."""
        call = _ProviderCall(self._generation_request(selection, answer, context,
                                                      research_blocks=research_blocks))
        reservation = None
        started = time.monotonic()
        try:
            reservation = self.budgets.reserve_before_call(
                conn, request.operation_id,
                prompt_reserved_tokens=estimate_prompt_reservation(call.request),
                requested_output_tokens=call.request.max_output_tokens,
            )
            call.request = GenerationRequest(
                **{**call.request.__dict__, "max_output_tokens": reservation.output_grant,
                   "timeout_seconds": reservation.timeout_seconds}
            )
            selection.assert_current(conn, lock=False, commit=True)
            call.started = True
            call.result = selection.provider.generate(call.request)
            call.usage = observed_usage(getattr(call.result, "usage", None))
        except Exception as exc:  # noqa: BLE001 - provider and budget failures are sealed here
            call.error = exc
            call.usage = observed_usage(getattr(exc, "usage", None))
        call.latency_ms = int((time.monotonic() - started) * 1000)
        if reservation is not None:
            self.budgets.account_usage(
                conn, reservation, usage=call.usage,
                outcome="failed" if call.error else "completed",
            )
        if call.started:
            self._audit(conn, trace_id, request, selection, call)
        return call

    @staticmethod
    def _judge(call: _ProviderCall, answer: str, context: dict[str, dict]) -> _Verdict:
        """Turn a provider outcome into a review state and its units."""
        result = call.result
        if call.error:
            timed_out = "timeout" in type(call.error).__name__.lower()
            return _Verdict("unknown" if timed_out else "failed",
                            "provider_timeout" if timed_out else "provider_error",
                            _invalid_units(answer))
        if result.finish_reason == "refusal" or result.refusal:
            return _Verdict("refused", "provider_refusal", _invalid_units(answer))
        if result.finish_reason in _INCOMPLETE_FINISH:
            return _Verdict("invalid", "incomplete_review_output", _invalid_units(answer))
        try:
            parsed = json.loads(result.text)
        except (TypeError, json.JSONDecodeError):
            parsed = None
        units = validate_model_review(answer, context, parsed)
        if units and units[0].get("_invalid"):
            return _Verdict("invalid", "invalid_review_output", units)
        return _Verdict("completed", None, units)

    @staticmethod
    def _finish(conn, workspace_key, request, usage, state, failure_code):
        return conn.execute(queries.get("finish_claim_review"), {
            "review_id": request.review_id, "workspace_key": workspace_key,
            "state": state, "usage_source": usage.source,
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "failure_code": failure_code,
        }).fetchone()

    def _publish(self, conn, workspace_key, request, selection, answer, usage, verdict) -> None:
        """Store the verdict unless the review was abandoned or the operation fenced."""
        review_id = request.review_id
        with conn.transaction():
            selection.assert_current(conn, lock=True)
            header = conn.execute(queries.get("get_claim_review_for_update"), {
                "review_id": review_id, "workspace_key": workspace_key,
            }).fetchone()
            if header is None:
                raise ClaimReviewNotFound()
            if header["state"] == "abandoned":
                conn.execute(queries.get("update_abandoned_claim_review_usage"), {
                    "review_id": review_id, "workspace_key": workspace_key,
                    "usage_source": usage.source, "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                })
                _insert_invalid_units(conn, review_id, answer)
                return
            state, failure_code, units = verdict.state, verdict.failure_code, verdict.units
            fence = conn.execute(queries.get("lock_claim_review_operation"), {
                "operation_id": request.operation_id,
            }).fetchone()
            if fence_violation(fence, workspace_key) is not None:
                state, failure_code, units = "failed", "operation_fence", _invalid_units(answer)
            if self._finish(conn, workspace_key, request, usage, state, failure_code) is None:
                raise ClaimReviewConflict("claim review changed before publication")
            for unit in units:
                row = _unit_row(unit)
                conn.execute(queries.get("insert_claim_review_unit"), {
                    "review_id": review_id, **row,
                    "evidence_spans": Jsonb(row["evidence_spans"]),
                })
            final_fence = conn.execute(queries.get("lock_claim_review_operation"), {
                "operation_id": request.operation_id,
            }).fetchone()
            if fence_violation(final_fence) is not None:
                conn.execute(queries.get("withhold_claim_review"), {
                    "review_id": review_id, "workspace_key": workspace_key,
                    "failure_code": "operation_fence",
                })
                conn.execute(queries.get("delete_claim_review_units"), {"review_id": review_id})
                _insert_unreviewed_gap(conn, review_id, answer)

    def _seal_unreviewed(self, conn, workspace_key, request, answer, usage) -> None:
        """Publication failed: keep the accounting and mark the answer unreviewed."""
        with conn.transaction():
            header = conn.execute(queries.get("get_claim_review_for_update"), {
                "review_id": request.review_id, "workspace_key": workspace_key,
            }).fetchone()
            if header and header["state"] == "pending":
                self._finish(conn, workspace_key, request, usage, "failed", "publication_fence")
                _insert_unreviewed_gap(conn, request.review_id, answer)
        conn.commit()

    @staticmethod
    def _audit(conn, trace_id, request, selection, call: _ProviderCall):
        result, error, usage, generation_request = call.result, call.error, call.usage, call.request
        created_at = datetime.now(UTC)
        schedule_id, cost, cost_status = PricingService.compute_call_cost(
            conn,
            provider=selection.profile.provider,
            model_id=selection.profile.model_id,
            created_at=created_at,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            cached_prompt_tokens=usage.cached_prompt_tokens,
            usage_source=usage.source,
        )
        with conn.transaction():
            conn.execute(queries.get("insert_claim_review_model_call"), {
                "model_call_id": uuid4(), "trace_id": trace_id,
                "provider": selection.profile.provider, "model_id": selection.profile.model_id,
                "status": "failed" if error else "completed",
                "finish_reason": result.finish_reason if result else None,
                "request_id": result.request_id if result else getattr(error, "request_id", None),
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "usage_source": usage.source, "latency_ms": call.latency_ms,
                "error_type": type(error).__name__ if error else None,
                "operation_id": request.operation_id,
                "sampling_policy": selection.settings.llm_sampling_policy,
                "reasoning_effort": generation_request.reasoning_effort,
                "requested_output_tokens": generation_request.max_output_tokens,
                "requested_timeout_seconds": generation_request.timeout_seconds,
                "requested_temperature": generation_request.temperature,
                "profile_id": selection.profile_id,
                "profile_revision": selection.profile_revision,
                "claim_review_id": request.review_id,
                "cached_prompt_tokens": usage.cached_prompt_tokens,
                "schedule_id": schedule_id,
                "estimated_cost": cost,
                "cost_status": cost_status,
            })
