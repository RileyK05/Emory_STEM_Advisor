"""Local Hugging Face provider for cheap experimentation.

Loads a small model (default: MiniCPM-2B-128k, Apache-2.0) via transformers
and answers through the Provider interface. Weights download from Hugging
Face on first use and are cached in the standard HF cache, so there is no
per-machine install step beyond pip.

Generation is greedy (do_sample=False) by default: experiment runs against
the eval set should be reproducible, not creative.
"""

import threading
import time
from queue import Empty, Full, Queue
from typing import NamedTuple

import torch
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    StoppingCriteria,
    StoppingCriteriaList,
    TextIteratorStreamer,
)

from app.config import Settings
from app.llm.base import (
    GenerationRequest,
    GenerationResult,
    InvalidRequestError,
    Message,
    ProviderTimeoutError,
    ProviderUnavailableError,
    StreamEvent,
    Usage,
    normalize_message,
)

# Lazy cache; separate model/device requests never share the wrong weights.
_models = {}
_generation_slots = {}
_load_lock = threading.Lock()
_generation_slots_lock = threading.Lock()
_STREAM_QUEUE_SIZE = 16
_STREAM_POLL_SECONDS = 0.1
_STREAM_BACKPRESSURE_SECONDS = 30.0
_STREAM_JOIN_SECONDS = 0.25
_STREAM_STOP_GRACE_SECONDS = 0.25


def _load(settings: Settings):
    key = _model_key(settings)
    device = key[1]
    cached = _models.get(key)
    if cached is not None:
        return cached
    with _load_lock:
        cached = _models.get(key)
        if cached is None:
            tokenizer = AutoTokenizer.from_pretrained(
                settings.hf_model_id, trust_remote_code=True
            )
            dtype = torch.bfloat16 if device == "cuda" else torch.float32
            model = AutoModelForCausalLM.from_pretrained(
                settings.hf_model_id,
                torch_dtype=dtype,
                device_map=device,
                trust_remote_code=True,  # MiniCPM ships custom modeling code
            )
            model.eval()
            cached = (tokenizer, model)
            _models[key] = cached
            with _generation_slots_lock:
                _generation_slots.setdefault(key, threading.Lock())
    return cached


def _model_key(settings):
    device = "cuda" if torch.cuda.is_available() else "cpu"
    return (settings.hf_model_id, device)


def _generation_slot(settings):
    """Return the single-flight lock for this cached model/device pair."""
    key = _model_key(settings)
    with _generation_slots_lock:
        return _generation_slots.setdefault(key, threading.Lock())


def _acquire_generation_slot(slot, *, deadline, cancel_requested=None):
    """Wait only while the request remains live; release if it expires at acquire."""
    while True:
        if cancel_requested is not None and cancel_requested.is_set():
            return False
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            return False
        timeout = _STREAM_POLL_SECONDS if cancel_requested is not None else remaining
        if remaining is not None and timeout is not None:
            timeout = min(timeout, remaining)
        acquired = slot.acquire() if timeout is None else slot.acquire(timeout=timeout)
        if not acquired:
            continue
        if ((cancel_requested is not None and cancel_requested.is_set())
                or (deadline is not None and time.monotonic() >= deadline)):
            slot.release()
            return False
        return True


def _slot_timeout_error(message="hf-local generation timed out"):
    error = ProviderTimeoutError(message, provider="hf-local")
    error.usage = Usage(source="unknown")
    return error


class _Completion(NamedTuple):
    text: str
    prompt_tokens: int
    completion_tokens: int
    finish_reason: str


