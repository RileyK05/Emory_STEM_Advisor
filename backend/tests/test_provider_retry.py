"""A rate-limited or transient provider call is retried inside the budget.

A provider blip used to surface as a 503 that blamed the provider settings
("Research model connection failed; check provider settings"), ending a
two-minute request with no attempt to recover. Both generation paths now share
one helper: retry inside the operation's remaining calls and seconds, honour the
provider's own retry hint, and let anything else through untouched.

These tests pin the fence: what is retried, what is not, how long the wait may
be, and that the operation's budget decides whether a retry is allowed.
"""

from __future__ import annotations

import time

import pytest

from app.generation.controller import (
    _MAX_RETRY_DELAY_SECONDS,
    retry_transient,
)
from app.llm.base import (
    AuthenticationError,
    InvalidRequestError,
    ProviderTimeoutError,
    RateLimitError,
    TransientProviderError,
)


class _Clock:
    """Records the sleeps a retry asks for, without waiting."""

    def __init__(self):
        self.slept: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def cancel(self) -> None:
        return None


class _TimeProxy:
    """The `time` module with only its `sleep` replaced.

    Patching `time.sleep` globally would also capture the sleeps of any other
    thread still running in the session, and the retry's own record would
    carry them.
    """

    def __init__(self, sleep):
        self.sleep = sleep

    def __getattr__(self, name):
        return getattr(time, name)


@pytest.fixture()
def clock(monkeypatch):
    fake = _Clock()
    monkeypatch.setattr("app.generation.controller.time", _TimeProxy(fake.sleep))
    return fake


def _transient(message: str = "connection reset") -> TransientProviderError:
    return TransientProviderError(message, provider="test")


def _rate_limited(retry_after: float | None = None) -> RateLimitError:
    error = RateLimitError("slow down", provider="test")
    if retry_after is not None:
        error.retry_after = retry_after
    return error


def test_a_transient_failure_is_retried_and_can_succeed(clock):
    attempts = []

    def call():
        attempts.append(1)
        if len(attempts) == 1:
            raise _transient()
        return "answered"

    result = retry_transient(call, remaining_calls=4, remaining_seconds=60,
                             check_cancelled=clock.cancel)
    assert result == "answered"
    assert len(attempts) == 2


def test_a_rate_limit_is_retried(clock):
    attempts = []

    def call():
        attempts.append(1)
        if len(attempts) == 1:
            raise _rate_limited()
        return "answered"

    assert retry_transient(call, remaining_calls=4, remaining_seconds=60,
                            check_cancelled=clock.cancel) == "answered"


def test_the_providers_retry_hint_is_honoured(clock):
    def call():
        raise _rate_limited(retry_after=7.5)

    with pytest.raises(RateLimitError):
        retry_transient(call, remaining_calls=4, remaining_seconds=60,
                        check_cancelled=clock.cancel)
    assert clock.slept == [7.5]


def test_the_wait_is_capped(clock):
    def call():
        raise _rate_limited(retry_after=9999)

    with pytest.raises(RateLimitError):
        retry_transient(call, remaining_calls=4, remaining_seconds=60,
                        check_cancelled=clock.cancel)
    assert clock.slept == [_MAX_RETRY_DELAY_SECONDS]


def test_a_second_failure_is_not_retried_again(clock):
    attempts = []

    def call():
        attempts.append(1)
        raise _transient()

    with pytest.raises(TransientProviderError):
        retry_transient(call, remaining_calls=8, remaining_seconds=60,
                        check_cancelled=clock.cancel)
    assert len(attempts) == 2, "the default allows exactly one retry"


def test_no_retry_when_the_call_budget_is_gone(clock):
    attempts = []

    def call():
        attempts.append(1)
        raise _transient()

    with pytest.raises(TransientProviderError):
        retry_transient(call, remaining_calls=1, remaining_seconds=60,
                        check_cancelled=clock.cancel)
    assert len(attempts) == 1, "a retry with no remaining call must not be made"
    assert clock.slept == []


def test_no_retry_when_the_time_budget_cannot_cover_the_wait(clock):
    attempts = []

    def call():
        attempts.append(1)
        raise _transient()

    with pytest.raises(TransientProviderError):
        retry_transient(call, remaining_calls=8, remaining_seconds=0.5,
                        check_cancelled=clock.cancel)
    assert len(attempts) == 1
    assert clock.slept == []


def test_a_timeout_is_transient_and_is_retried(clock):
    attempts = []

    def call():
        attempts.append(1)
        if len(attempts) == 1:
            raise ProviderTimeoutError("timed out", provider="test")
        return "answered"

    assert retry_transient(call, remaining_calls=4, remaining_seconds=60,
                            check_cancelled=clock.cancel) == "answered"


@pytest.mark.parametrize("error", [
    AuthenticationError("bad key", provider="test"),
    InvalidRequestError("bad request", provider="test"),
])
def test_other_provider_errors_are_not_retried(clock, error):
    attempts = []

    def call():
        attempts.append(1)
        raise error

    with pytest.raises(type(error)):
        retry_transient(call, remaining_calls=8, remaining_seconds=60,
                        check_cancelled=clock.cancel)
    assert len(attempts) == 1
    assert clock.slept == []


def test_cancellation_is_checked_before_waiting(clock):
    """A cancelled turn must not sleep its way into another provider call."""
    checks = []

    def check():
        checks.append(1)
        raise RuntimeError("cancelled")

    def call():
        raise _transient()

    with pytest.raises(RuntimeError, match="cancelled"):
        retry_transient(call, remaining_calls=4, remaining_seconds=60,
                        check_cancelled=check)
    assert checks == [1], "cancellation is checked before each wait"
    assert clock.slept == [], "a cancelled turn must not wait before failing"


def test_a_bigger_retry_allowance_is_honoured(clock):
    attempts = []

    def call():
        attempts.append(1)
        if len(attempts) < 3:
            raise _transient()
        return "answered"

    assert retry_transient(call, remaining_calls=8, remaining_seconds=60,
                            check_cancelled=clock.cancel, max_retries=3) == "answered"
    assert len(attempts) == 3
    assert len(clock.slept) == 2


def test_the_default_wait_is_short(clock):
    def call():
        raise _transient()

    with pytest.raises(TransientProviderError):
        retry_transient(call, remaining_calls=4, remaining_seconds=60,
                        check_cancelled=clock.cancel)
    assert clock.slept == [1.0]
