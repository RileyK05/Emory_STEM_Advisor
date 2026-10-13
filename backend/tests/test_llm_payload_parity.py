"""Batch request bodies must equal the sync payloads for the same request.

The batch builders in `app.llm.batches` and the synchronous `generate` paths in
`app.llm.adapters` describe the same request to the same provider API, so every
field, messages included, must agree. This pins that so the two cannot drift.
"""

from types import SimpleNamespace

import pytest

from app.llm.adapters import AnthropicProvider, GoogleProvider, OpenAIProvider
from app.llm.base import GenerationRequest, ToolDefinition
from app.llm.batches import _anthropic_body, _google_body, _openai_body

SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
TOOLS = (ToolDefinition("lookup", "Look a course up.", {"type": "object", "properties": {}}),)
MESSAGES = ({"role": "system", "content": "Be brief."}, {"role": "user", "content": "Hello"})

REQUESTS = {
    "plain": GenerationRequest(MESSAGES, max_output_tokens=64),
    "schema": GenerationRequest(MESSAGES, max_output_tokens=64, temperature=0, response_schema=SCHEMA),
    "tools": GenerationRequest(MESSAGES, max_output_tokens=64, tools=TOOLS, tool_choice="auto"),
    "everything": GenerationRequest(MESSAGES, max_output_tokens=64, temperature=0.2,
                                    response_schema=SCHEMA, tools=TOOLS, tool_choice="any"),
}


class _Capture:
    """A client double that records the keyword arguments of the one create call."""

    def __init__(self):
        self.kwargs = None
        capture = self

        def create(**kwargs):
            capture.kwargs = kwargs
            return SimpleNamespace(output=[], content=[], usage=None, status="completed",
                                   stop_reason="end_turn")

        def generate_content(**kwargs):
            capture.kwargs = kwargs
            return SimpleNamespace(candidates=[], usage_metadata=None)

        self.responses = SimpleNamespace(create=create)
        self.messages = SimpleNamespace(create=create)
        self.models = SimpleNamespace(generate_content=generate_content)


def _shared(sync: dict, batch: dict, keys: tuple[str, ...]):
    for key in keys:
        assert sync.get(key) == batch.get(key), f"{key!r} differs between sync and batch"


@pytest.mark.parametrize("name", REQUESTS)
def test_openai_sync_and_batch_agree_on_every_field(name):
    request = REQUESTS[name]
    client = _Capture()
    OpenAIProvider(client, "model").generate(request)
    _shared(client.kwargs, _openai_body(request, "model"),
            ("model", "store", "input", "max_output_tokens", "temperature", "tools", "tool_choice",
             "text"))


@pytest.mark.parametrize("name", REQUESTS)
def test_anthropic_sync_and_batch_agree_on_every_field(name):
    request = REQUESTS[name]
    client = _Capture()
    AnthropicProvider(client, "model").generate(request)
    _shared(client.kwargs, _anthropic_body(request, "model"),
            ("model", "max_tokens", "system", "messages", "temperature", "tools", "tool_choice",
             "output_config"))


@pytest.mark.parametrize("name", REQUESTS)
def test_google_sync_and_batch_agree_on_tool_and_schema_config(name):
    request = REQUESTS[name]
    client = _Capture()
    GoogleProvider(client, "model").generate(request)
    sync_config = client.kwargs["config"]
    batch_body = _google_body(request)
    assert client.kwargs["contents"] == batch_body["contents"]
    batch_config = batch_body["config"]
    sync_fields = sync_config.model_dump() if sync_config is not None else {}
    for key in ("system_instruction", "max_output_tokens", "temperature", "response_mime_type",
                "response_schema"):
        assert sync_fields.get(key) == batch_config.get(key), key
    for key in ("tools", "tool_config"):
        sync_value = getattr(sync_config, key, None) if sync_config is not None else None
        batch_value = batch_config.get(key)
        assert (sync_value is None) == (batch_value is None), key
        if batch_value is not None:
            assert sync_value == batch_value, key
