import pytest

from app.generation.controller import (
    GenerationCancelled,
    ToolCallLimitExceeded,
    ToolConversationController,
    _aggregate_usage,
)
from app.llm.base import (
    GenerationRequest,
    GenerationResult,
    ProviderError,
    StreamEvent,
    TextMessage,
    ToolCall,
    Usage,
)
from app.tools.builtins import CalculatorOutput, build_default_registry
from app.tools.registry import (
    ToolCapability,
    ToolInputModel,
    ToolRegistry,
    ToolScope,
    ToolSpec,
)


class Operation:
    def __init__(self, events):
        self.events = events

    def prepare(self, request):
        self.events.append("prepare")
        return request

    def account(self, result, error):
        self.events.append("account")


class Provider:
    def __init__(self, results, events=None):
        self.results = iter(results)
        self.requests = []
        self.events = events if events is not None else []

    def generate(self, request):
        self.requests.append(request)
        self.events.append("provider")
        return next(self.results)


def _scope(*capabilities):
    return ToolScope("test", frozenset(capabilities))


def _custom_registry(handler, output=CalculatorOutput):
    class EmptyInput(ToolInputModel):
        pass

    return ToolRegistry([ToolSpec("test_once", "test", EmptyInput, output,
        ToolCapability.CALCULATE, lambda value, scope: handler(value, scope), max_output_bytes=2048)])


def _call(call_id="call-1", args=None):
    return ToolCall(call_id, "advisor_calculate", args or {"expression": "2 + 2"})


def test_controller_accounts_each_attempt_before_tool_and_returns_only_final_text():
    events = []
    provider = Provider([
        GenerationResult(tool_calls=(_call(),), finish_reason="tool_calls",
                         usage=Usage(10, 3, source="provider")),
        GenerationResult(text="4", finish_reason="stop", usage=Usage(12, 1, source="provider")),
    ], events)
    result = ToolConversationController().run(
        provider, GenerationRequest((TextMessage("user", "calculate"),)),
        registry=build_default_registry(), scope=_scope(ToolCapability.CALCULATE),
        operation=Operation(events),
    )
    assert result.text == "4" and not result.tool_calls
    assert result.usage == Usage(22, 4, 26, "provider")
    assert events.index("account") < events.index("provider", events.index("account") + 1)
    assert len(provider.requests) == 2
    assert provider.requests[1].messages[-1].tool_call_id == "call-1"


def test_one_answer_rewrite_preserves_tool_results_and_charges_all_calls():
    events, resets = [], []
    provider = Provider([
        GenerationResult(tool_calls=(_call(),), finish_reason="tool_calls",
                         usage=Usage(10, 3, source="provider")),
        GenerationResult("Uncited draft", finish_reason="stop", usage=Usage(12, 1, source="provider")),
        GenerationResult("Four [1].", finish_reason="stop", usage=Usage(14, 2, source="provider")),
    ], events)
    result = ToolConversationController().run(provider,
        GenerationRequest((TextMessage("user", "calculate"),)),
        registry=build_default_registry(), scope=_scope(ToolCapability.CALCULATE),
        operation=Operation(events), on_provisional_reset=lambda: resets.append(True),
        final_repair=lambda r: "Rewrite with citations" if "[1]" not in r.text else None)
    assert result.text == "Four [1]."
    assert result.usage.total_tokens == 42
    assert events.count("prepare") == events.count("account") == 3
    assert any(getattr(m, "tool_call_id", None) == "call-1" for m in provider.requests[2].messages)
    assert provider.requests[2].tools == ()
    assert len(resets) == 2  # Tool preamble and invalid draft both reset.


def test_rewrite_cannot_execute_more_tools():
    provider = Provider([
        GenerationResult("Uncited draft", finish_reason="stop"),
        GenerationResult(tool_calls=(_call(),), finish_reason="tool_calls"),
    ])
    from app.generation.controller import ToolConversationError

    with pytest.raises(ToolConversationError, match="rewrite requested an unexecuted tool"):
        ToolConversationController().run(provider,
            GenerationRequest((TextMessage("user", "calculate"),)),
            registry=build_default_registry(), scope=_scope(ToolCapability.CALCULATE),
            operation=Operation([]), final_repair=lambda r: "Rewrite")


def test_text_only_stream_adapter_uses_typed_tool_generation():
    class TextOnlyStreamProvider(Provider):
        stream_supports_tools = False

        def stream(self, request):
            raise AssertionError("text-only decoder cannot execute native tools")

    text = []
    provider = TextOnlyStreamProvider([
        GenerationResult(tool_calls=(_call(),), finish_reason="tool_calls"),
        GenerationResult("Four [1].", finish_reason="stop"),
    ])
    result = ToolConversationController().run(provider,
        GenerationRequest((TextMessage("user", "calculate"),)),
        registry=build_default_registry(), scope=_scope(ToolCapability.CALCULATE),
        operation=Operation([]), on_provisional_text=text.append)
    assert result.text == "Four [1]." and text == ["Four [1]."]
    assert len(provider.requests) == 2


