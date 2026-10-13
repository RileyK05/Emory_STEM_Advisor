"""Request-bound native artifact tools; all writes remain staged until publication."""

from pydantic import Field

from app.generation.artifact_candidate import ArtifactCandidate, ElementBinding, validate_candidate
from app.tools.builtins import SourceReadOutput, build_default_registry
from app.tools.registry import (
    ToolCapability,
    ToolInputModel,
    ToolOutputModel,
    ToolScope,
    ToolSpec,
)
from app.util.digest import json_digest

STAGE_TOOL = "advisor_stage_artifact"


class StageArtifactInput(ToolInputModel):
    candidate: ArtifactCandidate


class StageArtifactOutput(ToolOutputModel):
    spec_json: str = Field(max_length=64_000)
    bindings: list[ElementBinding] = Field(max_length=1000)


def artifact_tool_context(*, kind, inputs, chunks, prior, context, operation, settings, selection):
    """Return only calculator, exact selected excerpts, and a staging callback."""
    by_id = {str(c.chunk_id): c for c in chunks}

    def check():
        context.check_cancelled()
        operation.assert_publishable()
        operation.conn.commit()

    def read(value, _scope):
        check()
        chunk = by_id[value.resource_id]
        if value.locator is not None and value.locator != chunk.locator_label:
            raise ValueError("requested locator does not match this excerpt")
        return SourceReadOutput(
            source_name=chunk.source_name, locator=chunk.locator_label,
            text=chunk.text[:value.max_chars], truncated=len(chunk.text) > value.max_chars,
            available_chars=len(chunk.text),
        )

    def stage(value, _scope):
        check()
        candidate = value.candidate
        spec = validate_candidate(kind, candidate.model_dump_json(), chunks, mode=inputs.mode)
        validate_generation_size(spec)
        # This result is data in the tool ledger. The leased worker alone can
        # create a durable artifact after the final validation and all fences.
        return StageArtifactOutput(**candidate.model_dump())

    stage_spec = ToolSpec(
        tool_id=STAGE_TOOL,
        description="Validate and stage the requested native artifact. Call this tool alone to finish.",
        input_model=StageArtifactInput, output_model=StageArtifactOutput,
        capability=ToolCapability.ARTIFACT_WRITE, handler=stage, max_output_bytes=64_000,
    )
    registry = build_default_registry(
        {"advisor_read_source": read} if chunks else {}, extra_specs=(stage_spec,))
    scope = ToolScope(
        principal_id=operation.workspace_key,
        capabilities=frozenset({ToolCapability.CALCULATE, ToolCapability.SOURCE_READ,
                                ToolCapability.ARTIFACT_WRITE}),
        resource_ids=frozenset(by_id), check_cancelled=context.check_cancelled,
    )
    identity = {
        "version": 1, "kind": kind, "inputs": inputs.model_dump(mode="json"),
        "context": [{"chunk_id": str(c.chunk_id), "source_id": str(c.source_id),
                     "source": c.source_name, "locator": c.locator_label, "text": c.text}
                    for c in chunks],
        "prior": prior, "provider": settings.llm_provider, "model": settings.llm_model_id,
        "sampling_policy": settings.llm_sampling_policy,
        "reasoning_effort": settings.llm_reasoning_effort,
        "profile_id": str(selection.profile_id) if selection else None,
        "profile_revision": selection.profile_revision if selection else None,
    }
    fingerprint = json_digest(identity)
    return registry, scope, fingerprint


def validate_generation_size(spec):
    from app.artifacts.models import DocumentSpec, WorkbookSpec
    if (isinstance(spec, DocumentSpec) and len(spec.blocks) > 16
            or isinstance(spec, WorkbookSpec) and sum(len(s.cells) for s in spec.sheets) > 100):
        raise ValueError("artifact exceeds the generation plan")
