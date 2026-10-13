import json

import httpx
import httpx2
import pytest
from anthropic import Anthropic
from google import genai
from google.genai import types
from openai import OpenAI

from app.llm.base import GenerationRequest, InvalidRequestError, Usage
from app.llm.batch import (
    DEFAULT_MAX_JSONL_BYTES,
    BatchItem,
    BatchItemResult,
    encode_jsonl,
    iter_jsonl,
    iter_jsonl_chunks,
    read_bounded_jsonl,
)
from app.llm.batches import (
    AnthropicBatchProvider,
    GoogleBatchProvider,
    OpenAIBatchProvider,
    _complete_results,
)
from app.llm.factory import _GoogleBatchFileDownloader


def _request(text):
    return GenerationRequest([{"role": "user", "content": text}], max_output_tokens=24)


def _item(key, text):
    return BatchItem(key, _request(text))


def _http_response(req, body, headers=None):
    return httpx.Response(200, json=body, headers=headers or {}, request=req)

def _http2_response(req, body, headers=None):
    return httpx2.Response(200, json=body, headers=headers or {}, request=req)


def test_jsonl_helpers_bound_bytes_records_and_validate_objects():
    content = encode_jsonl([{"custom_id": "one", "body": {"text": "hi"}}], max_bytes=100)
    assert next(iter(iter_jsonl(content, max_bytes=100)))["custom_id"] == "one"
    assert read_bounded_jsonl(iter((content[:4], content[4:])), max_bytes=100)[0]["custom_id"] == "one"
    with pytest.raises(ValueError, match="byte limit"):
        encode_jsonl([{"x": "oversized"}], max_bytes=1)
    with pytest.raises(ValueError, match="invalid JSONL"):
        list(iter_jsonl(b"{bad}\n"))
    with pytest.raises(TypeError, match="must be an object"):
        list(iter_jsonl(b"[]\n"))
    with pytest.raises(ValueError, match="byte limit"):
        list(iter_jsonl(b"x" * (DEFAULT_MAX_JSONL_BYTES + 1)))


def test_batch_item_results_preserve_usage_and_require_success_or_error():
    assert BatchItemResult("id", error="failed").usage.source == "unknown"
    with pytest.raises(ValueError, match="exactly one"):
        BatchItemResult("id")