def test_required_tool_cannot_be_replaced_by_model_claim():
    provider = Provider([
        GenerationResult("The tool returned four [1].", finish_reason="stop"),
        GenerationResult(tool_calls=(_call(),), finish_reason="tool_calls"),
        GenerationResult("Four [1].", finish_reason="stop"),
    ])
    result = ToolConversationController().run(provider,
        GenerationRequest((TextMessage("user", "calculate"),)),
        registry=build_default_registry(), scope=_scope(ToolCapability.CALCULATE),
        operation=Operation([]), required_tool_names=frozenset({"advisor_calculate"}))
    assert result.text == "Four [1]."
    assert [r.tool_choice for r in provider.requests] == ["required", "required", None]
    assert any(getattr(m, "tool_call_id", None) == "call-1" for m in provider.requests[-1].messages)


def test_required_tool_budget_cannot_publish_unexecuted_model_plan():
    with pytest.raises(ToolCallLimitExceeded):
        ToolConversationController(max_provider_calls=1).run(
            Provider([GenerationResult("Invented tool result [1].", finish_reason="stop")]),
            GenerationRequest((TextMessage("user", "calculate"),)),
            registry=build_default_registry(), scope=_scope(ToolCapability.CALCULATE),
            operation=Operation([]), required_tool_names=frozenset({"advisor_calculate"}))


def test_last_allowed_call_answers_from_executed_results_instead_of_requesting_tools():
    provider = Provider([
        GenerationResult(tool_calls=(_call(),), finish_reason="tool_calls"),
        GenerationResult("Four [1].", finish_reason="stop"),
    ])
    result = ToolConversationController(max_provider_calls=2).run(provider,
        GenerationRequest((TextMessage("user", "calculate"),)),
        registry=build_default_registry(), scope=_scope(ToolCapability.CALCULATE),
        operation=Operation([]))
    assert result.text == "Four [1]."
    assert provider.requests[0].tools and provider.requests[1].tools == ()
    assert any(getattr(m, "tool_call_id", None) == "call-1" for m in provider.requests[1].messages)


def test_streamed_tool_attempt_is_reset_and_final_attempt_text_survives():
    class StreamingProvider:
        def __init__(self):
            self.calls = 0

        def stream(self, request):
            self.calls += 1
            if self.calls == 1:
                yield StreamEvent("text_delta", text="internal tool planning")
                yield StreamEvent("completed", result=GenerationResult(
                    tool_calls=(_call(),), finish_reason="tool_calls", usage=Usage(7, 2, source="provider"),
                ))
            else:
                yield StreamEvent("text_delta", text="The answer is four.")
                yield StreamEvent("completed", result=GenerationResult(
                    text="The answer is four.", finish_reason="stop", usage=Usage(9, 4, source="provider"),
                ))

    events = []
    attempts = []
    result = ToolConversationController().run(
        StreamingProvider(), GenerationRequest((TextMessage("user", "calculate"),)),
        registry=build_default_registry(), scope=_scope(ToolCapability.CALCULATE),
        operation=Operation([]), on_provisional_text=lambda text: events.append(("text", text)),
        on_provisional_reset=lambda: events.append(("reset", None)),
        on_attempt=lambda request, value, error: attempts.append((value, error)),
    )
    assert result.text == "The answer is four."
    assert events == [("text", "internal tool planning"), ("reset", None),
                      ("text", "The answer is four.")]
    assert len(attempts) == 2 and all(value is not None and error is None for value, error in attempts)


def test_tool_stream_error_accounts_observed_usage_before_propagating():
    events = []
    measured_error = ProviderError("stream interrupted", provider="offline-test")
    measured_error.usage = Usage(6, 3, source="provider")

    class ErrorStream:
        def stream(self, request):
            yield StreamEvent("text_delta", text="unverified partial text")
            yield StreamEvent("error", error=measured_error)

    attempts = []

    class RecordingOperation(Operation):
        def account(self, result, error):
            events.append(("account", result, error))

    def on_attempt(_request, result, error):
        attempts.append((result, error))
        events.append(("audit", result, error))

    with pytest.raises(ProviderError, match="stream interrupted"):
        ToolConversationController().run(
            ErrorStream(), GenerationRequest((TextMessage("user", "calculate"),)),
            registry=build_default_registry(), scope=_scope(ToolCapability.CALCULATE),
            operation=RecordingOperation([]), on_attempt=on_attempt,
            on_provisional_text=lambda _text: None,
        )
    assert len(attempts) == 1 and attempts[0][0] is None
    assert attempts[0][1].usage == Usage(6, 3, source="provider")
    assert [event[0] for event in events] == ["audit", "account"]
    assert events[1][2].usage == Usage(6, 3, source="provider")


