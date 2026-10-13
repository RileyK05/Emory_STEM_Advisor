import json

import httpx
import httpx2
from anthropic import Anthropic
from google import genai
from google.genai import types
from openai import OpenAI

from app.generation.controller import ToolConversationController
from app.llm.adapters import (
    AnthropicProvider,
    GoogleProvider,
    OpenAICompatibleProvider,
    OpenAIProvider,
)
from app.llm.base import GenerationRequest, TextMessage
from app.tools.builtins import build_default_registry
from app.tools.registry import ToolCapability, ToolScope


class _UnlimitedOperation:
    def prepare(self, request):
        return request

    def account(self, result, error):
        pass


def _openai_response(request, *, tool):
    output = ([{"id": "fc_sdk", "type": "function_call", "call_id": "call_sdk",
                "name": "advisor_calculate", "arguments": '{"expression":"2 + 2"}',
                "status": "completed"}]
              if tool else [{"id": "msg_sdk", "type": "message", "role": "assistant",
                             "status": "completed", "content": [
                                 {"type": "output_text", "text": "4"}]}])
    return httpx2.Response(200, json={"id": "resp_sdk", "object": "response", "created_at": 1,
        "status": "completed", "model": "model-x", "output": output,
        "usage": {"input_tokens": 8, "output_tokens": 2, "total_tokens": 10}}, request=request)


def test_openai_responses_sdk_tool_conversation_roundtrip():
    requests = []

    def handler(request):
        requests.append(request)
        return _openai_response(request, tool=len(requests) == 1)

    sdk = OpenAI(api_key="test", base_url="https://mock/v1",
                 http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    result = ToolConversationController().run(OpenAIProvider(sdk, "model-x"),
        GenerationRequest((TextMessage("user", "calculate"),)), registry=build_default_registry(),
        scope=ToolScope("test", frozenset({ToolCapability.CALCULATE})), operation=_UnlimitedOperation())
    second = json.loads(requests[1].content)["input"]
    assert result.text == "4"
    assert any(item.get("type") == "function_call" and item["call_id"] == "call_sdk"
               for item in second)
    assert any(item.get("type") == "function_call_output" and item["call_id"] == "call_sdk"
               and '"result":"4"' in item["output"] for item in second)
    sdk.close()


def test_openai_compatible_chat_sdk_tool_conversation_roundtrip():
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            body = {"id": "chat_1", "object": "chat.completion", "model": "m",
                "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                    "role": "assistant", "content": None, "tool_calls": [{"id": "call_chat",
                    "type": "function", "function": {"name": "advisor_calculate",
                    "arguments": '{"expression":"2 + 2"}'}}]}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}}
        else:
            body = {"id": "chat_2", "object": "chat.completion", "model": "m",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {
                    "role": "assistant", "content": "4"}}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 1, "total_tokens": 9}}
        return httpx2.Response(200, json=body, request=request)

    sdk = OpenAI(api_key="test", base_url="https://mock/v1",
                 http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    result = ToolConversationController().run(OpenAICompatibleProvider(sdk, "m"),
        GenerationRequest((TextMessage("user", "calculate"),)), registry=build_default_registry(),
        scope=ToolScope("test", frozenset({ToolCapability.CALCULATE})), operation=_UnlimitedOperation())
    messages = json.loads(requests[1].content)["messages"]
    assert result.text == "4"
    assert messages[1]["tool_calls"][0]["id"] == "call_chat"
    assert messages[2]["tool_call_id"] == "call_chat"
    assert '"result":"4"' in messages[2]["content"]
    sdk.close()


def test_anthropic_messages_sdk_tool_conversation_roundtrip():
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            body = {"id": "msg_sdk", "type": "message", "role": "assistant", "model": "claude",
                    "content": [{"type": "tool_use", "id": "tool_sdk", "name": "calculator",
                                 "input": {"expression": "2 + 2"}}],
                    "stop_reason": "tool_use", "usage": {"input_tokens": 8, "output_tokens": 2}}
        else:
            body = {"id": "msg_sdk2", "type": "message", "role": "assistant", "model": "claude",
                    "content": [{"type": "text", "text": "4"}],
                    "stop_reason": "end_turn", "usage": {"input_tokens": 10, "output_tokens": 1}}
        return httpx.Response(200, json=body, request=request)

    sdk = Anthropic(api_key="test", base_url="https://mock",
                    http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = ToolConversationController().run(AnthropicProvider(sdk, "claude"),
        GenerationRequest((TextMessage("user", "calculate"),)), registry=build_default_registry(),
        scope=ToolScope("test", frozenset({ToolCapability.CALCULATE})), operation=_UnlimitedOperation())
    second = json.loads(requests[1].content)["messages"]
    assert result.text == "4"
    assert second[1]["role"] == "assistant"
    assert second[1]["content"][0]["type"] == "tool_use"
    assert second[2]["role"] == "user"
    assert second[2]["content"][0]["tool_use_id"] == "tool_sdk"
    sdk.close()


def test_google_genai_sdk_function_call_roundtrip():
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            body = {"candidates": [{"content": {"role": "model", "parts": [
                {"functionCall": {"id": "gcall", "name": "advisor_calculate",
                                  "args": {"expression": "2 + 2"}}}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 8, "candidatesTokenCount": 2}}
        else:
            body = {"candidates": [{"content": {"role": "model", "parts": [{"text": "4"}]},
                                    "finishReason": "STOP"}],
                    "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 1}}
        return httpx.Response(200, json=body, request=request)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    sdk = genai.Client(api_key="test", http_options=types.HttpOptions(httpx_client=http))
    result = ToolConversationController().run(GoogleProvider(sdk, "gemini"),
        GenerationRequest((TextMessage("user", "calculate"),)), registry=build_default_registry(),
        scope=ToolScope("test", frozenset({ToolCapability.CALCULATE})), operation=_UnlimitedOperation())
    second = json.loads(requests[1].content)["contents"]
    assert result.text == "4"
    assert second[1]["role"] == "model"
    assert second[1]["parts"][0]["functionCall"]["id"] == "gcall"
    assert second[2]["role"] == "user"
    assert second[2]["parts"][0]["functionResponse"]["id"] == "gcall"
    http.close()
