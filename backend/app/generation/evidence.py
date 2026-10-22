from datetime import UTC, datetime
from uuid import uuid4

from psycopg.types.json import Jsonb

from app.db import queries
from app.generation.answer import PROMPT_VERSION, GeneratedAnswer
from app.generation.claims import review_units
from app.llm.base import GenerationResult, Usage
from app.pricing import PricingService
from app.retrieval.types import Refusal, Retrieved


def record_attempt(conn, trace_id, settings, result: GenerationResult | None,
                   error: Exception | None, latency_ms: int, *, operation_id=None, request=None, selection=None) -> None:
    usage = result.usage if result else getattr(error, "usage", None)
    if not isinstance(usage, Usage):
        usage = Usage(source="unknown")
    provider = settings.llm_provider
    model_id = (result.model if result else None) or settings.llm_model_id or settings.hf_model_id
    created_at = datetime.now(UTC)
    schedule_id, cost, cost_status = PricingService.compute_call_cost(
        conn,
        provider=provider,
        model_id=model_id,
        created_at=created_at,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        cached_prompt_tokens=usage.cached_prompt_tokens,
        usage_source=usage.source,
    )
    with conn.transaction():
        conn.execute(queries.get("insert_model_call"), {
            "model_call_id": uuid4(), "trace_id": trace_id,
            "provider": provider,
            "model_id": model_id,
            "status": "failed" if error else "completed",
            "finish_reason": result.finish_reason if result else None,
            "request_id": result.request_id if result else getattr(error, "request_id", None),
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "usage_source": usage.source,
            "latency_ms": latency_ms,
            "error_type": type(error).__name__ if error else None,
            "operation_id": operation_id,
            "sampling_policy": settings.llm_sampling_policy,
            "reasoning_effort": request.reasoning_effort if request else settings.llm_reasoning_effort,
            "requested_output_tokens": request.max_output_tokens if request else None,
            "requested_timeout_seconds": request.timeout_seconds if request else None,
            "requested_temperature": request.temperature if request else None,
            "profile_id": selection.profile_id if selection else None,
            "profile_revision": selection.profile_revision if selection else None,
            "cached_prompt_tokens": usage.cached_prompt_tokens,
            "schedule_id": schedule_id,
            "estimated_cost": cost,
            "cost_status": cost_status,
        })



def persist_response(conn, retrieved: Retrieved | Refusal, *, status: str, reason: str | None = None,
                     answer: GeneratedAnswer | None = None, prompt_version: str | None = None,
                     claim_units: list[dict] | None = None):
    response_id = uuid4()
    chunks = getattr(retrieved, "chunks", [])
    snapshots = [
        {"chunk_id": str(c.chunk_id), "source_id": str(c.source_id),
         "source_name": c.source_name, "locator_label": c.locator_label, "text": c.text}
        for c in chunks
    ]
    by_id = {c.chunk_id: c for c in chunks}
    with conn.transaction():
        conn.execute(queries.get("insert_response"), {
            "response_id": response_id, "trace_id": retrieved.trace.trace_id,
            "status": status, "text": answer.text if answer and status == "answer" else "",
            "reason": reason,
            "prompt_version": prompt_version or (answer.prompt_version if answer else PROMPT_VERSION),
            "context_snapshot": Jsonb(snapshots),
        })
        for ordinal, citation in enumerate(answer.citations if answer and status == "answer" else ()):
            chunk = by_id[citation.chunk_id]
            conn.execute(queries.get("insert_response_citation"), {
                "response_id": response_id, "ordinal": ordinal,
                "chunk_id": chunk.chunk_id, "source_id": chunk.source_id,
                "source_name": chunk.source_name, "locator_label": chunk.locator_label,
                "text_snapshot": chunk.text,
            })
        if answer and status == "answer":
            units = claim_units if claim_units is not None else [
                {"text": u.text, "citation_numbers": list(u.citation_numbers)}
                for u in review_units(answer.text, len(chunks))]
            for ordinal, unit in enumerate(units):
                conn.execute(queries.get("insert_claim"), {
                    "claim_id": uuid4(), "response_id": response_id,
                    "ordinal": ordinal, "text": unit["text"],
                    "citation_numbers": Jsonb(unit["citation_numbers"]),
                })
    return response_id
