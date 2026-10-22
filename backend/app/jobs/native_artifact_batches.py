"""Workspace-scoped native provider batches for independent artifact drafts."""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.artifacts.storage import ArtifactStore
from app.connections.models import ConnectionUnavailable, required_data_scopes
from app.connections.selection import _routing_config, resolve
from app.generation.artifacts import (
    OUTPUT_SCHEMA,
    ArtifactGenerationInput,
    artifact_messages,
    load_artifact_target,
    retrieve_artifact_context,
    validate_candidate,
)
from app.generation.native_tools import validate_generation_size
from app.jobs.provider_batches import (
    ProviderBatchService,
    ProviderBatchSnapshot,
)
from app.llm.base import GenerationRequest
from app.llm.batch import TERMINAL_BATCH_STATES, BatchItem
from app.llm.routing import classify_role
from app.operations.budget import OperationLimits
from app.retrieval.config import RetrievalConfig
from app.util.digest import json_digest


class ArtifactBatchItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    itemId: UUID
    inputs: ArtifactGenerationInput


class ArtifactBatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    batchId: UUID
    kind: Literal["document", "workbook", "html"]
    items: list[ArtifactBatchItemInput] = Field(min_length=1, max_length=50)

    def unique_item_ids(self):
        ids = [item.itemId for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("batch item IDs must be unique")


def _plan_item(conn, kind, item, *, settings, config, embedder, profiles, runtime, workspace_key,
               request_fingerprint):
    """Retrieve, route, and build the immutable request for one batch item."""
    inputs = item.inputs
    collection_id, prior = load_artifact_target(conn, settings, workspace_key, kind, inputs)
    conn.commit()
    outcome, chunks = retrieve_artifact_context(
        conn, settings, config, embedder, workspace_key, collection_id, inputs)

    authoring = inputs.mode == "authoring"
    required_scopes = required_data_scopes(workspace_key, authoring=authoring)
    data_scope = "user_supplied_data" if authoring else "institutional_corpus"
    classification, _ = classify_role(_routing_config(), query=inputs.prompt, difficulty="low")
    route = {"difficulty": "complex"} if classification == "complex" else {"job_kind": "batch_generation"}
    required_capabilities = frozenset({"batch", "structured_output"})
    selection = resolve(
        settings, conn, query=inputs.prompt, **route, data_scope=data_scope,
        required_data_scopes=required_scopes, required_capabilities=required_capabilities,
        profiles=profiles, runtime=runtime,
    )
    if selection is None or selection.role not in {"fast_generation", "complex"}:
        raise ConnectionUnavailable("no eligible fast or complex batch route")
    adapter = runtime.batch_provider(
        selection.profile, role=selection.role, data_scope=data_scope,
        required_capabilities=required_capabilities,
    )
    request = _artifact_request(kind, inputs, chunks, prior, selection.settings)
    metadata = {
        "kind": kind,
        "inputs": inputs.model_dump(mode="json"),
        "chunks": [_chunk_record(chunk) for chunk in chunks],
        "prior": prior,
        "trace_id": str(outcome.trace.trace_id),
        "profile_id": str(selection.profile_id),
        "profile_revision": selection.profile_revision,
        "role": selection.role,
        "data_scope": data_scope,
        "client_request_fingerprint": request_fingerprint,
        "request_policy": {
            "sampling_policy": selection.settings.llm_sampling_policy,
            "reasoning_effort": selection.settings.llm_reasoning_effort,
            "max_output_tokens": request.max_output_tokens,
            "timeout_seconds": request.timeout_seconds,
            "temperature": request.temperature,
        },
    }
    return selection, adapter, BatchItem(str(item.itemId), request), metadata


def create_artifact_batch(body: ArtifactBatchInput, *, settings, config: RetrievalConfig,
                          embedder, profiles, runtime, connect,
                          workspace_key: str) -> ProviderBatchSnapshot:
    """Retrieve, route and submit immutable prompts; no model runs in this process."""
    body.unique_item_ids()
    if any(item.inputs.outputPlan is not None for item in body.items):
        raise ValueError("dependent output plans are only supported by /api/runs")
    fingerprint_body = body.model_dump(mode="json")
    # `outputPlan` is a newly added optional field. Keep its absent and explicit-null
    # forms equivalent to the historical independent-batch fingerprint.
    for item in fingerprint_body["items"]:
        if item["inputs"].get("outputPlan") is None:
            item["inputs"].pop("outputPlan", None)
    request_fingerprint = json_digest(fingerprint_body)
    service = ProviderBatchService(connect)
    replay = service.idempotent_replay(body.batchId, workspace_key, request_fingerprint)
    if replay is not None:
        return replay
    from app.quotas.service import QuotaService

    with connect() as quota_conn:
        QuotaService.check_and_consume(
            quota_conn, workspace_key=workspace_key, check_concurrency=True
        )
    if not settings.routing_enabled:
        raise ConnectionUnavailable("native artifact batches require evaluated profile routing")
    planned_items = []
    metadata = {}
    chosen = None
    chosen_adapter = None
    operation_seconds = settings.batch_operation_timeout_seconds
    limits = OperationLimits(
        active_budget_seconds=operation_seconds,
        max_calls=1,
        total_token_budget=settings.operation_token_budget,
        per_call_output_cap=settings.llm_max_output_tokens,
        per_call_timeout_seconds=settings.llm_timeout_seconds,
    )

    for item in body.items:
        with connect() as conn:
            selection, adapter, planned, item_metadata = _plan_item(
                conn, body.kind, item, settings=settings, config=config, embedder=embedder,
                profiles=profiles, runtime=runtime, workspace_key=workspace_key,
                request_fingerprint=request_fingerprint)
        binding = (selection.profile_id, selection.profile_revision,
                   selection.profile.provider, selection.profile.model_id)
        if chosen is None:
            chosen, chosen_adapter = (selection, binding), adapter
        elif chosen[1] != binding:
            raise ValueError("all native batch items must use one evaluated connection profile")
        planned_items.append(planned)
        metadata[str(item.itemId)] = item_metadata
    if chosen_adapter is None or chosen is None:
        raise ValueError("native artifact batch has no planned items")
    selection = chosen[0]
    return service.submit(
        batch_id=body.batchId,
        workspace_key=workspace_key,
        provider=chosen_adapter,
        items=planned_items,
        limits=limits,
        profile_id=selection.profile_id,
        profile_revision=selection.profile_revision,
        consumer_data=metadata,
    )


def current_snapshot(batch_id: UUID, workspace_key: str, *, connect) -> ProviderBatchSnapshot:
    return ProviderBatchService(connect).get(batch_id, workspace_key)


def _advance_and_publish(advance: str, batch_id: UUID, workspace_key: str, *, provider, connect,
                         data_dir: str | Path) -> ProviderBatchSnapshot:
    """Poll or cancel the provider batch, then publish artifacts once it is terminal."""
    service = ProviderBatchService(connect)
    snapshot = getattr(service, advance)(batch_id, workspace_key, provider)
    if snapshot.state in TERMINAL_BATCH_STATES:
        service.publish_artifacts(
            batch_id, workspace_key,
            lambda conn, parent, item, result: _publish_one(
                conn, parent, item, result, workspace_key=workspace_key, data_dir=data_dir),
        )
    return service.get(batch_id, workspace_key)


def poll_and_publish(batch_id: UUID, workspace_key: str, *, provider, connect,
                     data_dir: str | Path) -> ProviderBatchSnapshot:
    return _advance_and_publish("poll", batch_id, workspace_key, provider=provider,
                                connect=connect, data_dir=data_dir)


def cancel_and_account(batch_id: UUID, workspace_key: str, *, provider, connect,
                       data_dir: str | Path) -> ProviderBatchSnapshot:
    return _advance_and_publish("cancel", batch_id, workspace_key, provider=provider,
                                connect=connect, data_dir=data_dir)


def _artifact_request(kind, inputs, chunks, prior, settings):
    return GenerationRequest(
        artifact_messages(kind, inputs, chunks, prior, settings),
        max_output_tokens=settings.llm_max_output_tokens,
        timeout_seconds=settings.llm_timeout_seconds,
        temperature=0 if settings.llm_sampling_policy == "greedy" else None,
        reasoning_effort=settings.llm_reasoning_effort,
        response_schema=OUTPUT_SCHEMA,
    )


def _chunk_record(chunk):
    return {"chunk_id": str(chunk.chunk_id), "source_id": str(chunk.source_id),
            "chunk_index": chunk.chunk_index, "source_name": chunk.source_name,
            "locator_label": chunk.locator_label, "text": chunk.text,
            "seams": list(chunk.seams), "normalized_score": chunk.normalized_score,
            "source_slot": chunk.source_slot, "rank": chunk.rank}


def _publish_one(conn, parent, item, result, *, workspace_key, data_dir):
    if (result.refusal or result.tool_calls or not result.text.strip()
            or result.finish_reason.lower() in {
                "length", "max_tokens", "content_filter", "error", "incomplete", "failed", "tool_calls",
            }):
        raise ValueError("provider returned incomplete artifact output")
    metadata = item["consumer_data"]
    kind = metadata["kind"]
    inputs = ArtifactGenerationInput.model_validate(metadata["inputs"])
    from app.retrieval.types import CitedChunk

    chunks = [CitedChunk(
        UUID(value["chunk_id"]), UUID(value["source_id"]), value["chunk_index"],
        value["source_name"], value["locator_label"], value["text"],
        tuple(value["seams"]), value["normalized_score"], value["source_slot"], value["rank"],
    ) for value in metadata["chunks"]]
    spec = validate_candidate(kind, result.text, chunks, mode=inputs.mode)
    validate_generation_size(spec)
    provenance = dict(spec.provenance)
    provenance.update({"trace_id": metadata["trace_id"],
                       "model_id": result.model or parent["model_id"]})
    spec = spec.model_copy(update={"provenance": provenance})
    store = ArtifactStore(conn, workspace_key=workspace_key, data_dir=Path(data_dir))
    if inputs.artifactId:
        row = store.revise_uncommitted(
            inputs.artifactId, inputs.expectedRevisionId, spec,
            draft_id=f"{parent['batch_id']}:{item['correlation_id']}",
        )
    else:
        row = store.create_uncommitted(spec)
    return row["artifact_id"], row["revision_id"]
