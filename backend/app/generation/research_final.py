"""Validate research references and render server-owned citation numbers."""

import re
from dataclasses import dataclass
from uuid import UUID

from app.advising.research_types import (
    EvidenceRecord,
    FinalDecision,
    ResearchState,
    validate_candidate_references,
    validate_decision_limits,
    validate_evidence_references,
    validate_fact_support,
)
from app.atlas.records import normalize_code
from app.generation.answer import Citation, GeneratedAnswer
from app.llm.base import Usage
from app.programs.requirements import course_mentions
from app.retrieval.types import CitedChunk

RESEARCH_PROMPT_VERSION = "research-17"
RESEARCH_RENDER_VERSION = "research-1"
_MARKER = re.compile(r"\[\s*(?:e\d+|\d+(?:\s*[,;-]\s*\d+)*)\s*\]", re.IGNORECASE)
_INCLUSION = re.compile(
    r"\b(?:required|requirement|elective|counts?|sits? within|list(?:ed)?|"
    r"program|major|minor|degree)\b", re.IGNORECASE)
_ENTRY = re.compile(
    r"\b(?:entry point|starting point|first course|initial course|"
    r"begin with|start with)\b", re.IGNORECASE)
_ENTRY_NEGATED = re.compile(
    r"\b(?:not|never|rather than|instead of)\b[^.]{0,80}"
    r"\b(?:entry point|starting point|first course|initial course)\b", re.IGNORECASE)
_PREREQUISITE = re.compile(r"\b(?:prerequisite|prerequisites|requisite|requisites)\b",
                           re.IGNORECASE)


def _program_requirement_mentions(records, cited: set[str]) -> set[str]:
    """Codes mentioned in cited program reads, including alternatives and exclusions.

    A mention alone does not prove eligibility or support a course recommendation.
    Program evidence carries the issued program handle as an entity reference;
    the parser handles catalog shorthand such as ``BIOL 141/141L``.
    """
    mentioned: set[str] = set()
    for evidence_id in cited:
        record = records.get(evidence_id)
        if record is None or not any(ref.startswith("h_") for ref in record.entity_refs):
            continue
        mentioned.update(course_mentions(record.exact_text))
    return mentioned


class DecisionRejected(ValueError):
    pass