def test_openai_batch_uses_real_sdk_upload_and_batch_endpoints():
    requests = []
    output = (json.dumps({"custom_id":"second","response":{"status_code":200,"body":{
        "id":"r2","status":"completed","model":"gpt-test","output":[
          {"type":"message","content":[{"type":"output_text","text":"done"}]}],
        "usage":{"input_tokens":5,"output_tokens":2,"total_tokens":7}}},"error":None}) + "\n").encode()
    error_output = (json.dumps({"custom_id":"first","error":{"code":"invalid_request_error"}})+"\n").encode()

    def handler(req):
        body = req.read()
        requests.append((req, body))
        if req.url.path == "/v1/files":
            return httpx2.Response(200, json={"id":"file-in","object":"file","bytes":len(body),
                "created_at":1,"filename":"batch.jsonl","purpose":"batch"}, request=req)
        if req.url.path == "/v1/batches":
            return _http2_response(req, {"id":"batch-1","object":"batch","endpoint":"/v1/responses",
                "input_file_id":"file-in","completion_window":"24h","status":"validating",
                "created_at":1,"request_counts":{"total":1,"completed":0,"failed":0}})
        if req.method == "POST" and req.url.path == "/v1/batches/batch-1/cancel":
            return _http2_response(req, {"id":"batch-1","object":"batch","endpoint":"/v1/responses",
                "input_file_id":"file-in","completion_window":"24h","status":"cancelling",
                "created_at":1,"request_counts":{"total":2,"completed":1,"failed":1}})
        if req.url.path == "/v1/batches/batch-1":
            return _http2_response(req, {"id":"batch-1","object":"batch","endpoint":"/v1/responses",
                "input_file_id":"file-in","output_file_id":"file-out","error_file_id":"file-error","completion_window":"24h",
                "status":"completed","created_at":1,"request_counts":{"total":2,"completed":1,"failed":1}})
        if req.url.path in {"/v1/files/file-out/content", "/v1/files/file-error/content"}:
            data = output if req.url.path.endswith("file-out/content") else error_output
            return httpx2.Response(200, content=data, headers={"content-type":"application/octet-stream"}, request=req)
        raise AssertionError(f"unexpected path {req.url.path}")

    sdk = OpenAI(api_key="sk-test", base_url="https://mock/v1",
                 http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    provider = OpenAIBatchProvider(sdk, "gpt-test")
    submitted = provider.submit([_item("first", "one"), _item("second", "hello")])
    assert submitted.batch_id == "batch-1" and submitted.input_file_id == "file-in"
    assert b'"custom_id":"second"' in requests[0][1]
    assert b'"url":"/v1/responses"' in requests[0][1]
    progress = provider.status("batch-1")
    assert progress.state == "completed" and progress.total_count == 2 and progress.completed_count == 1 and progress.failed_count == 1
    results = provider.results("batch-1")
    assert [item.correlation_id for item in results] == ["first", "second"]
    assert results[0].error == "provider batch item failed" and results[0].usage.source == "unknown"
    assert results[1].result.text == "done"
    assert results[1].usage == Usage(5, 2, 7, "provider")
    provider.cancel("batch-1")
    assert any(req.method == "POST" and req.url.path.endswith("/cancel") for req, _ in requests)
    sdk.close()


def test_anthropic_batch_real_sdk_serializes_requests_and_result_iterator():
    requests = []

    def handler(req):
        body = req.read()
        requests.append((req, body))
        if req.method == "POST" and req.url.path == "/v1/messages/batches":
            return _http_response(req, {"id":"msgbatch_1","type":"message_batch",
                "processing_status":"in_progress","request_counts":{"processing":1,"succeeded":0,
                "errored":0,"canceled":0,"expired":0},"ended_at":None,"created_at":"2026-10-07T12:00:00Z"})
        if req.method == "POST" and req.url.path == "/v1/messages/batches/msgbatch_1/cancel":
            return _http_response(req, {"id":"msgbatch_1","type":"message_batch",
                "processing_status":"canceling","request_counts":{"processing":0,"succeeded":1,
                "errored":0,"canceled":0,"expired":0},"ended_at":None,"created_at":"2026-10-07T12:00:00Z"})
        if req.url.path == "/v1/messages/batches/msgbatch_1":
            return _http_response(req, {"id":"msgbatch_1","type":"message_batch",
                "processing_status":"ended","request_counts":{"processing":0,"succeeded":1,
                "errored":0,"canceled":0,"expired":0},"ended_at":"2026-10-07T12:00:00Z",
                "created_at":"2026-10-07T12:00:00Z","results_url":"https://mock/v1/messages/batches/msgbatch_1/results"})
        if req.url.path == "/v1/messages/batches/msgbatch_1/results":
            line = {"custom_id":"a","result":{"type":"succeeded","message":{
                "id":"msg_1","type":"message","role":"assistant","model":"claude-test",
                "content":[{"type":"text","text":"done"}],"stop_reason":"end_turn",
                "stop_sequence":None,"usage":{"input_tokens":4,"output_tokens":2}}}}
            return httpx.Response(200, content=(json.dumps(line)+"\n").encode(),
                headers={"content-type":"application/jsonl"}, request=req)
        raise AssertionError(f"unexpected path {req.url.path}")

    sdk = Anthropic(api_key="sk-ant-test", base_url="https://mock",
                    http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    provider = AnthropicBatchProvider(sdk, "claude-test")
    submission = provider.submit([_item("a", "hello")])
    sent = json.loads(requests[0][1])
    assert sent["requests"][0]["custom_id"] == "a"
    assert sent["requests"][0]["params"]["max_tokens"] == 24
    assert provider.status(submission.batch_id).state == "completed"
    result = provider.results(submission.batch_id)[0]
    assert result.correlation_id == "a" and result.result.text == "done"
    assert result.usage == Usage(4, 2, 6, "provider")
    provider.cancel("msgbatch_1")
    assert any(req.method == "POST" and req.url.path.endswith("/cancel") for req, _ in requests)
    sdk.close()


def test_google_batch_real_sdk_serializes_inline_requests_and_results():
    requests = []

    def handler(req):
        body = req.read()
        requests.append((req, body))
        if req.method == "POST" and req.url.path.endswith(":batchGenerateContent"):
            return _http_response(req, {"name":"batches/google-1","model":"gemini-test",
                "metadata":{"state":"JOB_STATE_PENDING"}})
        if req.method == "POST" and req.url.path.endswith("/batches/google-1:cancel"):
            return _http_response(req, {})
        if req.method == "GET" and req.url.path.endswith("/batches/google-1"):
            return _http_response(req, {"name":"batches/google-1","metadata":{"state":"JOB_STATE_SUCCEEDED",
                "output":{"inlinedResponses":{"inlinedResponses":[{
                    "metadata":{"custom_id":"g1"}, "response":{"modelVersion":"gemini-test",
                        "candidates":[{"content":{"role":"model","parts":[{"text":"done"}]},
                        "finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":3,
                        "candidatesTokenCount":1,"totalTokenCount":4}}}]}}}})
        raise AssertionError(f"unexpected {req.method} {req.url.path}")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    sdk = genai.Client(api_key="google-test", http_options=types.HttpOptions(httpx_client=http))
    provider = GoogleBatchProvider(sdk, "gemini-test")
    submitted = provider.submit([_item("g1", "hello")])
    sent = json.loads(requests[0][1])
    assert submitted.state == "pending"
    assert sent["batch"]["inputConfig"]["requests"]["requests"][0]["metadata"]["custom_id"] == "g1"
    progress = provider.status(submitted.batch_id)
    assert progress.state == "completed" and progress.completed_count is None
    result = provider.results(submitted.batch_id)[0]
    assert result.correlation_id == "g1" and result.result.text == "done"
    assert result.usage == Usage(3, 1, 4, "provider")
    provider.cancel("batches/google-1")
    assert any(req.method == "POST" and req.url.path.endswith(":cancel") for req, _ in requests)
    http.close()


def test_batch_providers_reject_duplicate_ids_mismatched_models_and_missing_results():
    fake = type("Fake", (), {"files":None})()
    provider = OpenAIBatchProvider(fake, "m")
    with pytest.raises(ValueError, match="unique"):
        provider.submit([_item("same", "1"), _item("same", "2")])
    with pytest.raises(ValueError, match="one configured model"):
        provider.submit([BatchItem("x", GenerationRequest([{"role":"user","content":"x"}], model="other"))])
    missing = provider._missing(["x"])
    assert missing[0].error is not None and missing[0].usage.source == "unknown"


def test_google_large_batch_uses_uploaded_jsonl_with_correlation_keys():
    requests = []

    def handler(req):
        body = req.read()
        requests.append((req, body))
        if req.method == "POST" and req.url.path == "/upload/v1beta/files":
            return httpx.Response(200, json={"file":{"name":"files/input-1","mimeType":"jsonl"}},
                headers={"x-goog-upload-url":"https://generativelanguage.googleapis.com/upload/v1beta/files/upload-1"},
                request=req)
        if req.method == "POST" and req.url.path.endswith("/upload-1"):
            return httpx.Response(200, json={"file":{"name":"files/input-1","mimeType":"jsonl",
                "displayName":"batch-requests"}}, headers={"x-goog-upload-status":"final"}, request=req)
        if req.method == "POST" and req.url.path.endswith(":batchGenerateContent"):
            return _http_response(req, {"name":"batches/google-file","metadata":{"state":"JOB_STATE_PENDING"}})
        raise AssertionError(f"unexpected {req.method} {req.url}")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    sdk = genai.Client(api_key="google-test", http_options=types.HttpOptions(httpx_client=http))
    provider = GoogleBatchProvider(sdk, "gemini-test")
    provider.max_inline_bytes = 10
    submitted = provider.submit([_item("file-key", "a longer batch request")])
    assert submitted.input_file_id == "files/input-1"
    upload = next(body for request, body in requests if request.method == "POST" and request.url.path.endswith("/upload-1"))
    assert b'"key":"file-key"' in upload
    assert b'"request":' in upload
    batch_body = json.loads(next(body for request, body in requests if request.url.path.endswith(":batchGenerateContent")))
    assert batch_body["batch"]["inputConfig"]["fileName"] == "files/input-1"
    http.close()


def test_result_completion_fills_missing_items_and_rejects_duplicate_or_unknown_ids():
    items = _complete_results([BatchItemResult("done", error="item failed")],
                              ["done", "missing"], "openai")
    assert [item.correlation_id for item in items] == ["done", "missing"]
    assert items[1].error is not None
    with pytest.raises(InvalidRequestError, match="duplicate batch correlation"):
        _complete_results([BatchItemResult("x", error="a"), BatchItemResult("x", error="b")],
                          ["x"], "openai")
    with pytest.raises(InvalidRequestError, match="unknown batch correlation"):
        _complete_results([BatchItemResult("unexpected", error="x")], ["expected"], "openai")


def test_google_file_downloader_streams_authenticated_bounded_jsonl():
    seen = []
    payload = b'{"key":"one","response":{"text":"ok"}}\n'
    def handler(req):
        seen.append(req)
        assert req.url.host == "generativelanguage.googleapis.com"
        assert req.url.path == "/v1beta/files/file-123:download"
        assert req.url.params["alt"] == "media"
        assert req.headers["x-goog-api-key"] == "secret-test-key"
        return httpx.Response(200, stream=httpx.ByteStream(payload),
                              headers={"content-length":str(len(payload))}, request=req)
    http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    downloader = _GoogleBatchFileDownloader("secret-test-key", client=http)
    with downloader("files/file-123", max_bytes=128) as chunks:
        records = tuple(iter_jsonl_chunks(iter(chunks), max_bytes=128))
    assert records == ({"key":"one","response":{"text":"ok"}},)
    assert len(seen) == 1
    http.close()


def test_google_file_batch_results_stream_and_report_only_accounted_records():
    class FileApi:
        def download(self, **_kwargs):
            pytest.fail("unbounded SDK file download must not be used")
    class Batches:
        def get(self, *, name):
            assert name == "batch-file"
            return {"dest":{"file_name":"files/output-1"}}
    class Download:
        def __call__(self, file_name, *, max_bytes):
            from contextlib import contextmanager
            assert file_name == "files/output-1" and max_bytes == 200_000_000
            @contextmanager
            def stream():
                yield iter([json.dumps({"key":"accounted","response":{
                    "candidates":[{"content":{"parts":[{"text":"ready"}]}}],
                    "usageMetadata":{"promptTokenCount":4,"candidatesTokenCount":2,"totalTokenCount":6}
                }}).encode()+b"\n"])
            return stream()
    provider = GoogleBatchProvider(type("SDK", (), {"files":FileApi(),"batches":Batches()})(),
                                   "gemini-test", file_downloader=Download())
    result = provider.results("batch-file")
    assert len(result) == 1
    assert result[0].correlation_id == "accounted" and result[0].result.text == "ready"
    assert result[0].usage == Usage(4, 2, 6, "provider")


def test_google_file_downloader_rejects_announced_oversize_and_bad_resource():
    requested = []
    def handler(req):
        requested.append(req)
        return httpx.Response(200, content=b"", headers={"content-length":"129"}, request=req)
    http = httpx.Client(transport=httpx.MockTransport(handler))
    downloader = _GoogleBatchFileDownloader("secret", client=http)
    with pytest.raises(ValueError, match="byte limit"), downloader("files/file-123", max_bytes=128):
        pytest.fail("oversized body must be rejected before reading")
    with pytest.raises(ValueError, match="invalid file resource"), downloader(
        "https://evil.test/files/file-123", max_bytes=128
    ):
        pytest.fail("arbitrary URLs must never be fetched")
    assert len(requested) == 1
    http.close()


def test_google_chunked_oversize_aborts_stream_and_redirect_is_not_followed():
    class TrackedStream(httpx.SyncByteStream):
        def __init__(self):
            self.closed = False
            self.reads = 0
        def __iter__(self):
            self.reads += 1
            yield b"x" * 80
            self.reads += 1
            yield b"y" * 80
        def close(self):
            self.closed = True

    streams = []
    requests = []
    def handler(req):
        requests.append(req)
        if req.url.path.endswith(":download") and not streams:
            stream = TrackedStream()
            streams.append(stream)
            return httpx.Response(200, stream=stream, request=req)
        return httpx.Response(302, headers={"location":"https://evil.test/steal"}, request=req)
    http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    downloader = _GoogleBatchFileDownloader("secret", client=http)
    with pytest.raises(ValueError, match="byte limit"), downloader(
        "files/file-123", max_bytes=100
    ) as chunks:
        tuple(iter_jsonl_chunks(iter(chunks), max_bytes=100))
    assert streams[0].closed and streams[0].reads == 2
    with pytest.raises(ValueError, match="redirects"), downloader("files/file-123", max_bytes=100):
        pytest.fail("redirects must be rejected")
    assert len(requests) == 2 and all(req.url.host == "generativelanguage.googleapis.com" for req in requests)
    http.close()
