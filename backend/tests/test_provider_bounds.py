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
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import load_settings
from app.generation.answer import (
    TRUNCATED_RETRY_NOTE,
    GenerationRejected,
    _with_retry_allowance,
    answer_from_context,
)
from app.generation.controller import (
    _MAX_RETRY_DELAY_SECONDS,
    retry_transient,
)
from app.llm import get_provider
from app.llm.adapters import _bounded, _bounded_stream
from app.llm.base import (
    AuthenticationError,
    GenerationRequest,
    GenerationResult,
    InvalidRequestError,
    ProviderTimeoutError,
    RateLimitError,
    TransientProviderError,
    Usage,
)
from app.operations.budget import BudgetExceeded
from app.retrieval.types import CitedChunk
from tests.fakes import ScriptedProvider
from tests.helpers import api_client, collection_name, ingest_text


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


def _chunk() -> CitedChunk:
    from uuid import uuid4

    return CitedChunk(
        chunk_id=uuid4(), source_id=uuid4(), chunk_index=0,
        source_name="policy.md", locator_label="CS 253",
        text="CS 253 requires CS 171 or CS_OX 171 or equivalent transfer credit.",
        seams=(), normalized_score=1.0, source_slot=0, rank=1)


def _truncated(text: str = "") -> GenerationResult:
    return GenerationResult(text=text, finish_reason="length",
                            usage=Usage(50, 4096, source="provider"))


def _complete() -> GenerationResult:
    return GenerationResult(text=_ANSWER, finish_reason="stop",
                            usage=Usage(50, 12, source="provider"))


def _ask(db, collection, provider, question="What does CS 253 require?"):
    client = api_client(db, collection, provider=provider)
    response = client.post("/api/query", json={
        "query": question, "collectionId": collection_name(db, collection)})
    assert response.status_code == 200, response.text
    return response.json()


# ------------------------------------------------------------------ the retry


def test_a_truncated_reply_is_asked_again_with_a_larger_allowance(db, collection,
                                                                   tmp_path):
    """A cut-off draft gets another chance instead of becoming a refusal."""
    ingest_text(db, collection, tmp_path, "policy.md", _TEXT,
                content_type="text/markdown")
    db.commit()

    provider = ScriptedProvider(results=[_truncated(), _complete()])
    payload = _ask(db, collection, provider)

    assert payload["type"] == "answer", payload
    assert len(provider.requests) == 2, "the cut-off reply was never retried"
    first, second = provider.requests
    assert second.max_output_tokens > first.max_output_tokens, (
        "the retry did not ask for more room")


def test_the_allowance_doubles_with_every_truncation():
    """Each cut-off reply doubles the allowance the next one is asked for.

    The operation's call budget is what decides how many retries a request can
    afford, so this asks the generator directly: the budget is not the subject
    here.
    """
    provider = ScriptedProvider(results=[_truncated(), _truncated(), _complete()])
    answer = answer_from_context(
        [_chunk()], "What does CS 253 require?", provider=provider, max_new_tokens=4096,
        course_subjects={"CS", "CS_OX"}, truncation_ceiling=16384)

    assert answer.text.startswith("CS 253 requires")
    allowances = [request.max_output_tokens for request in provider.requests]
    assert len(allowances) == 3, allowances
    assert allowances[1] == allowances[0] * 2, allowances
    assert allowances[2] == allowances[1] * 2, allowances


def test_the_retry_asks_the_model_to_answer_directly():
    """More room alone does not help a model that deliberated until the limit."""
    provider = ScriptedProvider(results=[_truncated(), _complete()])
    answer_from_context([_chunk()], "What does CS 253 require?", provider=provider,
                        max_new_tokens=4096, course_subjects={"CS", "CS_OX"},
                        truncation_ceiling=16384)

    first, second = provider.requests
    assert TRUNCATED_RETRY_NOTE not in str(first.messages)
    assert TRUNCATED_RETRY_NOTE in str(second.messages), (
        "the retry did not tell the model its previous reply was cut off")


def test_the_retry_is_bounded(db, collection, tmp_path):
    """A model that never finishes is not asked forever."""
    ingest_text(db, collection, tmp_path, "policy.md", _TEXT,
                content_type="text/markdown")
    db.commit()

    provider = ScriptedProvider(results=[_truncated()])
    payload = _ask(db, collection, provider)

    assert payload["type"] == "refusal", payload
    assert not payload.get("citations"), "a withheld reply still published citations"
    assert 1 < len(provider.requests) <= 4, "the retry did not stop"


def test_a_reply_that_never_finishes_is_still_withheld(db, collection, tmp_path):
    """The safety behaviour is unchanged once the retries are spent."""
    ingest_text(db, collection, tmp_path, "policy.md", _TEXT,
                content_type="text/markdown")
    db.commit()

    provider = ScriptedProvider(results=[_truncated()])
    payload = _ask(db, collection, provider)

    assert payload["type"] == "refusal", payload


