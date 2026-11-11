"""Translate backend retrieval/generation values into API schemas."""

from uuid import UUID

from app.api.schemas import (
    AnswerResponse,
    ApplicabilityNotice,
    Citation,
    ClaimReviewUnit,
    ContextItem,
    Contribution,
    ModelCallInfo,
    PromptInfo,
    RetrievalTrace,
    SeamStatus,
)
from app.generation.answer import GeneratedAnswer
from app.generation.claims import review_units
from app.retrieval.types import CitedChunk, Retrieved, SeamResult


def _redact_context(context_block, cited: set[str]) -> list:
    """Blank uncited passages in place so citation numbers stay stable."""
    return [
        item if isinstance(item, dict) and item.get("chunkId") in cited else {
            **(item if isinstance(item, dict) else {}),
            "chunkId": "", "sourceId": "",
            "sourceName": "", "locatorLabel": "", "text": "",
        }
        for item in context_block
    ]


def _cited_chunk_ids(citations) -> set[str]:
    return {
        item["chunkId"] for item in (citations or [])
        if isinstance(item, dict) and item.get("chunkId")
    }


def public_payload(value, principal):
    """Redact diagnostics at the HTTP boundary, keeping numbered cited excerpts.

    Saved evidence retains the complete trace for authorized review and replay.
    Empty context slots keep the original citation numbers stable. Live answers
    nest passages under ``trace``; saved turns store them under ``evidence``.
    """
    if principal.mode == "development" or principal.role == "admin":
        return value
    if isinstance(value, list):
        return [public_payload(item, principal) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: public_payload(item, principal) for key, item in value.items()}
    trace = result.get("trace")
    if isinstance(trace, dict) and "traceId" in trace:
        cited = _cited_chunk_ids(result.get("citations"))
        result["trace"] = {
            "traceId": trace["traceId"], "query": "", "normalizedQuery": "",
            "seams": [], "contributions": [],
            "contextBlock": _redact_context(trace.get("contextBlock") or [], cited),
            "prompt": {"system": "", "user": ""},
            "modelCall": {"provider": "", "modelId": "", "promptTokens": None,
                          "completionTokens": None, "usageSource": "unknown", "latencyMs": 0},
            "warnings": [], "errors": [], "timings": {},
        }
    evidence = result.get("evidence")
    if isinstance(evidence, dict) and "contextBlock" in evidence:
        cited = _cited_chunk_ids(evidence.get("citations"))
        result["evidence"] = {
            **evidence,
            "contextBlock": _redact_context(evidence.get("contextBlock") or [], cited),
        }
    return result


def _seams(results: tuple[SeamResult, ...]) -> list[SeamStatus]:
    return [
        SeamStatus(
            name=result.name,
            enabled=result.enabled,
            state=result.state,
            reason=result.reason,
            candidateCount=len(result.candidates),
        )
        for result in results
    ]


def _contributions(retrieved: Retrieved) -> list[Contribution]:
    return [
        Contribution(
            chunkId=str(chunk.chunk_id),
            seams=sorted(chunk.seams),
            normalizedScore=chunk.normalized_score,
            sourceSlot=chunk.source_slot,
        )
        for chunk in retrieved.chunks
    ]


def _context_block(chunks: list[CitedChunk]) -> list[ContextItem]:
    return [
        ContextItem(
            chunkId=str(chunk.chunk_id),
            sourceId=str(chunk.source_id),
            sourceName=chunk.source_name,
            locatorLabel=chunk.locator_label,
            text=chunk.text,
        )
        for chunk in chunks
    ]


def _model_call(settings, answer: GeneratedAnswer | None, latency_ms: int) -> ModelCallInfo:
    return ModelCallInfo(
        provider=settings.llm_provider,
        modelId=(answer.model_id if answer else None) or settings.llm_model_id or settings.hf_model_id,
        promptTokens=answer.prompt_tokens if answer else 0,
        completionTokens=answer.completion_tokens if answer else 0,
        usageSource=answer.usage_source if answer else "unknown",
        latencyMs=latency_ms,
    )


def answer_response(
    retrieved: Retrieved,
    answer: GeneratedAnswer,
    *,
    settings,
    trace_id: UUID,
    latency_ms: int,
    applicability: ApplicabilityNotice | None = None,
) -> AnswerResponse:
    warnings = list(answer.warnings) + list(retrieved.trace.warnings)
    return AnswerResponse(
        answer=answer.text,
        claims=[ClaimReviewUnit(text=unit.text, citationChunkIds=[
            str(retrieved.chunks[number - 1].chunk_id) for number in unit.citation_numbers
        ]) for unit in review_units(answer.text, len(retrieved.chunks))],
        citations=[
            Citation(
                sourceId=str(item.source_id),
                sourceName=item.source_name,
                locatorLabel=item.locator_label,
                chunkId=str(item.chunk_id),
            )
            for item in answer.citations
        ],
        trace=RetrievalTrace(
            traceId=str(trace_id),
            query=retrieved.trace.query,
            normalizedQuery=retrieved.trace.normalized_query,
            seams=_seams(retrieved.trace.seams),
            contributions=_contributions(retrieved),
            contextBlock=_context_block(retrieved.chunks),
            prompt=PromptInfo(system=answer.system_prompt, user=answer.user_prompt),
            modelCall=_model_call(settings, answer, latency_ms),
            warnings=warnings,
            errors=[],
            timings=dict(retrieved.trace.timings),
        ),
        applicability=applicability,
    )
