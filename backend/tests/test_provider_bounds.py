"""A provider call is bounded in wall-clock time, not just per socket read.

An SDK timeout bounds one socket read. A service that keeps sending keep-alive
bytes holds the call open long past it: a call was observed running 907 s
against a 120 s timeout. Every SDK call and stream is therefore bounded by the
request's own timeout, and an abandoned call is abandoned, not waited for.

These tests pin the bound itself: that it fires, that it fires near the
timeout, that it does not fire early, that the SDK's own richer error wins when
it arrives first, and that an abandoned call cannot leak into a later answer.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from app.config import load_settings
from app.generation.controller import (
    _MAX_RETRY_DELAY_SECONDS,
    retry_transient,
)
from app.llm import get_provider
from app.llm.adapters import _bounded, _bounded_stream
from app.llm.base import (
    AuthenticationError,
    GenerationRequest,
    InvalidRequestError,
    ProviderTimeoutError,
    RateLimitError,
    TransientProviderError,
)
from tests.fakes import FakeClock


def _request(timeout: float | None) -> GenerationRequest:
    return GenerationRequest(messages=[], timeout_seconds=timeout)


def test_a_hanging_call_is_bounded_by_its_timeout():
    """The caller is released at the deadline even though the call never ends."""
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        _bounded(_request(0.2), lambda: time.sleep(30))
    elapsed = time.monotonic() - started
    assert elapsed < 5, f"the bound did not fire promptly ({elapsed:.2f}s)"


def test_the_bound_does_not_fire_early():
    """A call that finishes inside its timeout is returned, not abandoned."""
    assert _bounded(_request(30), lambda: "done") == "done"


def test_no_bound_without_a_timeout():
    """An unbounded request is left unbounded; the caller opted out."""
    assert _bounded(_request(None), lambda: "done") == "done"


def test_the_sdk_error_wins_when_it_arrives_first():
    """The SDK's own timeout carries more detail, so it must not be masked."""
    def call():
        raise ProviderTimeoutError("sdk said no", provider="test")

    with pytest.raises(ProviderTimeoutError):
        _bounded(_request(30), call)


def test_an_error_raised_in_the_worker_surfaces_on_the_caller_thread():
    """The worker's exception is re-raised with its own type, not swallowed."""
    def call():
        raise ValueError("provider exploded")

    with pytest.raises(ValueError, match="provider exploded"):
        _bounded(_request(30), call)


def test_an_abandoned_call_does_not_deliver_a_later_result():
    """A call that finishes after the deadline must not produce a value.

    The abandoned call keeps running on a daemon thread and its result is
    discarded; nothing may surface after the deadline.
    """
    released = threading.Event()

    def call():
        released.wait(5)
        return "too late"

    with pytest.raises(TimeoutError):
        _bounded(_request(0.2), call)
    released.set()
    time.sleep(0.2)
    # The point is that nothing raised a second time and no value escaped.
    assert True


def test_a_hanging_stream_is_bounded():
    """A stream that never yields is bounded like a call."""
    def open_stream():
        def gen():
            while True:
                time.sleep(0.05)
                yield {"type": "x"}

        return gen()

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        list(_bounded_stream(_request(0.2), open_stream))
    assert time.monotonic() - started < 5


def test_a_stream_that_ends_is_yielded_whole():
    """A bounded stream still delivers every item it produced."""
    def open_stream():
        return iter([{"type": "a"}, {"type": "b"}])

    assert list(_bounded_stream(_request(30), open_stream)) == [
        {"type": "a"}, {"type": "b"}]


def test_a_stream_error_surfaces_to_the_caller():
    """An error raised while producing a stream is not swallowed."""
    def open_stream():
        def gen():
            yield {"type": "a"}
            raise RuntimeError("stream broke")

        return gen()

    with pytest.raises(RuntimeError, match="stream broke"):
        list(_bounded_stream(_request(30), open_stream))


def test_an_abandoned_stream_is_closed():
    """The abandoned stream is closed, so its producer stops."""

    class Stream:
        def __init__(self):
            self.closed = False

        def __iter__(self):
            while True:
                time.sleep(0.05)
                yield {"type": "x"}

        def close(self):
            self.closed = True

    stream = Stream()

    def open_stream():
        return stream

    with pytest.raises(TimeoutError):
        list(_bounded_stream(_request(0.2), open_stream))
    time.sleep(0.3)
    assert stream.closed, "the abandoned stream was not closed"


def test_an_unbounded_stream_is_not_bounded():
    """No timeout means the stream is consumed as-is."""
    def open_stream():
        return iter([1, 2, 3])

    assert list(_bounded_stream(_request(None), open_stream)) == [1, 2, 3]


_TEXT = """# Course Policy

## Prerequisites

CS 253 requires CS 171 or CS_OX 171 or equivalent transfer credit.
"""

_ANSWER = "CS 253 requires CS 171 or CS_OX 171. [1]"


