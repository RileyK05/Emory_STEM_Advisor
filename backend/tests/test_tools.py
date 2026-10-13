import pytest

from app.tools import (
    ToolCapability,
    ToolPermissionDenied,
    ToolScope,
    UnknownTool,
    build_default_registry,
)
from app.tools.builtins import ArtifactResult, CalculatorOutput, SourceReadOutput


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
    with pytest.raises(Exception, match="schema validation"):
        registry.invoke("advisor_calculate", {"expression": "1+1", "shell": True}, scope)


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
    def create_artifact(value, scope):
        return {
            "artifact_id": "artifact-1",
            "revision_id": "revision-1",
            "artifact_kind": "document",
            "title": value.artifact.title,
        }

    registry = build_default_registry({"advisor_create_artifact": create_artifact})
    definition = registry.definition("advisor_create_artifact")
    assert definition.name == "advisor_create_artifact"
    assert definition.input_schema["type"] == "object"
    result = registry.invoke(
        definition.name,
        {"artifact": {"kind": "document", "title": "Notes", "blocks": []}},
        ToolScope("user:1", frozenset({ToolCapability.ARTIFACT_WRITE})),
    )
    assert isinstance(result, ArtifactResult)
    assert result.title == "Notes"


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
