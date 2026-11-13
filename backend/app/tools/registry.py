"""Typed allowlisted tool registry with capabilities and per-resource checks."""

from __future__ import annotations

import re
import types
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Annotated, Any, Union, get_args, get_origin

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

# An OpenAI-compatible provider (deepseek via Ollama) re-encodes tool arguments:
# a field's value arrives as {"item": value}, and an empty array as "". Others
# (minimax) send an empty array as null or {}. These are serialization artifacts,
# not the model's choices, so they are undone exactly, guided by the input schema.
# No value the model did not send is ever invented; anything else is left for
# validation to accept or reject.
_WRAPPER_KEY = "item"
_INTEGER_RE = re.compile(r"-?\d{1,18}")


def _concrete(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin is Annotated:
        return _concrete(get_args(annotation)[0])
    if origin in (Union, types.UnionType):
        options = [arg for arg in get_args(annotation) if arg is not type(None)]
        return _concrete(options[0]) if len(options) == 1 else annotation
    return annotation


def _allows_none(annotation: Any) -> bool:
    origin = get_origin(annotation)
    if origin is Annotated:
        return _allows_none(get_args(annotation)[0])
    return origin in (Union, types.UnionType) and type(None) in get_args(annotation)


def repair_arguments(value: Any, annotation: Any) -> Any:
    """Undo provider re-encodings of a tool argument against its declared type."""
    nullable = _allows_none(annotation)
    annotation = _concrete(annotation)
    is_model = isinstance(annotation, type) and issubclass(annotation, BaseModel)
    if nullable and value == {} and annotation is not dict and get_origin(annotation) not in (
            dict, tuple, list):
        return None
    wrapped = (isinstance(value, dict) and set(value) == {_WRAPPER_KEY}
               and not (is_model and _WRAPPER_KEY in annotation.model_fields))
    if wrapped:
        value = value[_WRAPPER_KEY]
    origin = get_origin(annotation)
    if origin in (tuple, list):
        args = get_args(annotation)
        if value == "" or value == {} or (value is None and not nullable):
            value = []
        elif wrapped and not isinstance(value, (list, tuple)):
            value = [value]
        if not isinstance(value, (list, tuple)):
            return value
        if origin is tuple and args and args[-1] is not Ellipsis:
            items = [repair_arguments(item, arg) for item, arg in zip(value, args, strict=False)]
            items += list(value[len(args):])
        else:
            element = args[0] if args else Any
            items = [repair_arguments(item, element) for item in value]
        # Strict models accept a JSON array for a tuple only in JSON mode.
        return tuple(items) if origin is tuple else items
    # A whole number sent as a string ("3") and one object sent as a one-element
    # array are the same value; strict validation would reject both.
    if annotation is int and isinstance(value, str) and _INTEGER_RE.fullmatch(value):
        return int(value)
    if is_model and isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
        value = value[0]
    if is_model and isinstance(value, dict):
        fields = annotation.model_fields
        return {key: _repair_field(item, fields[key]) if key in fields else item
                for key, item in value.items()}
    return value


def _repair_field(value: Any, field: Any) -> Any:
    # A string field that defaults to "" and arrives as null means "not used",
    # the same way an empty array arrives as null. A required string keeps its null.
    if (value is None and field.default == "" and _concrete(field.annotation) is str
            and not _allows_none(field.annotation)):
        return ""
    return repair_arguments(value, field.annotation)


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
            input_schema=provider_schema(self.input_model),
        )


_SCHEMAS: dict[type, dict] = {}


def _without_titles(node):
    """Drop pydantic's generated `title` labels; a property named "title" stays."""
    if isinstance(node, list):
        return [_without_titles(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if key in {"properties", "$defs", "definitions", "patternProperties"} and isinstance(
                value, dict):
            out[key] = {name: _without_titles(schema) for name, schema in value.items()}
        elif key == "title" and isinstance(value, str):
            continue
        else:
            out[key] = _without_titles(value)
    return out


def provider_schema(model: type) -> dict:
    """A tool's input schema as providers see it, built once per model class.

    Every research round sends every tool's schema, so it is generated once and
    copied per use (callers may narrow it). The generated titles repeat each
    field name and cost about a sixth of the schema bytes.
    """
    schema = _SCHEMAS.get(model)
    if schema is None:
        schema = _SCHEMAS[model] = _without_titles(model.model_json_schema())
    return deepcopy(schema)


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
            parsed = spec.input_model.model_validate(
                repair_arguments(dict(arguments), spec.input_model))
        except ValidationError as exc:
            detail = "; ".join(
                f"{'.'.join(str(part) for part in error['loc']) or 'input'}: {error['msg']}"
                for error in exc.errors()[:5]
            )
            raise ToolInputError(
                "Tool arguments failed schema validation: " + detail[:500]) from exc

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