def test_the_research_path_doubles_the_allowance_for_a_truncated_round():
    """The fix that already existed: a truncated research round gets more room."""
    from app.config import load_settings
    from app.generation.research_controller import _with_retry_allowance

    settings = load_settings()
    request = GenerationRequest(messages=[], max_output_tokens=4096)
    assert _with_retry_allowance(request, 0, settings).max_output_tokens == 4096
    assert _with_retry_allowance(request, 1, settings).max_output_tokens == 8192
    assert _with_retry_allowance(request, 2, settings).max_output_tokens == 16384
    # The allowance never grows past the configured ceiling.
    assert (_with_retry_allowance(request, 9, settings).max_output_tokens
            == settings.research_truncation_max_output_tokens)


class _TimeProxy:
    """The `time` module with only its `sleep` replaced."""

    def __init__(self, sleep):
        self.sleep = sleep

    def __getattr__(self, name):
        return getattr(time, name)


@pytest.fixture()
def clock(monkeypatch):
    fake = FakeClock()
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
                             check_cancelled=lambda: None, clock=clock)
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
                           check_cancelled=lambda: None, clock=clock) == "answered"


def test_the_providers_retry_hint_is_honoured(clock):
    def call():
        raise _rate_limited(retry_after=7.5)

    with pytest.raises(RateLimitError):
        retry_transient(call, remaining_calls=4, remaining_seconds=60,
                        check_cancelled=lambda: None, clock=clock)
    assert clock.slept == [7.5]


def test_long_retry_after_fails_at_once_without_waiting(clock):
    attempts = []

    def call():
        attempts.append(1)
        raise _rate_limited(retry_after=9999)

    with pytest.raises(RateLimitError):
        retry_transient(call, remaining_calls=4, remaining_seconds=60,
                        check_cancelled=lambda: None, clock=clock)
    assert len(attempts) == 1
    assert clock.slept == []


def test_exponential_backoff_caps_at_max_delay_and_stops_at_deadline(clock):
    attempts = []

    def call():
        attempts.append(1)
        raise _transient()

    with pytest.raises(TransientProviderError):
        retry_transient(call, remaining_calls=12, remaining_seconds=80,
                        check_cancelled=lambda: None, max_retries=10,
                        clock=clock, jitter=lambda: 0.0)
    # 1 + 2 + 4 + 8 + 16 + 30 = 61s (< 80s); the next 30s wait would cross 80s.
    assert clock.slept == [1.0, 2.0, 4.0, 8.0, 16.0, _MAX_RETRY_DELAY_SECONDS]
    assert sum(clock.slept) < 80
    assert len(attempts) == 7


def test_a_second_failure_is_not_retried_again(clock):
    attempts = []

    def call():
        attempts.append(1)
        raise _transient()

    with pytest.raises(TransientProviderError):
        retry_transient(call, remaining_calls=8, remaining_seconds=60,
                        check_cancelled=lambda: None, clock=clock)
    assert len(attempts) == 2, "the default allows exactly one retry"


def test_no_retry_when_the_call_budget_is_gone(clock):
    attempts = []

    def call():
        attempts.append(1)
        raise _transient()

    with pytest.raises(TransientProviderError):
        retry_transient(call, remaining_calls=1, remaining_seconds=60,
                        check_cancelled=lambda: None, clock=clock)
    assert len(attempts) == 1, "a retry with no remaining call must not be made"
    assert clock.slept == []


def test_no_retry_when_the_time_budget_cannot_cover_the_wait(clock):
    attempts = []

    def call():
        attempts.append(1)
        raise _transient()

    with pytest.raises(TransientProviderError):
        retry_transient(call, remaining_calls=8, remaining_seconds=0.5,
                        check_cancelled=lambda: None, clock=clock)
    assert len(attempts) == 1
    assert clock.slept == []


@pytest.mark.parametrize(
    ("request_sent", "expected_attempts"),
    [
        (True, 1),
        (False, 2),
    ],
)
def test_unknown_outcome_timeout_is_never_replayed_while_unsent_slot_timeout_can_retry(
    clock, request_sent, expected_attempts
):
    attempts = []

    def call():
        attempts.append(1)
        if len(attempts) == 1:
            raise ProviderTimeoutError("timed out", provider="test", request_sent=request_sent)
        return "answered"

    if request_sent:
        with pytest.raises(ProviderTimeoutError):
            retry_transient(call, remaining_calls=4, remaining_seconds=60,
                            check_cancelled=lambda: None, clock=clock)
        assert clock.slept == []
    else:
        assert retry_transient(call, remaining_calls=4, remaining_seconds=60,
                               check_cancelled=lambda: None, clock=clock) == "answered"
    assert len(attempts) == expected_attempts


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
                        check_cancelled=lambda: None, clock=clock)
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
                        check_cancelled=check, clock=clock)
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
                           check_cancelled=lambda: None, max_retries=3, clock=clock) == "answered"
    assert len(attempts) == 3
    assert len(clock.slept) == 2
    assert clock.slept[0] < clock.slept[1]


def test_the_default_wait_is_short_with_jitter(clock):
    def call():
        raise _transient()

    with pytest.raises(TransientProviderError):
        retry_transient(call, remaining_calls=4, remaining_seconds=60,
                        check_cancelled=lambda: None, clock=clock)
    assert len(clock.slept) == 1
    assert 1.0 <= clock.slept[0] <= 1.25