class HFLocalProvider:
    def __init__(self, settings: Settings, timeout_seconds: float = 60.0):
        self._settings = settings
        self._timeout_seconds = timeout_seconds
        self._stop_ids: tuple | None = None  # (tokenizer, model, resolved ids)

    def _stop_token_ids(self, tokenizer, model) -> tuple[int, ...]:
        """Resolve once per loaded model; the vocabulary lookup copies the whole vocab."""
        cached = self._stop_ids
        if cached is None or cached[0] is not tokenizer or cached[1] is not model:
            cached = (tokenizer, model, _resolve_stop_token_ids(tokenizer, model, self._settings))
            self._stop_ids = cached
        return cached[2]

    def generate(self, request: GenerationRequest) -> GenerationResult:
        messages = self._validate_request(request)
        completion = self._complete(
            messages, request.max_output_tokens,
            timeout_seconds=request.timeout_seconds or self._timeout_seconds)
        return GenerationResult(
            text=completion.text, finish_reason=completion.finish_reason,
            usage=Usage(completion.prompt_tokens, completion.completion_tokens, source="provider"),
            model=request.model or self._settings.hf_model_id,
        )

    def chat(self, messages: list[Message], *, max_new_tokens: int | None = None) -> str:
        return self._complete(messages, max_new_tokens, timeout_seconds=self._timeout_seconds).text

    def stream(self, request: GenerationRequest):
        messages = self._validate_request(request)
        tokenizer, model = _load(self._settings)
        stop_token_ids = self._stop_token_ids(tokenizer, model)
        inputs, prompt_tokens, output_limit = _prepare_inputs(
            tokenizer, model, messages, request.max_output_tokens, self._settings
        )
        timeout_seconds = request.timeout_seconds or self._timeout_seconds
        deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else None
        cancel_requested = threading.Event()
        criteria = _DeadlineStoppingCriteria(deadline, cancel_requested)
        streamer = _BoundedTextIteratorStreamer(
            tokenizer, cancel_requested, deadline=deadline,
            queue_size=_STREAM_QUEUE_SIZE, poll_seconds=_STREAM_POLL_SECONDS,
            backpressure_seconds=_STREAM_BACKPRESSURE_SECONDS,
        )
        generation = _StreamedGeneration(
            model=model, inputs=inputs, prompt_tokens=prompt_tokens, output_limit=output_limit,
            stop_token_ids=stop_token_ids, slot=_generation_slot(self._settings), deadline=deadline,
            cancel_requested=cancel_requested, criteria=criteria, streamer=streamer,
        )
        pieces: list[str] = []
        completed = False
        generation.thread.start()
        try:
            while True:
                try:
                    chunk = streamer.text_queue.get(timeout=_STREAM_POLL_SECONDS)
                except Empty:
                    if generation.done.is_set() and streamer.text_queue.empty():
                        break
                    self._enforce_deadline(deadline, cancel_requested)
                    continue
                if chunk is streamer.stop_signal:
                    break
                if chunk:
                    pieces.append(chunk)
                    yield StreamEvent("text_delta", text=chunk)

            while not generation.done.is_set():
                self._enforce_deadline(deadline, cancel_requested)
                generation.done.wait(_STREAM_POLL_SECONDS)
            generation.thread.join(timeout=_STREAM_JOIN_SECONDS)
            if generation.error is not None:
                raise self._stream_error(generation.error) from None
            if generation.thread.is_alive() or generation.generated is None:
                self._raise_incomplete_timeout()
            usage = Usage(prompt_tokens, generation.completion_tokens, source="provider")
            if criteria.expired or (deadline is not None and time.monotonic() >= deadline):
                error = ProviderTimeoutError("hf-local generation timed out", provider="hf-local")
                error.usage = usage
                raise error
            result = GenerationResult(
                text="".join(pieces).strip(),
                finish_reason=_finish_reason(generation.generated, output_limit, stop_token_ids),
                usage=usage,
                model=request.model or self._settings.hf_model_id,
            )
            completed = True
            yield StreamEvent("completed", result=result)
        except GeneratorExit:
            cancel_requested.set()
            raise
        finally:
            if not completed:
                cancel_requested.set()
            generation.thread.join(timeout=_STREAM_JOIN_SECONDS)

    def _enforce_deadline(self, deadline, cancel_requested: threading.Event) -> None:
        """Past the deadline, ask the producer to stop; if it ignores that for the grace period, give up."""
        if deadline is None or time.monotonic() < deadline:
            return
        cancel_requested.set()
        if time.monotonic() >= deadline + _STREAM_STOP_GRACE_SECONDS:
            self._raise_incomplete_timeout()

    @staticmethod
    def _stream_error(error: Exception) -> Exception:
        """The provider error to surface for a failure on the producer thread."""
        if isinstance(error, _StreamBackpressureTimeout):
            mapped = ProviderTimeoutError(
                "hf-local stream consumer exceeded the backpressure limit", provider="hf-local")
        elif isinstance(error, ProviderTimeoutError):
            mapped = error
        else:
            mapped = ProviderUnavailableError("hf-local generation failed", provider="hf-local")
        mapped.usage = Usage(source="unknown")
        return mapped

    def _validate_request(self, request: GenerationRequest) -> list[dict]:
        if request.model is not None and request.model != self._settings.hf_model_id:
            raise InvalidRequestError("request model differs from the loaded local model", provider="hf-local")
        if request.reasoning_effort is not None:
            raise InvalidRequestError("reasoning effort is unsupported by hf-local", provider="hf-local")
        if request.tools or request.response_schema:
            raise InvalidRequestError("hf-local does not support native tools or constrained output", provider="hf-local")
        if request.temperature not in (None, 0):
            raise InvalidRequestError("hf-local runs greedy generation only", provider="hf-local")
        try:
            return [normalize_message(message) for message in request.messages]
        except (TypeError, ValueError):
            raise InvalidRequestError("hf-local messages are malformed", provider="hf-local") from None

    @staticmethod
    def _raise_incomplete_timeout() -> None:
        error = ProviderTimeoutError("hf-local generation timed out", provider="hf-local")
        # A live producer may still be inside a model forward pass. Observed
        # streamer tokens are only a lower bound until generate() returns.
        error.usage = Usage(source="unknown")
        raise error

    def _complete(self, messages: list[Message], max_new_tokens: int | None,
                  *, timeout_seconds: float | None = None) -> _Completion:
        tokenizer, model = _load(self._settings)
        stop_token_ids = self._stop_token_ids(tokenizer, model)
        inputs, prompt_tokens, output_limit = _prepare_inputs(
            tokenizer, model, messages, max_new_tokens, self._settings
        )
        deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else None
        criteria = StoppingCriteriaList([_DeadlineStoppingCriteria(deadline)]) if deadline else None
        slot = _generation_slot(self._settings)
        if not _acquire_generation_slot(slot, deadline=deadline):
            raise _slot_timeout_error()
        try:
            if deadline is not None and time.monotonic() >= deadline:
                raise _slot_timeout_error()
            with torch.no_grad():
                output = model.generate(
                    **inputs, max_new_tokens=output_limit, do_sample=False,
                    stopping_criteria=criteria,
                    **_eos_generation_kwargs(stop_token_ids),
                )
        finally:
            slot.release()
        generated = output[0][prompt_tokens:]
        completion_tokens = int(generated.shape[0])
        if criteria and (criteria[0].expired
                         or (deadline is not None and time.monotonic() >= deadline)):
            error = ProviderTimeoutError("hf-local generation timed out", provider="hf-local")
            error.usage = Usage(prompt_tokens, completion_tokens, source="provider")
            raise error
        text = tokenizer.decode(generated, skip_special_tokens=True).strip()
        return _Completion(text, prompt_tokens, completion_tokens,
                           _finish_reason(generated, output_limit, stop_token_ids))