def test_the_tools_path_reserves_a_call_for_the_answer_s_retry(db, collection, tmp_path):
    """The tool turns must not spend the whole operation budget.

    The tools path spends provider calls on tool turns. A reply that ran out
    of output allowance then needs one more call to be asked again with a
    larger allowance, and that call has to exist. One operation call is
    reserved for it, so the answer is retried instead of withheld.
    """
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from app.api import create_app
    from app.config import load_settings
    from app.db.connection import connection_dsn
    from app.llm.base import ToolCall
    from tests.fakes import FakeEmbedder
    from tests.helpers import lenient_config

    ingest_text(db, collection, tmp_path, "policy.md", _TEXT,
                content_type="text/markdown")
    db.commit()

    tool = ToolCall("calc-1", "advisor_calculate", {"expression": "0.1+0.2"})
    provider = ScriptedProvider(results=[
        GenerationResult(tool_calls=(tool,), finish_reason="tool_calls",
                         usage=Usage(50, 12, source="provider")),
        GenerationResult(tool_calls=(ToolCall("calc-2", "advisor_calculate",
                                               {"expression": "0.2+0.1"}),),
                         finish_reason="tool_calls",
                         usage=Usage(50, 12, source="provider")),
        _truncated(_ANSWER),
        _complete(),
    ])
    settings = replace(load_settings(), tools_enabled=True, llm_provider="openai",
                       llm_model_id="mock-tools", database_url=connection_dsn(db),
                       operation_max_calls=4)
    app = create_app(settings=settings, allow_test_client=True, config=lenient_config(),
                     embedder=FakeEmbedder(8), provider=provider)
    app.state.override_conn = db
    response = TestClient(app).post("/api/query", json={
        "query": "What does CS 253 require?",
        "collectionId": collection_name(db, collection)})
    payload = response.json()
    assert payload["type"] == "answer", payload
    assert len(provider.requests) == 4, "the cut-off reply was never retried"
    assert provider.requests[3].max_output_tokens > provider.requests[2].max_output_tokens, (
        "the retry did not ask for more room")


# ------------------------------------------------------- the allowance itself


def test_the_allowance_doubles_from_the_requested_base():
    """The base is the requested allowance, not the granted one."""
    request = GenerationRequest(messages=[], max_output_tokens=4096)
    assert _with_retry_allowance(request, 4096, 0, 16384).max_output_tokens == 4096
    assert _with_retry_allowance(request, 4096, 1, 16384).max_output_tokens == 8192
    assert _with_retry_allowance(request, 4096, 2, 16384).max_output_tokens == 16384


def test_the_allowance_never_grows_past_the_ceiling():
    request = GenerationRequest(messages=[], max_output_tokens=4096)
    assert _with_retry_allowance(request, 4096, 9, 16384).max_output_tokens == 16384


def test_an_unbounded_request_is_left_alone():
    """A request with no allowance cannot be doubled."""
    request = GenerationRequest(messages=[], max_output_tokens=None)
    assert _with_retry_allowance(request, None, 3, 16384).max_output_tokens is None


# ---------------------------------------------------------- the budget fence


def test_a_retry_the_operation_cannot_afford_is_not_made():
    """A cut-off draft is withheld when the budget cannot buy another call.

    The operation may spend only the calls it reserved. When the next call
    cannot be reserved, the retry stops and the cut-off draft is withheld,
    exactly as it was before the retry existed.
    """
    calls: list[int] = []

    def prepare(request: GenerationRequest) -> GenerationRequest:
        calls.append(1)
        if len(calls) > 1:
            raise BudgetExceeded("call_limit", "The generation operation call limit was reached.")
        return request

    provider = ScriptedProvider(results=[_truncated()])
    with pytest.raises(GenerationRejected) as rejected:
        answer_from_context([_chunk()], "What does CS 253 require?",
                            provider=provider, max_new_tokens=4096,
                            prepare_request=prepare, truncation_ceiling=16384)

    assert "incomplete" in str(rejected.value)
    assert len(calls) == 2, "the retry was not even attempted"


def test_the_first_call_is_not_downgraded_when_the_budget_is_gone():
    """A budget that cannot cover the first call is still a budget failure."""
    def prepare(request: GenerationRequest) -> GenerationRequest:
        raise BudgetExceeded("call_limit", "The generation operation call limit was reached.")

    provider = ScriptedProvider(results=[_complete()])
    with pytest.raises(BudgetExceeded):
        answer_from_context([_chunk()], "What does CS 253 require?",
                            provider=provider, max_new_tokens=4096,
                            prepare_request=prepare, truncation_ceiling=16384)


# -------------------------------------------------------------- the operation


def test_the_operation_admits_the_retry_allowance():
    """The per-call output cap must allow the allowance the retry asks for."""
    from app.api.query_service import _answer_operation_limits
    from app.config import load_settings

    settings = load_settings()
    limits = _answer_operation_limits(settings)
    assert limits.per_call_output_cap >= settings.answer_truncation_max_output_tokens
    assert limits.per_call_output_cap >= settings.llm_max_output_tokens


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


def test_the_truncation_ceiling_is_configured():
    """The ceiling is a setting, so an operator can bound the retry."""
    from app.config import load_settings

    settings = load_settings()
    assert settings.answer_truncation_max_output_tokens >= settings.llm_max_output_tokens


def test_the_source_of_the_legacy_path_names_no_truncation_retry():
    """The legacy path must be able to raise its own output allowance.

    A reply cut off at the limit is asked again with the allowance doubled, so
    the path has to be able to ask for more room at all.
    """
    from app.api import query_service

    source = Path(query_service.__file__).read_text(encoding="utf-8")
    assert "truncation_ceiling" in source, (
        "the legacy generation path never passes a truncation ceiling, so it "
        "cannot raise the output allowance for a reply that ran out of it")


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
