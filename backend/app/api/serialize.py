"""Translate backend retrieval/generation values into API schemas."""

from uuid import UUID

from app.api.schemas import (
    AnswerResponse,
    Citation,
    ContextItem,
    Contribution,
    ModelCallInfo,
    PromptInfo,
    RefusalResponse,
    RetrievalTrace,
    SeamStatus,
)
from app.generation.answer import GeneratedAnswer
from app.retrieval.types import CitedChunk, Refusal, Retrieved, SeamResult


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
            sourceName=chunk.source_name,
            locatorLabel=chunk.locator_label,
            text=chunk.text,
        )
        for chunk in chunks
    ]


def _model_call(settings, answer: GeneratedAnswer | None, latency_ms: int) -> ModelCallInfo:
    return ModelCallInfo(
        provider=settings.llm_provider,
        modelId=settings.hf_model_id,
        promptTokens=answer.prompt_tokens if answer else 0,
        completionTokens=answer.completion_tokens if answer else 0,
        latencyMs=latency_ms,
    )


def answer_response(
    retrieved: Retrieved,
    answer: GeneratedAnswer,
    *,
    settings,
    trace_id: UUID,
    latency_ms: int,
) -> AnswerResponse:
    warnings = list(answer.warnings)
    return AnswerResponse(
        answer=answer.text,
        citations=[
            Citation(
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
        ),
    )


def refusal_response(refusal: Refusal, *, settings, latency_ms: int) -> RefusalResponse:
    trace = refusal.trace
    return RefusalResponse(
        reason=refusal.reason,
        trace=RetrievalTrace(
            traceId=str(trace.trace_id),
            query=trace.query,
            normalizedQuery=trace.normalized_query,
            seams=_seams(trace.seams),
            contributions=[],
            contextBlock=[],
            prompt=PromptInfo(system="", user=""),
            modelCall=_model_call(settings, None, latency_ms),
            warnings=["refusal: zero grounded candidates; model was not called"],
            errors=[],
        ),
    )