class _StreamedGeneration:
    """One `model.generate()` on a worker thread, feeding a bounded streamer."""

    def __init__(self, *, model, inputs, prompt_tokens, output_limit, stop_token_ids, slot,
                 deadline, cancel_requested, criteria, streamer):
        self.model, self.inputs = model, inputs
        self.prompt_tokens, self.output_limit = prompt_tokens, output_limit
        self.stop_token_ids, self.slot = stop_token_ids, slot
        self.deadline, self.cancel_requested = deadline, cancel_requested
        self.criteria, self.streamer = criteria, streamer
        self.done = threading.Event()
        self.error: Exception | None = None
        self.completion_tokens: int | None = None
        self.generated = None  # set only when generate() returned
        self.thread = threading.Thread(target=self._run, name="hf-local-generation", daemon=True)

    def _past_deadline(self) -> bool:
        return self.deadline is not None and time.monotonic() >= self.deadline

    def _run(self) -> None:
        acquired = False
        try:
            acquired = _acquire_generation_slot(
                self.slot, deadline=self.deadline, cancel_requested=self.cancel_requested,
            )
            if not acquired or self.cancel_requested.is_set() or self._past_deadline():
                if not self.cancel_requested.is_set():
                    self.error = _slot_timeout_error()
                return
            with torch.no_grad():
                output = self.model.generate(
                    **self.inputs,
                    max_new_tokens=self.output_limit,
                    do_sample=False,
                    stopping_criteria=StoppingCriteriaList([self.criteria]),
                    streamer=self.streamer,
                    **_eos_generation_kwargs(self.stop_token_ids),
                )
            generated = output[0][self.prompt_tokens:]
            self.completion_tokens = int(generated.shape[0])
            self.generated = generated
        except Exception as exc:  # noqa: BLE001 - isolate model-runtime exceptions
            self.error = exc
            if isinstance(exc, _StreamBackpressureTimeout):
                self.cancel_requested.set()
        finally:
            if acquired:
                self.slot.release()
            if not self.streamer.ended:
                try:
                    self.streamer.end()
                except (Full, _StreamBackpressureTimeout):
                    pass
            self.done.set()


class _DeadlineStoppingCriteria(StoppingCriteria):
    def __init__(self, deadline: float | None, cancel_requested: threading.Event | None = None):
        self.deadline = deadline
        self.cancel_requested = cancel_requested
        self.expired = False

    def __call__(self, input_ids, scores, **kwargs):
        self.expired = self.deadline is not None and time.monotonic() >= self.deadline
        return self.expired or (
            self.cancel_requested is not None and self.cancel_requested.is_set()
        )


