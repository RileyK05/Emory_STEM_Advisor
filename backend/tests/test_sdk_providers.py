import json
from dataclasses import replace
from types import SimpleNamespace

import httpx
import httpx2
import pytest
from anthropic import Anthropic
from google import genai
from google.genai import types
from openai import OpenAI

from app.config import load_settings
from app.llm.adapters import (
    AnthropicProvider,
    GoogleProvider,
    OpenAICompatibleProvider,
    OpenAIProvider,
)
from app.llm.base import (
    GenerationRequest,
    InvalidRequestError,
    ProviderTimeoutError,
    ToolDefinition,
    Usage,
)
from app.llm.decision import DecisionRequest
from app.llm.decision_adapters import OpenAIDecisionProvider
from app.llm.factory import get_provider


@pytest.mark.parametrize("thinking, cache_key", [
    (None, False), ("enabled", False), ("disabled", False), (None, True), ("disabled", True)])
@pytest.mark.parametrize("stream", [False, True])
def test_compatible_extensions_are_explicit_and_factory_scoped(thinking, cache_key, stream):
    payloads = []

    def handler(request):
        payloads.append(json.loads(request.content))
        body = {"id": "chat-test", "object": "chat.completion", "created": 1,
                "model": "test", "choices": [{"index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "Answer"}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6,
                          "prompt_tokens_details": {"cached_tokens": 3}}}
        if stream:
            body["object"] = "chat.completion.chunk"
            body["choices"][0]["delta"] = body["choices"][0].pop("message")
            return httpx2.Response(200, content="data: " + json.dumps(body) + "\n\ndata: [DONE]\n\n",
                headers={"content-type": "text/event-stream"}, request=request)
        return _openai_response(body, request=request)

    sdk = OpenAI(api_key="test", base_url="https://mock/v1",
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    settings = replace(load_settings(), llm_provider="openai-compatible", llm_model_id="test",
                       llm_compatible_thinking=thinking, llm_compatible_cache_key=cache_key)
    provider = get_provider(settings, client=sdk)
    request = GenerationRequest([{"role": "user", "content": "question"}], cache_key="plan-1-abc")
    if stream:
        result = next(e for e in provider.stream(request) if e.type == "completed").result
    else:
        result = provider.generate(request)
    assert payloads[0].get("thinking") == ({"type": thinking} if thinking else None)
    # The routing key is sent only where enabled; a reported cache hit is kept either way.
    assert payloads[0].get("prompt_cache_key") == ("plan-1-abc" if cache_key else None)
    assert result.usage.cached_prompt_tokens == 3 and result.usage.prompt_tokens == 4
    sdk.close()


def _response(body, *, headers=None, request=None):
    return httpx.Response(200, json=body, headers=headers or {}, request=request or httpx.Request("POST", "https://mock"))

def _openai_response(body, *, headers=None, request=None):
    return httpx2.Response(200, json=body, headers=headers or {}, request=request or httpx2.Request("POST", "https://mock"))


def test_openai_actual_sdk_serializes_responses_schema_and_tool_call():
    requests = []

    def handler(request):
        requests.append(request)
        return _openai_response({
            "id": "resp_1", "object": "response", "created_at": 1, "status": "completed",
            "model": "model-x", "output": [
                {"id": "msg_1", "type": "message", "role": "assistant", "status": "completed",
                 "content": [{"type": "refusal", "refusal": "I cannot."}]},
                {"id": "fc_1", "type": "function_call", "call_id": "call_1",
                 "name": "lookup", "arguments": "{\"q\":\"x\"}", "status": "completed"},
            ], "usage": {"input_tokens": 7, "output_tokens": 2, "total_tokens": 9},
        }, headers={"x-request-id": "req-sdk-1"})

    sdk = OpenAI(api_key="sk-test-secret", base_url="https://mock/v1",
                 http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    provider = OpenAIProvider(sdk, "model-x")
    result = provider.generate(GenerationRequest(
        [{"role": "user", "content": "inspect"}], max_output_tokens=32,
        response_schema={"type": "object", "properties": {"ok": {"type": "boolean"}},
                         "required": ["ok"], "additionalProperties": False},
        tools=(ToolDefinition("lookup", "Lookup data", {"type": "object", "properties": {
            "q": {"type": "string"}}, "required": ["q"], "additionalProperties": False}),),
    ))
    payload = json.loads(requests[0].content)
    assert payload["text"]["format"]["type"] == "json_schema"
    assert payload["tools"][0]["name"] == "lookup"
    assert result.refusal == "I cannot."
    assert result.tool_calls[0].arguments == {"q": "x"}
    assert result.usage == Usage(7, 2, 9, "provider")
    assert result.request_id == "req-sdk-1"
    assert "sk-test-secret" not in repr(provider)
    sdk.close()


def test_openai_actual_sdk_stream_reads_final_usage():
    sse = ("event: response.created\ndata: {\"type\":\"response.created\",\"response\":{\"id\":\"resp_stream\",\"object\":\"response\",\"created_at\":1,\"status\":\"in_progress\",\"model\":\"model-x\",\"output\":[]}}\n\n"
           "event: response.output_text.delta\ndata: {\"type\":\"response.output_text.delta\",\"delta\":\"hello\",\"item_id\":\"msg_1\",\"output_index\":0,"+"\"content_index\":0}\n\n"
           "event: response.completed\ndata: {\"type\":\"response.completed\",\"response\":{\"id\":\"resp_stream\",\"object\":\"response\",\"created_at\":1,\"status\":\"completed\",\"model\":\"model-x\",\"output\":[],\"usage\":{\"input_tokens\":4,\"output_tokens\":1,\"total_tokens\":5}}}\n\n")
    sdk = OpenAI(api_key="sk-test", base_url="https://mock/v1",
                 http_client=httpx2.Client(transport=httpx2.MockTransport(
                     lambda req: httpx2.Response(200, content=sse, headers={"content-type":"text/event-stream"}, request=req))))
    events = list(OpenAIProvider(sdk, "model-x").stream(GenerationRequest(
        [{"role":"user","content":"hi"}])))
    assert events[0].text == "hello"
    assert events[-1].result.usage == Usage(4, 1, 5, "provider")
    sdk.close()


def test_openai_compatible_stream_separates_reasoning_and_reports_usage():
    def chunk(delta, finish=None, usage=None):
        body = {"id": "c1", "object": "chat.completion.chunk", "created": 1,
                "model": "longcat",
                "choices": [] if usage is not None else
                [{"index": 0, "delta": delta, "finish_reason": finish}]}
        if usage is not None:
            body["usage"] = usage
        return "data: " + json.dumps(body) + "\n\n"

    sse = (
        chunk({"role": "assistant", "reasoning_content": "private chain of thought"})
        + chunk({"content": "LONG "})
        + chunk({"content": "CAT OK"})
        + chunk({}, finish="stop")
        + chunk({}, usage={"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10})
        + "data: [DONE]\n\n"
    )
    sdk = OpenAI(api_key="sk-test", base_url="https://mock/v1",
                 http_client=httpx2.Client(transport=httpx2.MockTransport(
                     lambda req: httpx2.Response(200, content=sse.encode(),
                                                 headers={"content-type": "text/event-stream"},
                                                 request=req))))
    events = list(OpenAICompatibleProvider(sdk, "longcat").stream(GenerationRequest(
        [{"role": "user", "content": "hi"}], max_output_tokens=128)))
    # Reasoning deltas never become answer text.
    assert [event.text for event in events if event.type == "text_delta"] == ["LONG ", "CAT OK"]
    result = events[-1].result
    assert result.text == "LONG CAT OK"
    assert result.finish_reason == "stop"
    assert result.usage == Usage(7, 3, 10, "provider")
    sdk.close()


def test_openai_compatible_stream_marks_a_truncated_answer():
    def chunk(delta, finish=None):
        return "data: " + json.dumps({
            "id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "longcat",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\n"

    sse = (chunk({"role": "assistant", "reasoning_content": "thinking..."})
           + chunk({}, finish="length") + "data: [DONE]\n\n")
    sdk = OpenAI(api_key="sk-test", base_url="https://mock/v1",
                 http_client=httpx2.Client(transport=httpx2.MockTransport(
                     lambda req: httpx2.Response(200, content=sse.encode(),
                                                 headers={"content-type": "text/event-stream"},
                                                 request=req))))
    result = list(OpenAICompatibleProvider(sdk, "longcat").stream(
        GenerationRequest([{"role": "user", "content": "hi"}], max_output_tokens=8)))[-1].result
    assert result.text == ""
    assert result.finish_reason == "length"
    sdk.close()


def test_anthropic_actual_sdk_serializes_tools_json_schema_and_refusal():
    requests = []

    def handler(request):
        requests.append(request)
        return _response({
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-test",
            "content": [{"type":"text","text":"No."}, {"type":"tool_use","id":"tool_1",
                         "name":"lookup","input":{"q":"x"}}, {"type":"text","text":"\ufffc"}],
            "stop_reason":"tool_use", "stop_sequence":None,
            "usage":{"input_tokens":5,"output_tokens":3},
        }, headers={"request-id":"anthropic-req"})

    sdk = Anthropic(api_key="sk-ant-test", base_url="https://mock",
                    http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    provider = AnthropicProvider(sdk, "claude-test")
    result = provider.generate(GenerationRequest(
        [{"role":"system","content":"rules"},{"role":"user","content":"inspect"}],
        max_output_tokens=40,
        response_schema={"type":"object","properties":{"ok":{"type":"boolean"}}},
        tools=(ToolDefinition("lookup","Lookup",{"type":"object","properties":{"q":{"type":"string"}}}),),
    ))
    payload = json.loads(requests[0].content)
    assert payload["system"] == "rules"
    assert payload["tools"][0]["name"] == "lookup"
    assert payload["output_config"]["format"]["type"] == "json_schema"
    assert result.tool_calls[0].arguments == {"q":"x"}
    assert result.usage == Usage(5, 3, 8, "provider")
    assert result.request_id == "anthropic-req"
    sdk.close()


def test_google_actual_sdk_serializes_generation_config_and_usage():
    requests = []

    def handler(request):
        requests.append(request)
        return _response({
            "candidates":[{"content":{"role":"model","parts":[{"text":"answer"},
                          {"functionCall":{"name":"lookup","args":{"q":"x"}}}]},
                          "finishReason":"STOP"}],
            "usageMetadata":{"promptTokenCount":6,"candidatesTokenCount":2,"totalTokenCount":8},
            "modelVersion":"gemini-test",
        })

    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport)
    sdk = genai.Client(api_key="google-test-secret", http_options=types.HttpOptions(httpx_client=http))
    result = GoogleProvider(sdk, "gemini-test").generate(GenerationRequest(
        [{"role":"system","content":"rules"},{"role":"user","content":"inspect"}],
        max_output_tokens=30, temperature=0.25,
        response_schema={"type":"object","properties":{"ok":{"type":"boolean"}}},
        tools=(ToolDefinition("lookup","Lookup",{"type":"object","properties":{"q":{"type":"string"}}}),),
    ))
    payload = json.loads(requests[0].content)
    assert payload["systemInstruction"]["parts"][0]["text"] == "rules"
    assert payload["generationConfig"]["responseMimeType"] == "application/json"
    assert result.text == "answer"
    assert result.tool_calls[0].arguments == {"q":"x"}
    assert result.usage == Usage(6, 2, 8, "provider")
    http.close()


def test_openai_actual_sdk_native_decisions_serializes_choice_question():
    requests = []

    def handler(request):
        requests.append(request)
        return _openai_response({
            "id":"decision_1", "object":"decision", "model":"gpt-6-luna",
            "answers":[{"type":"choice","name":"answer","choice":"yes","confidence":0.9,
                        "probabilities":[{"value":"yes","probability":0.9},
                                          {"value":"no","probability":0.1}]}],
            "usage":{"input_tokens":3,"output_tokens":0,"total_tokens":3},
        })

    sdk = OpenAI(api_key="sk-test", base_url="https://mock/v1",
                 http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    result = OpenAIDecisionProvider(sdk, "gpt-6-luna").decide(DecisionRequest(
        task="approve?", input="evidence", choices=("yes","no")))
    payload=json.loads(requests[0].content)
    assert payload["questions"][0]["type"] == "choice"
    assert result.choice == "yes" and result.usage.total_tokens == 3
    sdk.close()


def test_openai_actual_sdk_serializes_json_decision_input_as_text():
    requests = []

    def handler(request):
        requests.append(request)
        return _openai_response({
            "id":"decision_json", "object":"decision", "model":"gpt-6-luna",
            "answers":[{"type":"predicate","name":"answer","probability":0.75}],
            "usage":{"input_tokens":5,"output_tokens":0,"total_tokens":5},
        })

    sdk = OpenAI(api_key="sk-test", base_url="https://mock/v1",
                 http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    OpenAIDecisionProvider(sdk, "gpt-6-luna").decide(DecisionRequest(
        task="classify", predicate="is eligible?", input={"student": "sample", "credits": 12}))
    payload = json.loads(requests[0].content)
    assert payload["input"] == '{"student":"sample","credits":12}'
    assert isinstance(payload["input"], str)
    sdk.close()


def test_anthropic_actual_sdk_stream_maps_usage_and_stop_reason():
    events = [
        {"type":"message_start","message":{"id":"msg_stream","type":"message","role":"assistant",
            "model":"claude-test","content":[],"stop_reason":None,"stop_sequence":None,
            "usage":{"input_tokens":3,"output_tokens":1}}},
        {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}},
        {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hello"}},
        {"type":"content_block_stop","index":0},
        {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":None},
         "usage":{"output_tokens":2}},
        {"type":"message_stop"},
    ]
    sse = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)
    sdk = Anthropic(api_key="sk-ant-test", base_url="https://mock",
        http_client=httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(
            200, content=sse.encode(), headers={"content-type":"text/event-stream"}, request=req))))
    output = list(AnthropicProvider(sdk, "claude-test").stream(GenerationRequest(
        [{"role":"user","content":"hi"}])))
    assert output[0].text == "hello"
    assert output[-1].result.usage == Usage(3, 2, 5, "provider")
    assert output[-1].result.finish_reason == "end_turn"
    sdk.close()


def test_google_actual_sdk_stream_maps_usage_and_finish_reason():
    events = [
        {"candidates":[{"content":{"role":"model","parts":[{"text":"hello"}]},
            "finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":2,
            "candidatesTokenCount":1,"totalTokenCount":3}},
    ]
    sse = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
    http = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(
        200, content=sse.encode(), headers={"content-type":"text/event-stream"}, request=req)))
    sdk = genai.Client(api_key="google-test", http_options=types.HttpOptions(httpx_client=http))
    output = list(GoogleProvider(sdk, "gemini-test").stream(GenerationRequest(
        [{"role":"user","content":"hi"}])))
    assert output[0].text == "hello"
    assert output[-1].result.usage == Usage(2, 1, 3, "provider")
    assert output[-1].result.finish_reason == "stop"
    http.close()


def test_sdk_adapters_forward_request_timeouts_and_google_uses_milliseconds():
    observed = []
    def openai_handler(request):
        observed.append(request)
        return _openai_response({"id":"r","object":"response","created_at":1,
            "status":"completed","model":"m","output":[],
            "usage":{"input_tokens":1,"output_tokens":1}}, request=request)
    openai = OpenAI(api_key="test", base_url="https://mock/v1",
                    http_client=httpx2.Client(transport=httpx2.MockTransport(openai_handler)))
    OpenAIProvider(openai, "m").generate(GenerationRequest(
        [{"role":"user","content":"x"}], max_output_tokens=7, timeout_seconds=2.5))
    assert json.loads(observed[-1].content)["max_output_tokens"] == 7
    assert observed[-1].extensions["timeout"]["read"] == 2.5

    anthropic_seen = []
    def anthropic_handler(request):
        anthropic_seen.append(request)
        return _response({"id":"msg","type":"message","role":"assistant","model":"m",
            "content":[{"type":"text","text":"ok"}],"stop_reason":"end_turn","usage":{
                "input_tokens":1,"output_tokens":1}}, request=request)
    anthropic = Anthropic(api_key="test", base_url="https://mock",
        http_client=httpx.Client(transport=httpx.MockTransport(anthropic_handler)))
    AnthropicProvider(anthropic, "m").generate(GenerationRequest(
        [{"role":"user","content":"x"}], max_output_tokens=8, timeout_seconds=3))
    assert json.loads(anthropic_seen[-1].content)["max_tokens"] == 8
    assert anthropic_seen[-1].extensions["timeout"]["read"] == 3

    google_seen = []
    def google_handler(request):
        google_seen.append(request)
        return _response({"candidates":[{"content":{"parts":[{"text":"ok"}]},
            "finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":1,
            "candidatesTokenCount":1}}, request=request)
    google_http = httpx.Client(transport=httpx.MockTransport(google_handler))
    google = genai.Client(api_key="test", http_options=types.HttpOptions(httpx_client=google_http))
    GoogleProvider(google, "m").generate(GenerationRequest(
        [{"role":"user","content":"x"}], max_output_tokens=9, timeout_seconds=4.25))
    assert json.loads(google_seen[-1].content)["generationConfig"]["maxOutputTokens"] == 9
    assert google_seen[-1].extensions["timeout"]["read"] == 4.25
    openai.close()
    anthropic.close()
    google_http.close()


def test_generation_request_rejects_invalid_budgets_and_maps_timeout_errors():
    import pytest

    from app.llm.adapters import _error

    with pytest.raises(ValueError, match="timeout_seconds"):
        GenerationRequest([], timeout_seconds=float("inf"))
    with pytest.raises(ValueError, match="max_output_tokens"):
        GenerationRequest([], max_output_tokens=0)
    class APITimeoutError(Exception):
        pass
    assert isinstance(_error("openai", APITimeoutError()), ProviderTimeoutError)


def test_factory_disables_sdk_retries_and_sets_bounded_timeouts():
    settings = SimpleNamespace(llm_provider="openai", llm_api_key="test-key",
                               llm_model_id="m", llm_base_url=None,
                               llm_timeout_seconds=7.5)
    openai_provider = get_provider(settings)
    assert openai_provider.client.max_retries == 0
    assert openai_provider.client._client.timeout.read == 7.5
    openai_provider.client.close()

    settings.llm_provider = "anthropic"
    anthropic_provider = get_provider(settings, api_key="test")
    assert anthropic_provider.client.max_retries == 0
    assert anthropic_provider.client._client.timeout.read == 7.5
    anthropic_provider.client.close()

    settings.llm_provider = "google"
    google_provider = get_provider(settings, api_key="test")
    options = google_provider.client._api_client._http_options
    assert options.timeout == 7500
    assert options.retry_options.attempts == 1
    google_provider.client.close()


def test_openai_responses_reasoning_effort_is_explicit_and_omits_temperature():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx2.Response(200, json={"id": "r", "object": "response", "created_at": 1,
            "status": "completed", "model": "configured-model", "output": [],
            "usage": {"input_tokens": 1, "output_tokens": 1}}, request=request)

    sdk = OpenAI(api_key="test", base_url="https://mock/v1",
                 http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    OpenAIProvider(sdk, "configured-model").generate(GenerationRequest(
        [{"role": "user", "content": "x"}], reasoning_effort="high"))
    payload = json.loads(requests[0].content)
    assert payload["reasoning"] == {"effort": "high"}
    assert "temperature" not in payload
    sdk.close()


def test_openai_compatible_maps_reasoning_effort_and_rejects_temperature_pair():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx2.Response(200, json={"id": "r", "object": "chat.completion",
            "model": "m", "choices": [{"index": 0, "message": {"role": "assistant",
            "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
            request=request)

    sdk = OpenAI(api_key="test", base_url="https://mock/v1",
                 http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    provider = OpenAICompatibleProvider(sdk, "m")
    provider.generate(GenerationRequest([{"role": "user", "content": "x"}],
                                        reasoning_effort="medium"))
    payload = json.loads(requests[0].content)
    assert payload["reasoning_effort"] == "medium"
    assert "temperature" not in payload
    with pytest.raises(ValueError, match="cannot be combined"):
        GenerationRequest([], temperature=0, reasoning_effort="medium")
    with pytest.raises(InvalidRequestError, match="unsupported"):
        AnthropicProvider(object(), "m").generate(GenerationRequest([], reasoning_effort="high"))
    sdk.close()
