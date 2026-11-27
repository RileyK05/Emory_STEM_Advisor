"""Validate research references and render server-owned citation numbers."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from app.advising.research_types import (
    DecisionBlock,
    EvidenceRecord,
    FinalDecision,
    ResearchState,
    validate_candidate_references,
    validate_decision_limits,
    validate_evidence_references,
    validate_fact_support,
)
from app.generation.answer import Citation, GeneratedAnswer
from app.generation.answer_checks.course_claims import (
    absent_code_problems,
    advised_courses,
    cited_support_problems,
    entry_claim_problems,
    established_codes,
    identity_problems,
    recommendation_problems,
    unread_course_problems,
)
from app.generation.answer_checks.file_claims import (
    file_claim_problems,
)
from app.generation.answer_checks.perspective import (
    perspective_content_problems,
)
from app.generation.answer_checks.policy_facts import (
    grading_rule_problems,
    policy_number_problems,
    retired_course_problems,
)
from app.generation.answer_checks.safety import (
    answer_safety_problems,
)
from app.llm.base import Usage
from app.retrieval.types import CitedChunk

RESEARCH_PROMPT_VERSION = "research-63"
RESEARCH_RENDER_VERSION = "research-1"
_MARKER = re.compile(r"\[\s*(?:e\d+|\d+(?:\s*[,;-]\s*\d+)*)\s*\]", re.IGNORECASE)


# The whole reply to a request outside academic advising. It is fixed so a
# declined request never carries model-written text.
SCOPE_DECLINE_TEXT = ("I can only help with Emory academic advising: courses, majors, "
                      "requirements, policies, and planning for grad school or a career.")


class DecisionRejected(ValueError):
    pass


def answer_content_problems(decision: FinalDecision, *, instructions: str | None,
                            evidence: list[EvidenceRecord],
                            artifact_expected: bool = False) -> list[str]:
    """Reject a reply that is unsafe, or that misstates the file being made."""
    return (answer_safety_problems(decision, instructions=instructions, evidence=evidence)
            + file_claim_problems(decision, artifact_expected=artifact_expected))


def normalize_decision(decision: FinalDecision, supplied) -> FinalDecision:
    """Fix in code the slips that need no model round, before validation.

    A citation of evidence this request never supplied is one the model cannot
    have read, so it is dropped (a fact left without support is still rejected).
    A judgment keeps its kind: a candidate is optional, and its own evidence and
    named-course checks decide whether it stands.
    """
    blocks, changed = [], False
    for block in decision.blocks:
        evidence = tuple(e for e in block.evidence_ids if e in supplied)
        if evidence != tuple(block.evidence_ids):
            block = block.model_copy(update={"evidence_ids": evidence})
            changed = True
        blocks.append(block)
    return decision.model_copy(update={"blocks": tuple(blocks)}) if changed else decision


# --- general advice (question router policies) ---------------------------------

_BLOCK_ORDER = {"fact": 0, "judgment": 0, "perspective": 1, "assumption": 2, "limitation": 2,
                "question": 2}


def order_blocks(decision: FinalDecision) -> FinalDecision:
    """Records first: facts and judgments, then general advice, then the rest."""
    ordered = tuple(sorted(decision.blocks, key=lambda b: _BLOCK_ORDER.get(b.kind, 2)))
    return decision if ordered == decision.blocks else decision.model_copy(update={"blocks": ordered})


_CONTACT_RE = re.compile(r"727-7450|\b988\b|727-6111|\b911\b|\bCAPS\b|"
                         r"Counseling and Psychological Services")


def with_support_text(decision: FinalDecision, *, first: str | None = None,
                      last: str | None = None) -> FinalDecision:
    """The distress tier's fixed text, written by code, and nothing else of the kind.

    A block the model wrote that names support contacts goes: before `support`
    the student gets none, and from then on they get them once, in the checked
    wording. Cited facts stay (a student who asks where CAPS is gets the answer).
    """
    blocks = [b for b in decision.blocks if b.text not in {first, last}
              and not (b.kind in {"limitation", "perspective", "assumption"}
                       and _CONTACT_RE.search(b.text))]
    if first:
        blocks.insert(0, DecisionBlock(kind="limitation", text=first))
    if last:
        blocks.append(DecisionBlock(kind="limitation", text=last))
    return decision.model_copy(update={"blocks": tuple(blocks)})



def preparation_problems(decision, checks, supplied) -> list[str]:
    """Each cited preparation check must have run, and each claim must match it."""
    problems = []
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
    return problems


def revision_problems(decision, artifact_references) -> list[str]:
    """A revision must target an artifact this turn referenced."""
    problems = []
    if decision.artifact is not None and decision.artifact.purpose == "revise":
        revises = decision.artifact.revises
        referenced = artifact_references or set()
        if revises is None or (str(revises.artifact_id), str(revises.revision_id)) not in referenced:
            problems.append("artifact revision was not referenced by this turn")
    return problems


def validate_final(decision: FinalDecision, state: ResearchState | None,
                   evidence: list[EvidenceRecord], supplied: set[str],
                   checks: dict[str, dict], entity_evidence: dict[str, set[str]] | None = None,
                   artifact_references: set[tuple[str, str]] | None = None,
                   known_course_codes: frozenset[str] | None = None,
                   retired_courses: frozenset[str] = frozenset(),
                   instructions: str | None = None,
                   absent_codes: frozenset[str] = frozenset(),
                   artifact_expected: bool = False,
                   grading_rules: Sequence[tuple[str, str, str]] = ()) -> None:
    candidates = {c.id: c for c in state.candidates} if state else {}
    records = {e.evidence_id: e for e in evidence}
    problems = (validate_decision_limits(decision) + validate_fact_support(decision)
                + (answer_content_problems(decision, instructions=instructions,
                                           evidence=evidence,
                                           artifact_expected=artifact_expected)
                   if decision.blocks else [])
                + grading_rule_problems(decision, grading_rules)
                + perspective_content_problems(decision)
                + validate_evidence_references(decision, supplied & records.keys())
                + validate_candidate_references(decision, set(candidates)))
    if not decision.blocks:
        problems.append("finish requires at least one answer block")
    if any(_MARKER.search(b.text) for b in decision.blocks):
        problems.append("block text contains a raw citation marker; use evidence_ids")
    for block in decision.blocks:
        if block.kind == "judgment" and not block.evidence_ids:
            problems.append("judgment requires supporting evidence; cite the course records or "
                            "passages behind your advice")
    cited = {e for b in decision.blocks for e in b.evidence_ids}
    refs = {ref for e in cited if e in records for ref in records[e].entity_refs}
    if entity_evidence is not None:
        refs = {entity for entity, ids in entity_evidence.items() if ids & cited}
    established = established_codes(records, known_course_codes)
    # A code a passage the run read names is real to this run, master or not.
    absent_codes = frozenset(absent_codes - established)
    problems += absent_code_problems(decision, absent_codes)
    established |= absent_codes
    problems += identity_problems(decision, established)
    problems += cited_support_problems(decision, records, absent_codes)
    problems.extend(policy_number_problems(decision, records))
    problems += entry_claim_problems(decision, records)
    course_recommendations = advised_courses(decision, records)
    problems += recommendation_problems(decision, candidates, refs, course_recommendations)
    problems += unread_course_problems(decision, refs, course_recommendations)
    problems += preparation_problems(decision, checks, supplied)
    problems += revision_problems(decision, artifact_references)
    problems += retired_course_problems(decision, course_recommendations, retired_courses)
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
        # General advice is labeled where the student reads it: it cites nothing.
        paragraphs.append(f"General advice: {text}" if block.kind == "perspective" else text)
        blocks.append({"kind": block.kind, "text": block.text,
                       "evidenceIds": list(block.evidence_ids), "citationNumbers": refs,
                       "candidateIds": list(block.candidate_ids)})
    warnings = ("Research is partial; unresolved questions remain.",) if (
        decision.completeness == "partial") else ()
    answer = GeneratedAnswer("\n\n".join(paragraphs), tuple(citations), system_prompt,
                             user_prompt, usage.prompt_tokens, usage.completion_tokens,
                             warnings, usage.source, model_id)
    return RenderedDecision(answer, chunks, blocks)


def declined_decision(decision: FinalDecision) -> FinalDecision:
    """The published form of outcome=declined: the fixed sentence and nothing else.

    A decline is not an answer, so it may not cite, weigh candidates, recommend
    courses or make an artifact; a proposal that does is answering and is sent
    back to finish as one.
    """
    if (decision.candidate_ids or decision.recommended_course_codes
            or decision.preparation_check_ids or decision.preparation_claims
            or decision.artifact is not None
            or any(block.evidence_ids or block.candidate_ids for block in decision.blocks)):
        raise DecisionRejected(
            "outcome=declined is for requests outside academic advising and carries no "
            "citations, candidates, courses or artifact; if this is advising, finish with "
            "outcome=information instead")
    return decision.model_copy(update={
        "completeness": "complete", "unresolved_questions": (),
        "blocks": (DecisionBlock(kind="limitation", text=SCOPE_DECLINE_TEXT),)})
