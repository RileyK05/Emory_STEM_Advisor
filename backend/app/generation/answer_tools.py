"""Request-scoped, read-only tools for grounded answer generation."""

from __future__ import annotations

from collections.abc import Callable

from app.generation.advising_tools import advising_identity, advising_specs
from app.generation.answer import PROMPT_VERSION
from app.llm.base import GenerationRequest, GenerationResult
from app.retrieval.types import CitedChunk
from app.tools.builtins import SourceReadOutput, build_default_registry
from app.tools.registry import ToolCapability, ToolInputError, ToolScope
from app.util.digest import json_digest


def answer_tool_context(*, query: str, chunks: list[CitedChunk], workspace_key: str,
                        settings, selection=None,
                        advising_context: dict | None = None,
                        history_context: str | None = None,
                        check_cancelled: Callable[[], None] | None = None):
    """Build read-only tools scoped to the exact chunks cited by this query."""
    by_id = {str(chunk.chunk_id): chunk for chunk in chunks}

    def read(value, _scope):
        if check_cancelled:
            check_cancelled()
        chunk = by_id[value.resource_id]
        if value.locator is not None and value.locator != chunk.locator_label:
            raise ToolInputError("requested locator does not match this cited excerpt")
        text = chunk.text[:value.max_chars]
        return SourceReadOutput(
            source_name=chunk.source_name,
            locator=chunk.locator_label,
            text=text,
            truncated=len(chunk.text) > len(text),
            available_chars=len(chunk.text),
        )

    registry = build_default_registry(
        {"advisor_read_source": read} if chunks else {},
        extra_specs=advising_specs(advising_context, chunks),
    )
    scope = ToolScope(
        principal_id=workspace_key,
        capabilities=frozenset({ToolCapability.CALCULATE, ToolCapability.SOURCE_READ}),
        resource_ids=frozenset(by_id),
        check_cancelled=check_cancelled,
    )
    identity = {
        "prompt_version": PROMPT_VERSION,
        "query": query,
        "history_context": history_context,
        "chunks": [
            {
                "chunk_id": str(chunk.chunk_id),
                "source_id": str(chunk.source_id),
                "source_name": chunk.source_name,
                "locator": chunk.locator_label,
                "text": chunk.text,
            }
            for chunk in chunks
        ],
        "advising": advising_identity(advising_context),
        "workspace": workspace_key,
        "provider": settings.llm_provider,
        "model": settings.llm_model_id or settings.hf_model_id,
        "sampling_policy": settings.llm_sampling_policy,
        "reasoning_effort": settings.llm_reasoning_effort,
        "profile_id": str(selection.profile_id) if selection else None,
        "profile_revision": selection.profile_revision if selection else None,
    }
    fingerprint = json_digest(identity)
    return registry, scope, fingerprint


class ToolAnswerProvider:
    """Provider-shaped bridge so answer validation remains in answer_from_context."""

    def __init__(self, provider, *, controller, registry, scope, operation, ledger,
                 operation_id, workspace_key, scope_fingerprint,
                 on_attempt, cancellation, on_progress, on_provisional_text=None,
                 on_provisional_reset=None, final_repair=None,
                 required_tool_names=frozenset()):
        self._provider = provider
        self._controller = controller
        self._registry = registry
        self._scope = scope
        self._operation = operation
        self._ledger = ledger
        self._operation_id = operation_id
        self._workspace_key = workspace_key
        self._scope_fingerprint = scope_fingerprint
        self._on_attempt = on_attempt
        self._cancellation = cancellation
        self._on_progress = on_progress
        self._on_provisional_text = on_provisional_text
        self._on_provisional_reset = on_provisional_reset
        self._final_repair = final_repair
        self._required_tool_names = required_tool_names

    def generate(self, request: GenerationRequest) -> GenerationResult:
        return self._controller.run(
            self._provider,
            request,
            registry=self._registry,
            scope=self._scope,
            operation=self._operation,
            ledger=self._ledger,
            operation_id=self._operation_id,
            workspace_key=self._workspace_key,
            scope_fingerprint=self._scope_fingerprint,
            cancellation=self._cancellation,
            on_progress=self._on_progress,
            on_provisional_text=self._on_provisional_text,
            on_provisional_reset=self._on_provisional_reset,
            on_attempt=self._on_attempt,
            final_repair=self._final_repair,
            required_tool_names=self._required_tool_names,
        )
