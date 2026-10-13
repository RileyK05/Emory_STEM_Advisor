"""Allowlisted, typed tools available to model orchestration."""

from app.tools.builtins import build_default_registry
from app.tools.calculator import CalculationError, calculate
from app.tools.registry import (
    ToolCapability,
    ToolError,
    ToolInputError,
    ToolOutputTooLarge,
    ToolPermissionDenied,
    ToolRegistry,
    ToolScope,
    ToolSpec,
    UnknownTool,
)

__all__ = [
    "CalculationError",
    "ToolCapability",
    "ToolError",
    "ToolInputError",
    "ToolOutputTooLarge",
    "ToolPermissionDenied",
    "ToolRegistry",
    "ToolScope",
    "ToolSpec",
    "UnknownTool",
    "build_default_registry",
    "calculate",
]