def validate_final(decision: FinalDecision, state: ResearchState | None,
                   evidence: list[EvidenceRecord], supplied: set[str],
                   checks: dict[str, dict], entity_evidence: dict[str, set[str]] | None = None) -> None:
    candidates = {c.id: c for c in state.candidates} if state else {}
    records = {e.evidence_id: e for e in evidence}
    problems = (validate_decision_limits(decision) + validate_fact_support(decision)
                + validate_evidence_references(decision, supplied & records.keys())
                + validate_candidate_references(decision, set(candidates)))
    if not decision.blocks:
        problems.append("finish requires at least one answer block")
    if any(_MARKER.search(b.text) for b in decision.blocks):
        problems.append("block text contains a raw citation marker; use evidence_ids")
    for block in decision.blocks:
        if block.kind == "judgment" and (not block.candidate_ids or not block.evidence_ids):
            problems.append("judgment requires a candidate and supporting evidence; "
                            "use fact or limitation for source-scope conclusions, "
                            "and judgment only for candidate preferences")
    cited = {e for b in decision.blocks for e in b.evidence_ids}
    refs = {ref for e in cited if e in records for ref in records[e].entity_refs}
    if entity_evidence is not None:
        refs = {entity for entity, ids in entity_evidence.items() if ids & cited}
    for block in decision.blocks:
        if block.kind != "fact" or not _INCLUSION.search(block.text):
            continue
        support = "\n".join(records[e].exact_text for e in block.evidence_ids if e in records)
        named = set(course_mentions(block.text))
        missing = named - set(course_mentions(support)) - _program_requirement_mentions(
            records, set(block.evidence_ids))
        if missing:
            problems.append(
                "fact block names course code(s) absent from its cited evidence: "
                + ", ".join(sorted(missing)))
    for block in decision.blocks:
        if (block.kind not in {"fact", "judgment"} or not _ENTRY.search(block.text)
                or _ENTRY_NEGATED.search(block.text)):
            continue
        mentions = course_mentions(block.text)
        if not mentions:
            continue
        course = mentions[0]
        support = [records[e] for e in block.evidence_ids
                   if e in records and course in set(records[e].entity_refs)]
        if not support:
            problems.append(
                f"entry/starting-point claim names {course} without citing its course record")
        elif any(_PREREQUISITE.search(record.exact_text) for record in support):
            prerequisite_codes = {
                code for record in support for code in course_mentions(record.exact_text)
            } - {course}
            if not prerequisite_codes & set(mentions):
                problems.append(
                    f"entry/starting-point claim for {course} cites a record listing a prerequisite; "
                    "state the prerequisite first")
    course_recommendations = set(decision.recommended_course_codes)
    if decision.outcome == "recommendation":
        if not decision.candidate_ids or not any(b.kind == "judgment" for b in decision.blocks):
            problems.append("recommendation requires a candidate and a judgment block")
        for cid in decision.candidate_ids:
            candidate = candidates.get(cid)
            if candidate is None:
                continue
            for handle in candidate.program_handles:
                if handle not in refs:
                    problems.append(f"candidate {cid}: read and cite program {handle}")
            if not candidate.program_handles:
                course_recommendations.update(candidate.course_codes)
    for requested in course_recommendations:
        code = normalize_code(requested)
        if code is None:
            problems.append(f"invalid recommended course code {requested!r}")
        elif code in refs:
            continue  # A cited course read, not merely a program mention.
        elif not any(
            b.kind == "limitation" and code in b.text
            and re.search(r"missing|unavailable|unknown|not read", b.text, re.IGNORECASE)
            for b in decision.blocks
        ):
            problems.append(f"read recommended course {code} or disclose its missing record")
    for check_id in decision.preparation_check_ids:
        if check_id not in checks:
            problems.append(f"preparation check {check_id} was not executed")
    for claim in decision.preparation_claims:
        check = checks.get(claim.check_id)
        if (check is None or claim.check_id not in decision.preparation_check_ids
                or set(claim.selected_codes) != set(check["selected_codes"])
                or set(claim.completed_codes) != set(check["completed_codes"])
                or claim.conclusion != check["conclusion"]):
            problems.append(f"preparation claim does not match executed check {claim.check_id}")
        elif not set(check["evidence_ids"]) <= supplied:
            problems.append(f"reactivate evidence for preparation check {claim.check_id}")
    if problems:
        raise DecisionRejected("; ".join(problems))


@dataclass(frozen=True)
class RenderedDecision:
    answer: GeneratedAnswer
    chunks: list[CitedChunk]
    blocks: list[dict]


def render_final(decision: FinalDecision, evidence: list[EvidenceRecord], *,
                 system_prompt: str, user_prompt: str, usage: Usage,
                 model_id: str | None) -> RenderedDecision:
    records = {e.evidence_id: e for e in evidence}
    numbers: dict[str, int] = {}
    chunks, citations, blocks, paragraphs = [], [], [], []
    for block in decision.blocks:
        for eid in block.evidence_ids:
            if eid in numbers:
                continue
            record = records[eid]
            numbers[eid] = len(numbers) + 1
            chunks.append(CitedChunk(
                UUID(record.chunk_id), UUID(record.source_id), 0, record.source_name,
                record.locator_label, record.exact_text, (), 0.0, 0, len(numbers)))
            citations.append(Citation(record.source_name, record.locator_label,
                                      UUID(record.chunk_id), UUID(record.source_id)))
        refs = list(dict.fromkeys(numbers[e] for e in block.evidence_ids))
        text = block.text + (" " + " ".join(f"[{n}]" for n in refs) if refs else "")
        paragraphs.append(text)
        blocks.append({"kind": block.kind, "text": block.text,
                       "evidenceIds": list(block.evidence_ids), "citationNumbers": refs,
                       "candidateIds": list(block.candidate_ids)})
    warnings = ("Research is partial; unresolved questions remain.",) if (
        decision.completeness == "partial") else ()
    answer = GeneratedAnswer("\n\n".join(paragraphs), tuple(citations), system_prompt,
                             user_prompt, usage.prompt_tokens, usage.completion_tokens,
                             warnings, usage.source, model_id)
    return RenderedDecision(answer, chunks, blocks)