def test_repeated_identical_tool_call_id_reuses_prior_result_once():
    executions = []
    registry = _custom_registry(lambda _value, _scope:
        executions.append("run") or CalculatorOutput(result="ok"))
    duplicate = ToolCall("same-id", "test_once", {})
    provider = Provider([
        GenerationResult(tool_calls=(duplicate,)),
        GenerationResult(tool_calls=(duplicate,)),
        GenerationResult(text="done"),
    ])
    result = ToolConversationController(max_provider_calls=4).run(
        provider, GenerationRequest((TextMessage("user", "do"),)), registry=registry,
        scope=_scope(ToolCapability.CALCULATE), operation=Operation([]))
    assert result.text == "done"
    assert executions == ["run"]


def test_changed_arguments_with_reused_call_id_fail_without_second_execution():
    executions = []
    registry = _custom_registry(lambda value, _scope:
        executions.append(value.model_dump()) or CalculatorOutput(result="ok"))
    provider = Provider([
        GenerationResult(tool_calls=(ToolCall("same-id", "test_once", {}),)),
        GenerationResult(tool_calls=(ToolCall("same-id", "test_once", {"x": 1}),)),
    ])
    with pytest.raises(Exception, match="changed its request"):
        ToolConversationController().run(provider, GenerationRequest((TextMessage("user", "do"),)),
            registry=registry, scope=_scope(ToolCapability.CALCULATE), operation=Operation([]))
    assert executions == [{}]


def test_tool_limits_and_cancellation_stop_execution():
    side_effects = []
    registry = _custom_registry(lambda _value, _scope:
        side_effects.append(1) or CalculatorOutput(result="ok"))
    two = GenerationResult(tool_calls=(ToolCall("1", "test_once", {}),
                                       ToolCall("2", "test_once", {})))
    with pytest.raises(ToolCallLimitExceeded):
        ToolConversationController(max_tool_calls=1).run(Provider([two]),
            GenerationRequest((TextMessage("user", "x"),)), registry=registry,
            scope=_scope(ToolCapability.CALCULATE), operation=Operation([]))
    cancelled = lambda: True
    with pytest.raises(GenerationCancelled):
        ToolConversationController().run(Provider([]), GenerationRequest((TextMessage("user", "x"),)),
            registry=registry, scope=_scope(ToolCapability.CALCULATE),
            operation=Operation([]), cancellation=cancelled)
    assert side_effects == []


def test_terminal_tool_returns_receipt_without_an_extra_provider_call():
    registry = _custom_registry(lambda _value, _scope: CalculatorOutput(result="candidate"))
    # The test-only schema is adapted to the exact terminal tool name.
    class Input(ToolInputModel):
        value: str

    registry = ToolRegistry([ToolSpec("stage_candidate", "stage", Input, CalculatorOutput,
        ToolCapability.CALCULATE,
        lambda value, _scope: CalculatorOutput(result=value.value))])
    provider = Provider([GenerationResult(tool_calls=(ToolCall(
        "stage-call", "stage_candidate", {"value": "candidate-json"}),))])
    result = ToolConversationController().run(provider,
        GenerationRequest((TextMessage("user", "draft"),)), registry=registry,
        scope=_scope(ToolCapability.CALCULATE), operation=Operation([]),
        terminal_tool_names=frozenset({"stage_candidate"}))
    assert result.text == '{"result":"candidate-json"}'
    assert result.finish_reason == "stop"
    assert len(provider.requests) == 1


@pytest.mark.parametrize("finish_reason", [
    "length", "max_tokens", "content_filter", "error", "incomplete", "MAX_TOKENS",
])
def test_incomplete_provider_tool_response_is_accounted_but_never_invoked(finish_reason):
    events = []
    side_effects = []
    registry = _custom_registry(lambda _value, _scope:
        side_effects.append(1) or CalculatorOutput(result="bad"))
    provider = Provider([GenerationResult(
        tool_calls=(ToolCall("partial", "test_once", {}),), finish_reason=finish_reason,
        usage=Usage(4, 2, source="provider"))], events)
    with pytest.raises(Exception, match="did not complete"):
        ToolConversationController().run(provider,
            GenerationRequest((TextMessage("user", "do"),)), registry=registry,
            scope=_scope(ToolCapability.CALCULATE), operation=Operation(events))
    assert side_effects == []
    assert "account" in events


def test_usage_aggregation_does_not_treat_missing_counts_as_zero():
    incomplete = Usage(3, 1, source="provider")
    object.__setattr__(incomplete, "completion_tokens", None)
    assert _aggregate_usage([incomplete]).source == "unknown"
