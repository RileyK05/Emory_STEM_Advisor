"""Built-in typed schemas and stable IDs for advising, artifacts, and code."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from pydantic import Field

from app.artifacts.models import DocumentSpec, HtmlArtifactSpec, WorkbookSpec
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


ArtifactSpecInput = Annotated[
    DocumentSpec | WorkbookSpec | HtmlArtifactSpec,
    Field(discriminator="kind"),
]


class CreateArtifactInput(ToolInputModel):
    artifact: ArtifactSpecInput


class ReviseArtifactInput(ToolInputModel):
    artifact_id: str = Field(min_length=1, max_length=128)
    expected_revision_id: str = Field(min_length=1, max_length=128)
    instruction: str = Field(min_length=1, max_length=10_000)


class ArtifactResult(ToolOutputModel):
    artifact_id: str = Field(min_length=1, max_length=128)
    revision_id: str = Field(min_length=1, max_length=128)
    artifact_kind: str = Field(pattern=r"^(document|workbook|html)$")
    title: str = Field(min_length=1, max_length=500)


class SandboxRunInput(ToolInputModel):
    source: str = Field(min_length=1, max_length=200_000)
    resource_ids: list[str] = Field(default_factory=list, max_length=20)
    timeout_seconds: int = Field(default=30, ge=1, le=300)


class SandboxFileOutput(ToolOutputModel):
    path: str = Field(min_length=1, max_length=1024)
    content_base64: str = Field(max_length=1_500_000)


class SandboxRunOutput(ToolOutputModel):
    exit_code: int
    stdout: str = Field(max_length=100_000)
    stderr: str = Field(max_length=100_000)
    files: list[SandboxFileOutput] = Field(max_length=100)
    runtime_seconds: float = Field(ge=0, le=600)


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
        (
            "advisor_create_artifact",
            "Create a validated document, workbook, or HTML artifact from a typed spec.",
            CreateArtifactInput,
            ArtifactResult,
            ToolCapability.ARTIFACT_WRITE,
            (),
            4096,
        ),
        (
            "advisor_revise_artifact",
            "Revise an authorized artifact against its expected current revision.",
            ReviseArtifactInput,
            ArtifactResult,
            ToolCapability.ARTIFACT_WRITE,
            ("artifact_id",),
            4096,
        ),
        (
            "advisor_run_python",
            "Run bounded Python code in a disposable isolated container using scoped resource IDs.",
            SandboxRunInput,
            SandboxRunOutput,
            ToolCapability.CODE_EXECUTION,
            ("resource_ids",),
            2 * 1024 * 1024,
        ),
        (
            "advisor_run_javascript",
            "Run bounded JavaScript code in a disposable isolated container using scoped resource IDs.",
            SandboxRunInput,
            SandboxRunOutput,
            ToolCapability.CODE_EXECUTION,
            ("resource_ids",),
            2 * 1024 * 1024,
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
