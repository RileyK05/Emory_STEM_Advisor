from uuid import uuid4

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from app.generation.controller import (
    GenerationCancelled,
    ToolConversationController,
    _aggregate_usage,
)
from app.llm.base import (
    GenerationRequest,
    GenerationResult,
    TextMessage,
    ToolCall,
    Usage,
)
from app.tools.builtins import CalculatorOutput, build_default_registry
from app.tools.executions import ToolCallLimitReached
from app.tools.registry import (
    ToolCapability,
    ToolInputModel,
    ToolRegistry,
    ToolScope,
    ToolSpec,
    repair_arguments,
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
    # The fence holds: a model may not run more tools than the operation
    # allows. It is told the budget is spent and answers from what it has, so
    # the request does not die with the answer it already earned.
    provider = Provider([two, GenerationResult("Answered [1].", finish_reason="stop")])
    result = ToolConversationController(max_tool_calls=1).run(provider,
        GenerationRequest((TextMessage("user", "x"),)), registry=registry,
        scope=_scope(ToolCapability.CALCULATE), operation=Operation([]))
    assert result.text == "Answered [1]."
    assert side_effects == []
    cancelled = lambda: True
    with pytest.raises(GenerationCancelled):
        ToolConversationController().run(Provider([]),
            GenerationRequest((TextMessage("user", "x"),)), registry=registry,
            scope=_scope(ToolCapability.CALCULATE), operation=Operation([]),
            cancellation=cancelled)
    assert side_effects == []


def test_a_model_that_asks_for_more_tools_than_the_budget_is_told_and_can_answer():
    """The refused call is answered with a refusal, not with silence.

    The provider protocol needs a result for every tool call, so the model is
    told the budget is spent and asked to answer from the context and the
    tool results it already has.
    """
    side_effects = []
    registry = _custom_registry(lambda _value, _scope:
        side_effects.append(1) or CalculatorOutput(result="ok"))
    two = GenerationResult(tool_calls=(ToolCall("1", "test_once", {}),
                                       ToolCall("2", "test_once", {})))
    provider = Provider([two, GenerationResult("Answered [1].", finish_reason="stop")])
    ToolConversationController(max_tool_calls=1).run(provider,
        GenerationRequest((TextMessage("user", "x"),)), registry=registry,
        scope=_scope(ToolCapability.CALCULATE), operation=Operation([]))
    refusals = [message for message in provider.requests[1].messages
                if getattr(message, "tool_call_id", None) == "1"]
    assert refusals and "budget" in refusals[0].content


def test_a_spent_durable_tool_budget_is_refused_and_the_model_can_answer():
    """An earlier provider attempt may have spent the operation's tool budget.

    The ledger fences the budget durably, so the second attempt on the same
    request finds it spent. That is a fence holding, not a failure: the call
    does not run, the model is told, and it answers from what it has.
    """
    executions = []

    class SpentLedger:
        def claim_or_replay(self, **_kwargs):
            raise ToolCallLimitReached("tool call limit has been reached")

    registry = _custom_registry(lambda _value, _scope:
        executions.append("run") or CalculatorOutput(result="ok"))
    provider = Provider([
        GenerationResult(tool_calls=(ToolCall("1", "test_once", {}),)),
        GenerationResult("Answered [1].", finish_reason="stop"),
    ])
    result = ToolConversationController().run(provider,
        GenerationRequest((TextMessage("user", "do"),)), registry=registry,
        scope=_scope(ToolCapability.CALCULATE), operation=Operation([]),
        ledger=SpentLedger(), operation_id=uuid4(), workspace_key="test",
        scope_fingerprint="a" * 64)
    assert result.text == "Answered [1]."
    assert executions == []
    refusals = [message for message in provider.requests[1].messages
                if getattr(message, "tool_call_id", None) == "1"]
    assert refusals and "budget" in refusals[0].content


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


@pytest.mark.parametrize("cached, expected", [
    ((2, 5), 7),       # every round reported its cache hits
    ((2, None), None),  # one round did not: price the whole prompt as uncached
])
def test_usage_aggregation_keeps_cached_prompt_tokens(cached, expected):
    usage = _aggregate_usage([
        Usage(10, 1, source="provider", cached_prompt_tokens=cached[0]),
        Usage(20, 3, source="provider", cached_prompt_tokens=cached[1]),
    ])
    assert (usage.prompt_tokens, usage.total_tokens, usage.cached_prompt_tokens) == (
        30, 34, expected)


class Inner(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    n: int
    s: str


class Outer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    inner: Inner
    items: tuple[Inner, ...]
    names: tuple[str, ...]
    count: int
    label: str
    maybe: int | None = None


class HasItem(BaseModel):
    """A model that really does have a field called "item"."""
    model_config = ConfigDict(extra="forbid", strict=True)
    item: str
    n: int


def _repair(args: dict) -> dict:
    return repair_arguments(dict(args), Outer)


def _valid(args: dict) -> bool:
    repaired = _repair(args)
    full = {"inner": {"n": 1, "s": "a"}, "items": (), "names": (), "count": 1,
                "label": "x", **repaired}
    try:
        Outer.model_validate(full)
        return True
    except ValidationError:
        return False


# ---------------------------------------------------------------- integers


@pytest.mark.parametrize(("value", "expected"), [
    ("3", 3),
    ({"item": "3"}, 3),
    ("-5", -5),
    ({"item": "-5"}, -5),
    ("0", 0),
])
def test_a_whole_number_sent_as_a_string_is_repaired(value, expected):
    """A provider that stringifies an integer is undone, not rejected."""
    assert _repair({"count": value})["count"] == expected


@pytest.mark.parametrize("value", [
    "3.5",      # a float, not a whole number
    " 3 ",      # padded: not the token the provider sent
    "3a",       # not a number at all
    "",         # an empty value is not zero
    "123456789012345678901",  # too wide to be an int
    "0x10",
])
def test_a_value_that_is_not_a_whole_number_is_left_alone(value):
    """Only a whole number is coerced; everything else still fails validation."""
    assert _repair({"count": value})["count"] == value


@pytest.mark.parametrize("empty", [None, {}, ""])
def test_an_empty_array_sent_as_another_empty_value_is_repaired(empty):
    """Providers spell an empty array as null, {} or ""; all mean no items."""
    assert _repair({"names": empty})["names"] == ()
    assert _valid({"names": empty})


def test_null_stays_null_for_a_field_that_allows_it():
    """Only a required array turns null into empty; an optional keeps its null."""
    class Maybe(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)
        names: tuple[str, ...] | None = None

    assert repair_arguments({"names": None}, Maybe)["names"] is None
    assert repair_arguments({"names": {}}, Maybe)["names"] == ()


def test_null_for_an_unused_string_is_empty_but_a_required_string_keeps_its_null():
    """A provider fills unused fields with null: "" for one that defaults to "", else untouched."""
    class Section(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)
        html: str = ""
        title: str

    assert repair_arguments({"html": None, "title": "t"}, Section) == {"html": "", "title": "t"}
    assert repair_arguments({"html": None, "title": None}, Section)["title"] is None
    Section.model_validate(repair_arguments({"html": None, "title": "t"}, Section))


def test_an_empty_object_for_an_optional_object_means_none():
    class Holder(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)
        inner: Inner | None = None

    assert repair_arguments({"inner": {}}, Holder)["inner"] is None
    assert repair_arguments({"inner": {"n": 1, "s": "a"}}, Holder)["inner"] == {"n": 1, "s": "a"}
    assert _repair({"maybe": {}})["maybe"] is None  # An optional number, too.


def test_a_boolean_is_not_coerced_to_an_integer():
    """True is not 1 here; strict validation must still reject it."""
    assert _repair({"count": True})["count"] is True


def test_a_numeric_string_for_a_string_field_stays_a_string():
    """The repair is guided by the declared type, not by the value's shape."""
    assert _repair({"label": "3"})["label"] == "3"
    assert _repair({"label": {"item": "3"}})["label"] == "3"


def test_an_optional_integer_is_repaired_too():
    assert _repair({"maybe": "4"})["maybe"] == 4


# ------------------------------------------------------------------ models


def test_one_object_sent_as_a_one_element_array_is_repaired():
    """A provider that wraps a lone object in an array is undone."""
    repaired = _repair({"inner": [{"n": 1, "s": "a"}]})
    assert repaired["inner"] == {"n": 1, "s": "a"}


def test_a_two_element_array_is_never_mistaken_for_one_object():
    """Two objects are two objects; the repair must not silently drop one."""
    args = {"inner": [{"n": 1, "s": "a"}, {"n": 2, "s": "b"}]}
    assert _repair(args)["inner"] == args["inner"]


def test_an_array_of_scalars_is_never_mistaken_for_one_object():
    args = {"inner": ["a"]}
    assert _repair(args)["inner"] == args["inner"]


def test_a_wrapped_object_is_unwrapped_for_a_model_field():
    repaired = _repair({"inner": {"item": {"n": 1, "s": "a"}}})
    assert repaired["inner"] == {"n": 1, "s": "a"}


def test_a_model_with_a_real_item_field_is_not_unwrapped():
    """The wrapper name only counts when the model does not declare it."""
    repaired = repair_arguments({"item": {"item": "x"}, "n": 1}, HasItem)
    assert repaired == {"item": "x", "n": 1}
    HasItem.model_validate(repaired)


# ------------------------------------------------------------------ lists


def test_an_empty_array_sent_as_an_empty_string_becomes_an_empty_list():
    assert _repair({"names": ""})["names"] == ()


def test_a_single_value_wrapped_for_a_list_becomes_a_one_element_list():
    assert _repair({"names": {"item": "a"}})["names"] == ("a",)


def test_a_tuple_field_becomes_a_tuple_after_repair():
    """A tuple field is handed back as a tuple, so strict validation accepts it."""
    repaired = _repair({"items": [{"n": 1, "s": "a"}]})
    assert isinstance(repaired["items"], tuple)
    assert isinstance(repaired["items"][0], dict)


# --------------------------------------------------------------- limits


def test_an_unknown_key_is_still_rejected():
    """Repair undoes re-encodings; it does not widen what a tool accepts."""
    assert "nope" in _repair({"count": 1, "nope": 2})
    full = {"inner": {"n": 1, "s": "a"}, "items": (), "names": (), "count": 1,
            "label": "x", "nope": 2}
    with pytest.raises(ValidationError):
        Outer.model_validate(repair_arguments(full, Outer))


def test_repairing_is_idempotent():
    """Repairing an already-repaired value changes nothing."""
    once = _repair({"count": "3", "inner": [{"n": 1, "s": "a"}], "names": ""})
    twice = repair_arguments(dict(once), Outer)
    assert once == twice
