"""Deterministic test doubles. No models, no network, no randomness across runs."""

import hashlib
import json
import re
import threading
from datetime import UTC, datetime, timedelta

import numpy as np

from app.llm.base import GenerationResult, Usage, normalize_message


class FakeEmbedder:
    """Implements the Embedder protocol. Vectors are deterministic from text."""

    def __init__(self, dim: int = 8) -> None:
        self.dim = dim

    def _vector(self, text: str) -> np.ndarray:
        digest = hashlib.sha256(text.encode("utf-8")).digest()[:8]
        rng = np.random.default_rng(int.from_bytes(digest, "big"))
        vec = rng.standard_normal(self.dim).astype(np.float32)
        norm = float(np.linalg.norm(vec))
        if norm == 0.0:
            vec[0] = 1.0
            norm = 1.0
        return (vec / norm).astype(np.float32)

    def count_tokens(self, text: str) -> int:
        return whitespace_tokens(text)

    def chunk_tokens(self, text: str) -> int:
        return whitespace_tokens(text)

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self.dim), dtype=np.float32)
        return np.vstack([self._vector(text) for text in texts]).astype(np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self._vector(text)


def embed_other_model(text: str, dim: int) -> np.ndarray:
    """A second, differently-dimensioned embedding for cross-model tests."""
    digest = hashlib.sha256(("other:" + text).encode("utf-8")).digest()[:8]
    rng = np.random.default_rng(int.from_bytes(digest, "big"))
    vec = rng.standard_normal(dim).astype(np.float32)
    return (vec / np.linalg.norm(vec)).astype(np.float32)


def whitespace_tokens(text: str) -> int:
    return len(text.split())


class FakeProvider:
    """Deterministic stand-in for the LLM provider. Records the last messages."""

    def __init__(
        self,
        text: str = "An answer citing [1] the context.",
        *,
        gate=None,
        on_call=None,
    ) -> None:
        self.text = text
        self.gate = gate
        self.on_call = on_call
        self.last_messages: list[dict[str, str]] = []
        self.calls = 0

    def generate(self, request) -> GenerationResult:
        import contextlib

        slot = self.gate.acquire(request) if self.gate is not None else contextlib.nullcontext(request)
        with slot as active:
            if self.on_call is not None:
                self.on_call(active)
            self.calls += 1
            self.last_messages = [normalize_message(m) for m in active.messages]
            words = sum(len(str(m.get("content", "")).split()) for m in self.last_messages)
            usage = Usage(words, len(self.text.split()), source="estimated")
            research = research_context(active)
            if research is not None:
                return GenerationResult(tool_calls=(research_step(research, self.text, self.calls,
                                                                  active.tools),),
                                        finish_reason="tool_calls", usage=usage)
            return GenerationResult(text=self.text, finish_reason="stop", usage=usage)


def research_context(request) -> dict | None:
    """The research round's context, or None for any other kind of request."""
    if len(request.messages) < 2:
        return None
    try:
        context = json.loads(normalize_message(request.messages[1]).get("content") or "")
    except (TypeError, ValueError):
        return None
    return context if isinstance(context, dict) and "exact_evidence_untrusted" in context else None


def research_step(context: dict, text: str, call: int, tools=()):
    """The next research action of a model that looks the question up and answers with `text`.

    It searches the question once, reads the first passage that search found,
    then finishes: a fact citing everything it read, or "insufficient evidence"
    when it read nothing. It asks only for tools the request offers.
    """
    from app.llm.base import ToolCall

    evidence = [item["evidence_id"] for item in context["exact_evidence_untrusted"]]
    searched = any(action["tool"] == "advisor_search" for action in context["completed_actions"])
    readable = [handle["handle"] for handle in context["issued_handles"]
                if handle["kind"] in {"document", "section"}]
    offered = {tool.name for tool in tools}
    if not context["finish_required"] and not evidence:
        if not searched and "advisor_search" in offered:
            return ToolCall(f"search-{call}", "advisor_search",
                            {"query": context["question"][:300], "kind": "any"})
        if readable and "advisor_read_document" in offered:
            return ToolCall(f"read-{call}", "advisor_read_document", {"handle": readable[0]})
    if evidence:
        # Research cites by evidence ID; the server numbers the citations.
        fact = re.sub(r"\s*\[\d+\]", "", text).strip()
        decision = {"outcome": "information", "completeness": "complete",
                    "blocks": [{"kind": "fact", "text": fact, "evidence_ids": evidence}]}
    else:
        decision = {"outcome": "insufficient_evidence", "completeness": "partial",
                    "blocks": [{"kind": "limitation",
                                "text": "The available sources do not answer this."}],
                    "unresolved_questions": ["The sources in scope do not cover this question."]}
    return ToolCall(f"finish-{call}", "advisor_finish", {"decision": decision})


class ScriptedProvider:
    """Provider double that returns one scripted `GenerationResult` per call.

    Unlike `FakeProvider`, this implements the typed `generate` seam, so tests can
    exercise finish reasons, provider refusals, tool calls, and usage accounting
    without redefining a provider in each file. Requests are recorded for
    inspection.

    Pass ``results`` to script a sequence: each call takes the next result, and
    the last one repeats once the sequence is exhausted.
    """

    def __init__(
        self,
        text: str = "An answer citing [1] the context.",
        *,
        finish_reason: str = "stop",
        refusal: str | None = None,
        tool_calls=(),
        usage: Usage | None = None,
        model: str | None = None,
        results: list[GenerationResult] | None = None,
    ) -> None:
        if usage is None:
            usage = Usage(prompt_tokens=8, completion_tokens=8, source="provider")
        self.result = GenerationResult(
            text=text,
            tool_calls=tuple(tool_calls),
            finish_reason=finish_reason,
            usage=usage,
            refusal=refusal,
            model=model,
        )
        self.results = list(results) if results else None
        self.requests: list = []

    def generate(self, request):
        self.requests.append(request)
        if self.results:
            index = min(len(self.requests) - 1, len(self.results) - 1)
            return self.results[index]
        return self.result


class ArtifactStageProvider:
    """Scripted provider for the artifact pipeline's terminal stage tools.

    ``outline`` is one StageOutline payload (or a list, consumed in order).
    ``sections`` maps a section id to a list of StageSection payloads, so a test
    can script a refusal followed by a correction. Each response is one tool
    call; the last scripted payload repeats when exhausted.
    """

    def __init__(self, *, outline=None, sections=None, edits=None,
                 usage: Usage | None = None, model: str = "fake-stage"):
        self.outline = outline if isinstance(outline, list) else [outline] if outline else []
        self.sections = {key: list(value) for key, value in (sections or {}).items()}
        self.edits = edits if isinstance(edits, list) else [edits] if edits else []
        self.usage = usage or Usage(100, 30, source="provider")
        self.model = model
        self.requests: list = []
        self._counts: dict[str, int] = {}
        self._calls = 0
        self._last_section: str | None = None
        self._last_section_payload: dict | None = None

    def generate(self, request):
        import json

        from app.generation.native_tools import EDITS_TOOL, OUTLINE_TOOL, SECTION_TOOL
        from app.llm.base import ToolCall, normalize_message

        self.requests.append(request)
        tool = request.tools[0].name if request.tools else ""
        last = normalize_message(request.messages[-1]).get("content")
        try:
            payload = json.loads(last)
        except (TypeError, ValueError):
            # A synthetic continuation turn is not the JSON stage payload; the
            # last real payload is still the section being written.
            payload = self._last_section_payload or {}
        section_id = (payload.get("section") or {}).get("id") if tool == SECTION_TOOL else None
        if section_id is not None:
            self._last_section = section_id
            self._last_section_payload = payload
        elif tool == SECTION_TOOL:
            section_id = self._last_section
            payload = self._last_section_payload or payload
        key = f"{tool}:{section_id or ''}"
        count = self._counts.get(key, 0)
        self._counts[key] = count + 1
        if tool == OUTLINE_TOOL:
            arguments = self.outline[min(count, len(self.outline) - 1)]
        elif tool == EDITS_TOOL:
            arguments = self.edits[min(count, len(self.edits) - 1)]
        elif tool == SECTION_TOOL:
            outputs = self.sections[section_id]
            arguments = outputs[min(count, len(outputs) - 1)]
            if isinstance(arguments, GenerationResult):
                # A scripted truncated reply: the controller should reprompt it.
                return arguments
        else:  # pragma: no cover - the pipeline offers only these three tools
            raise AssertionError(f"unexpected stage tool {tool!r}")
        if callable(arguments):
            arguments = arguments(payload)
        call_id, self._calls = self._calls, self._calls + 1
        return GenerationResult(
            tool_calls=(ToolCall(f"{tool}-{call_id}", tool, arguments),),
            finish_reason="tool_calls", usage=self.usage, model=self.model,
        )


class FakeClock:
    def __init__(self):
        self.value = datetime(2026, 10, 7, tzinfo=UTC)
        self.slept: list[float] = []
        self._lock = threading.Lock()
        self._listeners: list = []

    def __call__(self):
        with self._lock:
            return self.value

    def advance(self, seconds):
        with self._lock:
            self.value += timedelta(seconds=seconds)
            listeners = tuple(self._listeners)
        for listener in listeners:
            listener()

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.slept.append(seconds)
            self.value += timedelta(seconds=seconds)
            listeners = tuple(self._listeners)
        for listener in listeners:
            listener()

    def subscribe(self, listener):
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe():
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe
