"""Typed allowlisted tool registry with capabilities and per-resource checks."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from app.llm.base import ToolDefinition

_TOOL_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_RESOURCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$")


class ToolCapability(StrEnum):
    CALCULATE = "calculate"
    SOURCE_READ = "source_read"
    CORPUS_RESEARCH = "corpus_research"
    ARTIFACT_WRITE = "artifact_write"
    CODE_EXECUTION = "code_execution"


class ToolError(RuntimeError):
    """Base tool dispatch error with safe messages."""


class UnknownTool(ToolError):
    pass


class ToolPermissionDenied(ToolError):
    pass


class ToolInputError(ToolError):
    pass


class ToolOutputTooLarge(ToolError):
    pass


class ToolInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ToolOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


@dataclass(frozen=True)
class ToolScope:
    principal_id: str
    capabilities: frozenset[ToolCapability]
    resource_ids: frozenset[str] = frozenset()
    check_cancelled: Callable[[], None] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.principal_id or len(self.principal_id) > 200:
            raise ValueError("principal_id must contain 1 to 200 characters")
        if any(not _RESOURCE_RE.fullmatch(value) for value in self.resource_ids):
            raise ValueError("tool scope contains an invalid resource ID")


ToolHandler = Callable[[BaseModel, ToolScope], Any]


@dataclass(frozen=True)
class ToolSpec:
    tool_id: str
    description: str
    input_model: type[ToolInputModel]
    output_model: type[ToolOutputModel]
    capability: ToolCapability
    handler: ToolHandler = field(repr=False, compare=False)
    resource_fields: tuple[str, ...] = ()
    max_output_bytes: int = 256 * 1024

    def __post_init__(self) -> None:
        if not _TOOL_ID_RE.fullmatch(self.tool_id):
            raise ValueError("tool_id must be a stable lowercase identifier")
        if not self.description or len(self.description) > 1000:
            raise ValueError("tool description must contain 1 to 1000 characters")
        if not 1 <= self.max_output_bytes <= 8 * 1024 * 1024:
            raise ValueError("tool max_output_bytes must be between 1 byte and 8 MiB")
        unknown = set(self.resource_fields) - set(self.input_model.model_fields)
        if unknown:
            raise ValueError(f"resource fields are absent from input schema: {sorted(unknown)}")

    def provider_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.tool_id,
            description=self.description,
            input_schema=self.input_model.model_json_schema(),
        )


class ToolRegistry:
    def __init__(self, specs: tuple[ToolSpec, ...] | list[ToolSpec] = ()):
        by_id = {}
        for spec in specs:
            if spec.tool_id in by_id:
                raise ValueError(f"duplicate tool ID: {spec.tool_id}")
            by_id[spec.tool_id] = spec
        self._specs = by_id

    @property
    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(spec.provider_definition() for spec in self._specs.values())

    @property
    def tool_ids(self) -> frozenset[str]:
        return frozenset(self._specs)

    def invoke(self, tool_id: str, arguments: Mapping[str, Any], scope: ToolScope) -> BaseModel:
        spec = self._specs.get(tool_id)
        if spec is None:
            raise UnknownTool(f"Unknown tool ID: {tool_id!r}")
        if spec.capability not in scope.capabilities:
            raise ToolPermissionDenied(f"Tool capability {spec.capability.value!r} is not granted.")
        try:
            parsed = spec.input_model.model_validate(arguments)
        except ValidationError as exc:
            raise ToolInputError("Tool arguments failed schema validation.") from exc

        for field_name in spec.resource_fields:
            value = getattr(parsed, field_name)
            identifiers = [value] if isinstance(value, str) else list(value or ())
            if any(not _RESOURCE_RE.fullmatch(item) for item in identifiers):
                raise ToolInputError("Resource ID is invalid.")
            if any(item not in scope.resource_ids for item in identifiers):
                raise ToolPermissionDenied("Tool request references a resource outside its scope.")

        if scope.check_cancelled:
            scope.check_cancelled()
        try:
            value = spec.handler(parsed, scope)
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError("Tool execution failed.") from exc
        try:
            output = spec.output_model.model_validate(value)
            encoded = output.model_dump_json().encode("utf-8")
        except ValidationError as exc:
            raise ToolError("Tool returned a value outside its output schema.") from exc
        if len(encoded) > spec.max_output_bytes:
            raise ToolOutputTooLarge("Tool output exceeds its configured byte limit.")
        if scope.check_cancelled:
            scope.check_cancelled()
        return output

    def definition(self, tool_id: str) -> ToolDefinition:
        spec = self._specs.get(tool_id)
        if spec is None:
            raise UnknownTool(f"Unknown tool ID: {tool_id!r}")
        return spec.provider_definition()
