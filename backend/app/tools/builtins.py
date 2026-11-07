"""Built-in typed schemas and stable IDs for advising, artifacts, and code."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import Field

from app.tools.calculator import calculate
from app.tools.registry import (
    ToolCapability,
    ToolHandler,
    ToolInputModel,
    ToolOutputModel,
    ToolRegistry,
    ToolSpec,
)


class CalculatorInput(ToolInputModel):
    expression: str = Field(min_length=1, max_length=256)


class CalculatorOutput(ToolOutputModel):
    result: str = Field(max_length=128)


class SourceReadInput(ToolInputModel):
    resource_id: str = Field(min_length=1, max_length=128)
    locator: str | None = Field(default=None, max_length=256)
    max_chars: int = Field(default=4000, ge=1, le=10_000)


class SourceReadOutput(ToolOutputModel):
    source_name: str = Field(max_length=500)
    locator: str = Field(max_length=256)
    text: str = Field(max_length=10_000)
    truncated: bool = False
    available_chars: int | None = Field(default=None, ge=0)


def build_default_registry(handlers: dict[str, ToolHandler] | None = None, *,
                           extra_specs: Sequence[ToolSpec] = ()) -> ToolRegistry:
    """Build only exact stable IDs whose application handlers are provided.

    The calculator is built in and deterministic. Read, artifact, and sandbox
    handlers are injected by the trusted application layer.
    """
    injected = handlers or {}

    def calculate_handler(value: CalculatorInput, _scope):
        return CalculatorOutput(result=calculate(value.expression))

    specs = [
        ToolSpec(
            tool_id="advisor_calculate",
            description="Evaluate bounded arithmetic using decimal precision. No Python code is evaluated.",
            input_model=CalculatorInput,
            output_model=CalculatorOutput,
            capability=ToolCapability.CALCULATE,
            handler=calculate_handler,
            max_output_bytes=1024,
        ),
    ]
    definitions = [
        (
            "advisor_read_source",
            "Read an authorized source excerpt by opaque resource ID and optional locator.",
            SourceReadInput,
            SourceReadOutput,
            ToolCapability.SOURCE_READ,
            ("resource_id",),
            64 * 1024,
        ),
    ]
    for tool_id, description, input_model, output_model, capability, resources, output_bytes in definitions:
        handler = injected.get(tool_id)
        if handler is not None:
            specs.append(
                ToolSpec(
                    tool_id=tool_id,
                    description=description,
                    input_model=input_model,
                    output_model=output_model,
                    capability=capability,
                    handler=handler,
                    resource_fields=resources,
                    max_output_bytes=output_bytes,
                )
            )
    specs.extend(extra_specs)
    return ToolRegistry(specs)