def test_provider_concurrency_cap_holds_across_callers_and_bounds_slot_waits():
    from app.llm.base import ProviderGate

    clock = FakeClock()
    both_in_flight = threading.Barrier(2)
    release_slots = threading.Event()
    active = 0
    peak = 0
    lock = threading.Lock()

    def create(**_kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            first_wave = active <= 2 and not release_slots.is_set()
        if first_wave:
            import contextlib

            with contextlib.suppress(threading.BrokenBarrierError):
                both_in_flight.wait(timeout=2.0)
            release_slots.wait(timeout=2.0)
        with lock:
            active -= 1
        return SimpleNamespace(
            output=[SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text", text="ok")])],
            status="completed",
            usage=SimpleNamespace(input_tokens=4, output_tokens=2),
            _request_id="req-1",
            model="m",
        )

    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    settings = SimpleNamespace(
        llm_provider="openai", llm_model_id="m", llm_base_url="https://cap-test.example/v1",
        llm_max_concurrency=2,
    )
    provider_a = get_provider(settings, api_key="k", client=client)
    provider_b = get_provider(settings, api_key="k", client=client)
    assert provider_a.gate is provider_b.gate and provider_a.gate.limit == 2

    results: list = []

    def worker(provider, req):
        try:
            results.append(provider.generate(req).text)
        except ProviderTimeoutError as exc:
            results.append(exc)

    req = GenerationRequest([{"role": "user", "content": "hi"}], timeout_seconds=30.0)
    threads = [
        threading.Thread(target=worker, args=(provider_a if idx % 2 == 0 else provider_b, req))
        for idx in range(4)
    ]
    for thread in threads[:2]:
        thread.start()
    for thread in threads[2:]:
        thread.start()
    assert provider_a.gate.wait_for_waiters(2, timeout=2.0)
    assert peak == 2
    release_slots.set()
    for thread in threads:
        thread.join(timeout=2.0)
    assert peak == 2
    assert results == ["ok", "ok", "ok", "ok"]

    # Prove FakeClock bounds a slot wait at the operation's remaining time without real sleep.
    timed_gate = ProviderGate(1, provider="openai", clock=clock)
    provider_c = get_provider(settings, api_key="k", client=client)
    provider_c.gate = timed_gate
    held = threading.Event()
    unblock = threading.Event()

    def hold_create(**_kwargs):
        held.set()
        unblock.wait(timeout=2.0)
        return create()

    provider_c.client = SimpleNamespace(responses=SimpleNamespace(create=hold_create))
    outcomes: list = []
    holder = threading.Thread(target=lambda: outcomes.append(provider_c.generate(req).text))
    waiter_req = GenerationRequest([{"role": "user", "content": "hi"}], timeout_seconds=30.0, deadline_seconds=8.0)
    waiter = threading.Thread(target=lambda: outcomes.append(_catch(lambda: provider_c.generate(waiter_req))))
    holder.start()
    assert held.wait(timeout=2.0)
    waiter.start()
    assert timed_gate.wait_for_waiters(1, timeout=2.0)
    clock.advance(8.0)
    waiter.join(timeout=2.0)
    unblock.set()
    holder.join(timeout=2.0)
    timeout_err = next(item for item in outcomes if isinstance(item, ProviderTimeoutError))
    assert timeout_err.request_sent is False


def _catch(fn):
    try:
        return fn()
    except ProviderTimeoutError as exc:
        return exc


def test_factory_honors_hf_model_override_and_model_specific_stops(monkeypatch):
    monkeypatch.delenv("HF_STOP_TOKEN_STRINGS", raising=False)
    monkeypatch.setenv("HF_MODEL_ID", "openbmb/MiniCPM-2B-128k")
    settings = load_settings()
    same_model = get_provider(settings, provider="hf-local", model=settings.hf_model_id)
    other_model = get_provider(settings, provider="hf-local", model="another/local-model")
    assert same_model._settings.hf_stop_token_strings == ("<|im_end|>",)
    assert other_model._settings.hf_model_id == "another/local-model"
    assert other_model._settings.hf_stop_token_strings == ()


def test_factory_accepts_an_injected_client_and_keeps_secrets_out_of_repr():
    from app.llm.adapters import OpenAIProvider

    settings = SimpleNamespace(llm_provider="openai", llm_model_id="model")
    provider = get_provider(settings, api_key="secret", client=SimpleNamespace(responses=None))
    assert isinstance(provider, OpenAIProvider)
    assert "secret" not in repr(provider)


@pytest.mark.parametrize("kind", ["llm", "embedding"])
def test_factory_rejects_unknown_provider(kind):
    from app.embeddings import get_embedder

    settings = load_settings()
    field = f"{kind}_provider"
    settings = type(settings)(**{field: "nope"}, **{
        k: getattr(settings, k) for k in settings.__dataclass_fields__ if k != field
    })
    with pytest.raises(ValueError, match=f"unknown {kind.upper()}_PROVIDER"):
        (get_provider if kind == "llm" else get_embedder)(settings)
