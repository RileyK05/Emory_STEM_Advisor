import pytest

from app.ingest.normalize import decode_text, normalize_text
from app.tools import (
    ToolCapability,
    ToolPermissionDenied,
    ToolScope,
    UnknownTool,
    build_default_registry,
)
from app.tools.builtins import CalculatorOutput, SourceReadOutput
from app.tools.calculator import CalculationError, calculate


def test_default_registry_contains_only_calculator_without_injected_handlers():
    registry = build_default_registry()
    assert registry.tool_ids == {"advisor_calculate"}
    result = registry.invoke(
        "advisor_calculate",
        {"expression": "19.99 * 2"},
        ToolScope("user:1", frozenset({ToolCapability.CALCULATE})),
    )
    assert result == CalculatorOutput(result="39.98")


def test_registry_requires_exact_tool_id_and_capability():
    registry = build_default_registry()
    scope = ToolScope("user:1", frozenset({ToolCapability.CALCULATE}))
    with pytest.raises(UnknownTool):
        registry.invoke("shell", {"command": "echo hi"}, scope)
    with pytest.raises(ToolPermissionDenied):
        registry.invoke("advisor_calculate", {"expression": "1+1"}, ToolScope("user:1", frozenset()))


def test_schema_rejects_extra_fields_and_malformed_arguments():
    registry = build_default_registry()
    scope = ToolScope("user:1", frozenset({ToolCapability.CALCULATE}))
    with pytest.raises(Exception, match="schema validation.*shell"):
        registry.invoke("advisor_calculate", {"expression": "1+1", "shell": True}, scope)
    with pytest.raises(Exception, match="schema validation"):
        registry.invoke("advisor_calculate", {"expression": {"item": "1+1", "shell": True}}, scope)
    # A provider's {"item": value} re-encoding is undone, not rejected.
    assert registry.invoke("advisor_calculate", {"expression": {"item": "1+1"}}, scope) == (
        CalculatorOutput(result="2"))


def test_injected_source_tool_requires_resource_scope_and_validates_output():
    def read_source(value, scope):
        assert scope.principal_id == "user:1"
        return {"source_name": "Syllabus", "locator": "Week 2", "text": "Course material."}

    registry = build_default_registry({"advisor_read_source": read_source})
    with pytest.raises(ToolPermissionDenied, match="outside its scope"):
        registry.invoke(
            "advisor_read_source",
            {"resource_id": "source-1"},
            ToolScope("user:1", frozenset({ToolCapability.SOURCE_READ})),
        )
    output = registry.invoke(
        "advisor_read_source",
        {"resource_id": "source-1", "max_chars": 40},
        ToolScope(
            "user:1",
            frozenset({ToolCapability.SOURCE_READ}),
            frozenset({"source-1"}),
        ),
    )
    assert isinstance(output, SourceReadOutput)
    assert output.text == "Course material."


def test_provider_definitions_are_schema_backed_and_tool_id_is_preserved():
    registry = build_default_registry({"advisor_read_source": lambda value, scope: {
        "source_name": "syllabus.txt", "locator": "page 1", "text": "x"}})
    definition = registry.definition("advisor_read_source")
    assert definition.name == "advisor_read_source"
    assert definition.input_schema["type"] == "object"


def test_tool_output_byte_limit_is_enforced():
    from app.tools.registry import (
        ToolOutputModel,
        ToolOutputTooLarge,
        ToolRegistry,
        ToolSpec,
    )

    class Input(ToolOutputModel):
        value: str

    class Output(ToolOutputModel):
        value: str

    registry = ToolRegistry(
        [ToolSpec(
            tool_id="test_long_output",
            description="test",
            input_model=Input,
            output_model=Output,
            capability=ToolCapability.CALCULATE,
            handler=lambda value, scope: {"value": "🙂" * 100},
            max_output_bytes=30,
        )]
    )
    with pytest.raises(ToolOutputTooLarge):
        registry.invoke(
            "test_long_output",
            {"value": "x"},
            ToolScope("user:1", frozenset({ToolCapability.CALCULATE})),
        )


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("2 + 3 * 4", "14"),
        ("(100.5 - 0.5) / 4", "25"),
        ("-2 ** 3", "-8"),
        ("2 ** 8", "256"),
        ("3 ** -2", "0.1111111111111111111111111111"),
    ],
)
def test_calculator_returns_deterministic_decimal_results(expression, expected):
    assert calculate(expression) == expected


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('whoami')",
        "open('/etc/passwd').read()",
        "x + 1",
        "True + 1",
        "2 ** 9",
        "1 / 0",
        "9" * 300,
    ],
)
def test_calculator_rejects_code_names_and_unbounded_operations(expression):
    with pytest.raises(CalculationError):
        calculate(expression)


@pytest.mark.parametrize(("raw", "expected"), [
    (b"\xef\xbb\xbfhello", "hello"),  # UTF-8 BOM
    (b"caf\xe9", "caf\u00e9"),  # cp1252 fallback
    ("hello".encode("utf-16"), "hello"),
    ("hello".encode("utf-32"), "hello"),
])
def test_decode_text(raw, expected):
    assert decode_text(raw) == expected


@pytest.mark.parametrize(("text", "expected"), [
    ("a\r\nb\rc", "a\nb\nc"),  # CRLF and CR become LF
    ("a\x00b\x07c\td\ne", "abc\td\ne"),  # C0 controls go; tab and newline stay
    ("a\x85b\x9fc", "abc"),  # C1 controls
    ("co\u00adoperate", "cooperate"),  # soft hyphen
    ("a\u200bb\u200dc\u2060d\ufeffe", "abcde"),  # zero-width characters
    ("x\u202ey", "xy"),  # bidi control
])
def test_normalize_text(text, expected):
    assert normalize_text(text) == expected
    assert normalize_text(expected) == expected  # idempotent