class _StreamBackpressureTimeout(RuntimeError):
    pass


class _BoundedTextIteratorStreamer(TextIteratorStreamer):
    """TextIteratorStreamer with bounded buffering and cancellable backpressure."""

    def __init__(self, tokenizer, cancel_requested, *, deadline, queue_size,
                 poll_seconds, backpressure_seconds):
        super().__init__(
            tokenizer, skip_prompt=True, timeout=poll_seconds,
            skip_special_tokens=True,
        )
        self.text_queue = Queue(maxsize=queue_size)
        self.cancel_requested = cancel_requested
        self.deadline = deadline
        self.poll_seconds = poll_seconds
        self.backpressure_seconds = backpressure_seconds
        self.ended = False

    def on_finalized_text(self, text: str, stream_end: bool = False):
        if self.cancel_requested.is_set():
            return
        if text:
            self._put(text)
        if stream_end:
            self.ended = True
            self._put(self.stop_signal)

    def _put(self, value):
        started = time.monotonic()
        while not self.cancel_requested.is_set():
            try:
                self.text_queue.put(value, timeout=self.poll_seconds)
                return
            except Full:
                if self.deadline is not None and time.monotonic() >= self.deadline:
                    raise _StreamBackpressureTimeout("hf-local stream deadline elapsed")
                if time.monotonic() - started >= self.backpressure_seconds:
                    raise _StreamBackpressureTimeout("hf-local stream consumer is too slow")


def _prepare_inputs(tokenizer, model, messages, max_new_tokens, settings):
    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(
        prompt,
        return_tensors="pt",
        truncation=False,
        return_attention_mask=True,
    ).to(model.device)
    prompt_tokens = int(inputs["input_ids"].shape[1])
    if prompt_tokens >= settings.hf_max_context:
        raise InvalidRequestError("input exceeds the configured local context window", provider="hf-local")
    output_limit = settings.hf_max_new_tokens if max_new_tokens is None else max_new_tokens
    if output_limit < 1:
        raise InvalidRequestError("max_output_tokens must be positive", provider="hf-local")
    output_limit = min(output_limit, settings.hf_max_context - prompt_tokens)
    return inputs, prompt_tokens, output_limit


def _resolve_stop_token_ids(tokenizer, model, settings) -> tuple[int, ...]:
    """Combine model EOS ids with exact configured vocabulary tokens."""
    generation_config = getattr(model, "generation_config", None)
    eos_value = getattr(generation_config, "eos_token_id", None)
    if eos_value is None:
        eos_value = getattr(getattr(model, "config", None), "eos_token_id", None)
    if eos_value is None:
        eos_ids = ()
    elif isinstance(eos_value, int) and not isinstance(eos_value, bool):
        eos_ids = (eos_value,)
    elif isinstance(eos_value, (tuple, list)) and all(
        isinstance(value, int) and not isinstance(value, bool) for value in eos_value
    ):
        eos_ids = tuple(eos_value)
    else:
        raise ProviderUnavailableError(
            "hf-local model has an invalid EOS token configuration", provider="hf-local"
        )

    configured = getattr(settings, "hf_stop_token_strings", ()) or ()
    if not configured:
        return tuple(dict.fromkeys(eos_ids))
    try:
        vocabulary = tokenizer.get_vocab()
        unknown_id = getattr(tokenizer, "unk_token_id", None)
        ids = list(eos_ids)
        for stop_token in configured:
            token_id = vocabulary.get(stop_token)
            if (not isinstance(token_id, int) or isinstance(token_id, bool)
                    or token_id == unknown_id):
                raise ValueError
            if tokenizer.convert_ids_to_tokens(token_id) != stop_token:
                raise ValueError
            ids.append(token_id)
    except Exception:  # noqa: BLE001 - tokenizer vocabulary is a provider boundary
        raise ProviderUnavailableError(
            "hf-local configured stop token is unavailable in the model tokenizer",
            provider="hf-local",
        ) from None
    return tuple(dict.fromkeys(ids))


def _eos_generation_kwargs(stop_token_ids: tuple[int, ...]) -> dict:
    return {"eos_token_id": list(stop_token_ids)} if stop_token_ids else {}


def _finish_reason(generated, output_limit: int, stop_token_ids: tuple[int, ...]) -> str:
    if generated.shape[0] and int(generated[-1]) in stop_token_ids:
        return "stop"
    return "length" if int(generated.shape[0]) >= output_limit else "stop"
