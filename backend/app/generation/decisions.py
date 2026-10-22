"""Durable, workspace-scoped binary decisions over evaluated native providers."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import isfinite
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from app.connections.models import ConnectionUnavailable, CredentialUnreadable, MissingCredential
from app.connections.selection import StaleProfileSelection, resolve
from app.db import queries
from app.llm.base import InvalidRequestError, Usage, observed_usage
from app.llm.decision import DecisionRequest
from app.operations.budget import (
    BudgetExceeded,
    OperationBudgetController,
    OperationLimits,
    fence_violation,
)
from app.pricing import PricingService
from app.util.digest import json_digest


class DecisionUnavailable(RuntimeError):
    pass


class DecisionConflict(RuntimeError):
    pass


class DecisionNotFound(RuntimeError):
    pass


@dataclass(frozen=True)
class DecisionInput:
    decision_id: UUID
    task: str
    predicate: str
    input: str | dict

    def canonical(self) -> dict:
        return {"task": self.task, "predicate": self.predicate, "input": self.input}


def public_decision(row) -> dict:
    return {"decisionId": str(row["decision_id"]), "state": row["state"],
            "prediction": row["prediction"], "probability": row["probability"],
            "confidence": row["confidence"], "refused": row["refused"],
            "usage": {"source": row["usage_source"], "promptTokens": row["prompt_tokens"],
                      "completionTokens": row["completion_tokens"]}}


def _safe_error_state(exc: Exception) -> tuple[str, str]:
    name = type(exc).__name__.lower()
    if "timeout" in name or isinstance(exc, TimeoutError):
        return "unknown", "provider_timeout"
    if isinstance(exc, (ValueError, TypeError, InvalidRequestError)) or "invalidrequest" in name:
        return "invalid", "invalid_decision_output"
    return "failed", "provider_error"


def _validate_request(body: DecisionInput) -> dict:
    if not isinstance(body.task, str) or not 1 <= len(body.task.strip()) <= 1000:
        raise ValueError("task must contain 1-1000 characters")
    if not isinstance(body.predicate, str) or not 1 <= len(body.predicate.strip()) <= 1000:
        raise ValueError("predicate must contain 1-1000 characters")
    if isinstance(body.input, str):
        if len(body.input.encode("utf-8")) > 12_000:
            raise ValueError("input exceeds the 12000-byte limit")
    elif isinstance(body.input, dict):
        encoded = json.dumps(body.input, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > 12_000:
            raise ValueError("input exceeds the 12000-byte limit")
    else:
        raise TypeError("input must be text or a JSON object")
    return body.canonical()


_TERMINAL_STATES = frozenset({"completed", "refused", "invalid", "failed", "abandoned"})


def _replay(existing, fingerprint: str) -> dict:
    """The stored answer for a repeated decision ID; pending or unknown outcomes conflict."""
    if existing["request_fingerprint"].strip() != fingerprint:
        raise DecisionConflict("decision ID was already used for different input")
    if existing["state"] in _TERMINAL_STATES:
        return public_decision(existing)
    raise DecisionConflict("decision is pending or has an unknown outcome")


def _complete(conn, workspace_key, decision_id, *, state, usage, code, prediction=None,
              probability=None, confidence=None):
    return conn.execute(queries.get("complete_decision"), {
        "decision_id": decision_id, "workspace_key": workspace_key,
        "state": state, "prediction": prediction, "probability": probability,
        "confidence": confidence, "refused": state == "refused",
        "usage_source": usage.source, "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens, "error_code": code,
    }).fetchone()


def _record_abandoned_usage(conn, workspace_key, decision_id, usage):
    """The caller abandoned the decision mid-call; keep the usage but not the answer."""
    return conn.execute(queries.get("update_abandoned_decision_usage"), {
        "decision_id": decision_id, "workspace_key": workspace_key,
        "usage_source": usage.source, "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
    }).fetchone()


@dataclass
class _ProviderCall:
    result: object | None = None
    error: Exception | None = None
    usage: Usage = field(default_factory=lambda: Usage(source="unknown"))


def _judge(call: _ProviderCall) -> tuple[str, str | None, bool | None]:
    """Classify a provider outcome as (state, error code, prediction)."""
    if call.error:
        state, code = _safe_error_state(call.error)
        return state, code, None
    result = call.result
    if result and result.refused:
        return "refused", "provider_refusal", None
    prediction = result.predicate
    numeric = (result.probability, result.confidence)
    if (not isinstance(prediction, bool)
            or any(value is not None and (isinstance(value, bool)
                   or not isinstance(value, (int, float)) or not isfinite(value)
                   or not 0 <= value <= 1) for value in numeric)):
        return "invalid", "invalid_decision_output", None
    return "completed", None, prediction


class DecisionService:
    def __init__(self, *, settings, profiles, runtime, budgets=None):
        self.settings = settings
        self.profiles = profiles
        self.runtime = runtime
        self.budgets = budgets or OperationBudgetController()

    def get(self, conn, workspace_key: str, decision_id: UUID) -> dict:
        row = conn.execute(queries.get("get_decision_for_workspace"), {
            "decision_id": decision_id, "workspace_key": workspace_key,
        }).fetchone()
        if row is None:
            raise DecisionNotFound()
        return public_decision(row)

    def abandon(self, conn, workspace_key: str, decision_id: UUID) -> dict:
        with conn.transaction():
            row = conn.execute(queries.get("abandon_decision"), {
                "decision_id": decision_id, "workspace_key": workspace_key,
            }).fetchone()
            if row is None:
                row = conn.execute(queries.get("get_decision_for_workspace"), {
                    "decision_id": decision_id, "workspace_key": workspace_key,
                }).fetchone()
                if row is None:
                    raise DecisionNotFound()
        conn.commit()
        return public_decision(row)

    def submit(self, conn, workspace_key: str, body: DecisionInput, *, data_scope: str) -> dict:
        request_data = _validate_request(body)
        fingerprint = json_digest(request_data)
        existing = conn.execute(queries.get("get_decision_for_workspace"), {
            "decision_id": body.decision_id, "workspace_key": workspace_key,
        }).fetchone()
        if existing is not None:
            return _replay(existing, fingerprint)
        owner = conn.execute(queries.get("get_decision_owner"), {
            "decision_id": body.decision_id,
        }).fetchone()
        if owner is not None:
            raise DecisionNotFound()
        conn.commit()
        selected = self._select_provider(conn, body, data_scope)
        output_cap = min(int(getattr(self.settings, "llm_max_output_tokens", 1024)), 1024)
        self.budgets.ensure(conn, body.decision_id, workspace_key, self._limits(output_cap))
        replayed = self._record_intent(conn, workspace_key, body, fingerprint, request_data, selected)
        if replayed is not None:
            return replayed

        reservation = self._reserve(conn, workspace_key, body, output_cap)
        self._assert_still_pending(conn, workspace_key, body, reservation)
        call = self._call_provider(conn, body, selected, reservation)
        try:
            state, code, prediction = _judge(call)
            response, state = self._publish(conn, workspace_key, body, selected, call, state, code, prediction)
            if state in {"failed", "unknown"}:
                raise DecisionUnavailable("Decision provider did not return a usable result.")
            return response
        except (DecisionUnavailable, BudgetExceeded, StaleProfileSelection) as exc:
            if isinstance(exc, StaleProfileSelection):
                state, code = "failed", "profile_changed"
            elif isinstance(exc, BudgetExceeded):
                state, code = "failed", "operation_deadline"
            else:
                raise
            self._seal_failed(conn, workspace_key, body, call.usage, state, code)
            raise DecisionUnavailable("Decision result was withheld by a safety check.") from None

    def _select_provider(self, conn, body: DecisionInput, data_scope: str):
        scopes = {"user_supplied_data"}
        if data_scope == "private_workspace":
            scopes.add("private_workspace")
        try:
            selected = resolve(
                self.settings, conn, query=body.task, job_kind="binary_decision",
                difficulty="binary", data_scope=data_scope,
                required_capabilities={"decision"}, required_data_scopes=scopes,
                profiles=self.profiles, runtime=self.runtime,
            )
        except (ConnectionUnavailable, CredentialUnreadable, MissingCredential) as exc:
            raise DecisionUnavailable("No evaluated decision connection is available.") from exc
        if selected is None or selected.role != "decision":
            raise DecisionUnavailable("Native decisions are not enabled.")
        return selected

    def _limits(self, output_cap: int) -> OperationLimits:
        return OperationLimits(
            active_budget_seconds=float(getattr(self.settings, "operation_timeout_seconds", 120)),
            max_calls=1,
            total_token_budget=int(getattr(self.settings, "operation_token_budget", 16_000)),
            per_call_output_cap=output_cap,
            per_call_timeout_seconds=float(getattr(self.settings, "llm_timeout_seconds", 60)),
        )

    @staticmethod
    def _record_intent(conn, workspace_key, body, fingerprint, request_data, selected) -> dict | None:
        """Commit the intent before any provider call; a lost race replays the winner's row."""
        with conn.transaction():
            inserted = conn.execute(queries.get("insert_decision_intent"), {
                "decision_id": body.decision_id, "workspace_key": workspace_key,
                "operation_id": body.decision_id, "profile_id": selected.profile_id,
                "profile_revision": selected.profile_revision,
                "provider": selected.profile.provider, "model_id": selected.profile.model_id,
                "request_fingerprint": fingerprint, "request_json": Jsonb(request_data),
            }).fetchone()
            if inserted is not None:
                return None
            existing = conn.execute(queries.get("get_decision_for_workspace_update"), {
                "decision_id": body.decision_id, "workspace_key": workspace_key,
            }).fetchone()
            if existing is None:
                raise DecisionNotFound()
            return _replay(existing, fingerprint)

    def _reserve(self, conn, workspace_key, body, output_cap):
        prompt = f"Task: {body.task}\nPredicate: {body.predicate}\nInput: {json.dumps(body.input, ensure_ascii=False)}"
        try:
            return self.budgets.reserve_before_call(
                conn, body.decision_id, prompt_reserved_tokens=len(prompt.encode("utf-8")) + 128,
                requested_output_tokens=min(256, output_cap),
            )
        except Exception as exc:
            with conn.transaction():
                _complete(conn, workspace_key, body.decision_id, state="failed",
                          usage=Usage(source="unknown"), code="operation_budget")
            if isinstance(exc, BudgetExceeded):
                raise DecisionUnavailable("Decision operation budget is unavailable.") from None
            raise

    def _assert_still_pending(self, conn, workspace_key, body, reservation) -> None:
        with conn.transaction():
            current = conn.execute(queries.get("get_decision_for_workspace_update"), {
                "decision_id": body.decision_id, "workspace_key": workspace_key,
            }).fetchone()
            if current is None:
                raise DecisionNotFound()
            abandoned = current["state"] != "pending"
        conn.commit()
        if abandoned:
            self.budgets.account_usage(conn, reservation, usage=None, outcome="cancelled")
            raise DecisionConflict("decision was abandoned before provider submission")

    def _call_provider(self, conn, body, selected, reservation) -> _ProviderCall:
        """Ask the provider once, then account the usage and audit the call."""
        call = _ProviderCall()
        started = time.monotonic()
        try:
            call.result = selected.provider.decide(DecisionRequest(
                task=body.task, predicate=body.predicate, input=body.input,
                timeout_seconds=reservation.timeout_seconds,
            ))
            call.usage = observed_usage(getattr(call.result, "usage", None))
        except Exception as exc:  # noqa: BLE001 - provider SDK errors are normalized at this boundary
            call.error = exc
            call.usage = observed_usage(getattr(exc, "usage", None))
        latency = int((time.monotonic() - started) * 1000)
        self.budgets.account_usage(conn, reservation, usage=call.usage,
                                   outcome="failed" if call.error else "completed")
        created_at = datetime.now(UTC)
        schedule_id, cost, cost_status = PricingService.compute_call_cost(
            conn,
            provider=selected.profile.provider,
            model_id=selected.profile.model_id,
            created_at=created_at,
            prompt_tokens=call.usage.prompt_tokens,
            completion_tokens=call.usage.completion_tokens,
            cached_prompt_tokens=call.usage.cached_prompt_tokens,
            usage_source=call.usage.source,
        )
        with conn.transaction():
            conn.execute(queries.get("insert_decision_model_call"), {
                "model_call_id": uuid4(), "decision_id": body.decision_id,
                "provider": selected.profile.provider, "model_id": selected.profile.model_id,
                "status": "failed" if call.error else "completed", "request_id": None,
                "prompt_tokens": call.usage.prompt_tokens,
                "completion_tokens": call.usage.completion_tokens,
                "usage_source": call.usage.source, "latency_ms": latency,
                "error_type": type(call.error).__name__ if call.error else None,
                "operation_id": body.decision_id,
                "sampling_policy": selected.settings.llm_sampling_policy,
                "reasoning_effort": selected.settings.llm_reasoning_effort,
                "requested_timeout_seconds": reservation.timeout_seconds,
                "profile_id": selected.profile_id, "profile_revision": selected.profile_revision,
                "cached_prompt_tokens": call.usage.cached_prompt_tokens,
                "schedule_id": schedule_id,
                "estimated_cost": cost,
                "cost_status": cost_status,
            })
        return call

    @staticmethod
    def _publish(conn, workspace_key, body, selected, call, state, code, prediction):
        """Store the judged outcome under the profile and operation fences.

        Returns the public decision and the state that was finally stored.
        """
        result, usage = call.result, call.usage
        with conn.transaction():
            selected.assert_current(conn, lock=True)
            locked = conn.execute(queries.get("get_decision_for_workspace_update"), {
                "decision_id": body.decision_id, "workspace_key": workspace_key,
            }).fetchone()
            if locked["state"] == "abandoned":
                row = _record_abandoned_usage(conn, workspace_key, body.decision_id, usage) or locked
            else:
                fence = conn.execute(queries.get("lock_decision_operation_fence"), {
                    "operation_id": body.decision_id,
                }).fetchone()
                if fence_violation(fence, workspace_key) is not None and state not in {"invalid", "refused"}:
                    state, code, prediction = "failed", "operation_deadline", None
                row = _complete(
                    conn, workspace_key, body.decision_id, state=state, usage=usage, code=code,
                    prediction=prediction,
                    probability=result.probability if state == "completed" else None,
                    confidence=result.confidence if state == "completed" else None,
                ) or locked
                # Recheck after the state write; withhold any candidate if the
                # deadline or measured token ceiling crossed during publication.
                final_fence = conn.execute(queries.get("lock_decision_operation_fence"), {
                    "operation_id": body.decision_id,
                }).fetchone()
                violation = fence_violation(final_fence)
                if violation in {"deadline_exceeded", "missing"}:
                    withheld = "operation_deadline"
                elif violation == "token_budget":
                    withheld = "operation_token_budget"
                else:
                    withheld = None
                if withheld:
                    row = conn.execute(queries.get("withhold_decision_result"), {
                        "decision_id": body.decision_id, "workspace_key": workspace_key,
                        "error_code": withheld,
                    }).fetchone() or row
                if row["state"] == "failed":
                    state = "failed"
        conn.commit()
        return public_decision(row), state

    @staticmethod
    def _seal_failed(conn, workspace_key, body, usage, state, code) -> None:
        """A safety check withheld the result: record the failure unless the caller abandoned first."""
        with conn.transaction():
            current = conn.execute(queries.get("get_decision_for_workspace_update"), {
                "decision_id": body.decision_id, "workspace_key": workspace_key,
            }).fetchone()
            if current and current["state"] == "abandoned":
                _record_abandoned_usage(conn, workspace_key, body.decision_id, usage)
            elif current:
                _complete(conn, workspace_key, body.decision_id, state=state, usage=usage, code=code)
        conn.commit()
