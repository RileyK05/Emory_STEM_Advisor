"""A reply that ran out of output allowance must not become an answer.

Two paths answer a question. The research path retries a round whose reply was
cut off at the output limit, with a doubled allowance. The legacy path now
does the same: it withholds the draft only after the retries are spent.

Withholding is the right safety behaviour and is pinned here. So is the retry:
a model that spends its whole allowance reasoning before it writes anything
produces an empty draft, and an empty draft is refused. A question that needs a
long answer (every course a prerequisite unlocks, with each one's other
conditions) is exactly the question that trips this.

Measured on the development corpus before the retry existed, "Which courses
does completing CS 253 unlock?" was refused 4 times in 4 on the legacy path,
every time with a zero-character draft and finish_reason "length".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.generation.answer import (
    TRUNCATED_RETRY_NOTE,
    GenerationRejected,
    _with_retry_allowance,
    answer_from_context,
)
from app.llm.base import GenerationRequest, GenerationResult, Usage
from app.operations.budget import BudgetExceeded
from app.retrieval.types import CitedChunk
from tests.fakes import ScriptedProvider
from tests.helpers import api_client, collection_name, ingest_text

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
