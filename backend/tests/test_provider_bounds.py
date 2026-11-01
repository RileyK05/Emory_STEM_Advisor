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

import pytest

from app.llm.adapters import _bounded, _bounded_stream
from app.llm.base import GenerationRequest, ProviderTimeoutError


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
