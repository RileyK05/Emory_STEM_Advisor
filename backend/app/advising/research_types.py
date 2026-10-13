"""Strict internal models for the research advising path.

Everything here is a pure data contract: no database access, no provider seam, no
model. ``app/advising/`` is scanned by ``tests/test_no_llm_in_prereq.py``, so this
module must never import ``app.llm`` or a concrete provider/embedder.

Two rules these types exist to make hard to break:

1. **Sourced facts and advising judgment are different things.** A ``fact`` block
   carries evidence; a ``judgment`` block may express a preference and must name
   the facts it rests on. Neither is created by schema validation alone -- these
   models bound and shape the output, they do not prove entailment.
2. **Nothing here widens scope or writes graph facts.** Evidence IDs are issued by
   the server inside one run; a model cannot invent one. Candidate course codes
   are hypotheses, not prerequisites.

Bounds below are starting limits from the research plan, not a claim that they
are sufficient for every question.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# --- Bounds ------------------------------------------------------------------

MAX_CANDIDATES = 6
MAX_CRITERIA = 8
MAX_OPEN_QUESTIONS = 8
MAX_STATE_BYTES = 8_000
MAX_DECISION_BLOCKS = 20
MAX_DECISION_TEXT_BYTES = 16_000
MAX_QUESTIONS_PER_TURN = 2
MAX_UNRESOLVED_QUESTIONS = 8

#: Evidence register ceilings for one run.
MAX_EVIDENCE_ITEMS = 100
MAX_EVIDENCE_BYTES = 256_000

SourceKind = Literal[
    "institutional_catalog",
    "institutional_department",
    "institutional_policy",
    "career_reference",
    "user_supplied",
    "unclassified",
]

ContentCompleteness = Literal["complete", "incomplete"]
AssertionBasis = Literal["user_report", "assumption"]
CriterionBasis = Literal["user_report", "career_evidence", "assumption"]
CandidateStatus = Literal["investigating", "viable", "conditional", "not_preferred"]
QuestionImportance = Literal["material", "optional"]
QuestionNextAction = Literal["research", "ask_student", "disclose"]
DecisionOutcome = Literal[
    "recommendation", "information", "clarification", "insufficient_evidence"
]
DecisionCompleteness = Literal["complete", "partial"]
BlockKind = Literal["fact", "judgment", "assumption", "limitation", "question"]
PreparationConclusion = Literal["blocked", "dependency_order", "unresolved"]


class _Strict(BaseModel):
    """No unknown fields, no silent coercion. Tools and state share this rule."""

    model_config = ConfigDict(extra="forbid", strict=True)


class _Frozen(_Strict):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


# --- Evidence ----------------------------------------------------------------


class EvidenceRecord(_Frozen):
    """One locator-scoped chunk read during the run, with server-issued identity.

    ``evidence_id`` is assigned by the server and is stable for the whole run,
    including across context compaction and tool-result replay. An item is one
    actual chunk: a complete course or requirement section references several,
    and chunks are never concatenated to synthesize a new identity.
    """

    evidence_id: str = Field(min_length=1, max_length=32, pattern=r"^e[0-9]+$")
    source_id: str = Field(min_length=1, max_length=64)
    source_revision: int = Field(ge=0)
    origin_key: str | None = None
    source_kind: SourceKind
    source_name: str = Field(min_length=1, max_length=400)
    publisher: str | None = Field(default=None, max_length=2000)
    geography: str | None = Field(default=None, max_length=2000)
    occupation_id: str | None = Field(default=None, max_length=2000)
    source_url: str | None = Field(default=None, max_length=2000)
    edition: str | None = Field(default=None, max_length=200)
    acquired_at: str | None = Field(default=None, max_length=64)
    published_at: str | None = Field(default=None, max_length=64)
    chunk_id: str = Field(min_length=1, max_length=64)
    locator_id: str | None = Field(default=None, max_length=64)
    locator_label: str = Field(min_length=1, max_length=400)
    exact_text: str = Field(min_length=1)
    text_digest: str = Field(min_length=8, max_length=64)
    entity_refs: tuple[str, ...] = ()
    acquired_step: int = Field(ge=0)
    child_trace_id: str | None = Field(default=None, max_length=64)
    content_completeness: ContentCompleteness


# --- Working state -----------------------------------------------------------


class StudentContext(_Strict):
    """A student-reported fact or an explicit working assumption.

    Never promoted into an institutional fact by being placed here.
    """

    statement: str = Field(min_length=1, max_length=2000)
    basis: AssertionBasis
    turn_id: str | None = Field(default=None, max_length=64)


class Criterion(_Strict):
    """What the student says matters to the decision."""

    id: str = Field(min_length=1, max_length=32, pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    description: str = Field(min_length=1, max_length=2000)
    basis: CriterionBasis
    evidence_ids: tuple[str, ...] = ()


class Candidate(_Strict):
    """A possible path under investigation.

    ``program_handles`` and ``course_codes`` are discovery handles and search
    hypotheses. A candidate combination is not a persisted degree pathway and
    never supplies a prerequisite edge.
    """

    id: str = Field(min_length=1, max_length=32, pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    program_handles: tuple[str, ...] = ()
    course_codes: tuple[str, ...] = ()
    description: str = Field(max_length=4000)
    status: CandidateStatus
    strengths: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()
    objections: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()


class OpenQuestion(_Strict):
    question: str = Field(min_length=1, max_length=1000)
    importance: QuestionImportance
    next_action: QuestionNextAction


class ResearchState(_Strict):
    """The model's own bounded working comparison.

    These are labeled model assessments. They are never promoted into citable
    passages and never become institutionally verified facts.
    """

    revision: int = Field(ge=0)
    decision_question: str = Field(min_length=1, max_length=4000)
    student_context: tuple[StudentContext, ...] = ()
    criteria: tuple[Criterion, ...] = Field(default=(), max_length=MAX_CRITERIA)
    candidates: tuple[Candidate, ...] = Field(default=(), max_length=MAX_CANDIDATES)
    open_questions: tuple[OpenQuestion, ...] = Field(
        default=(), max_length=MAX_OPEN_QUESTIONS
    )
    active_evidence_ids: tuple[str, ...] = ()


# --- Final decision ----------------------------------------------------------


class DecisionBlock(_Strict):
    """One typed part of the answer.

    ``fact`` requires supporting ``evidence_ids`` for its institutional or
    external claims. ``judgment`` may express a preference and must name the
    facts behind it. ``question`` needs no evidence. Text cannot escape factual
    validation by labeling itself judgment or limitation; that is a review
    responsibility, not something this schema can prove.
    """

    kind: BlockKind
    text: str = Field(min_length=1, max_length=20_000)
    evidence_ids: tuple[str, ...] = ()
    candidate_ids: tuple[str, ...] = ()


class PreparationClaim(_Strict):
    """An asserted readiness/ordering conclusion, tied to an executed check.

    This is an audit contract: the conclusion must match a deterministic check
    the server actually ran against the same asserted completed courses. Language
    alone cannot establish a successful check.
    """

    check_id: str = Field(min_length=1, max_length=64)
    selected_codes: tuple[str, ...] = ()
    completed_codes: tuple[str, ...] = ()
    conclusion: PreparationConclusion


class FinalDecision(_Strict):
    """The typed final answer proposal.

    It proposes; the backend validates and publishes. ``candidate_ids`` and
    ``preparation_check_ids`` must refer to things that exist in the run.
    """

    outcome: DecisionOutcome
    completeness: DecisionCompleteness
    candidate_ids: tuple[str, ...] = ()
    blocks: tuple[DecisionBlock, ...] = Field(
        default=(), max_length=MAX_DECISION_BLOCKS
    )
    # Candidate course codes are research hypotheses; this list names courses
    # actually recommended in the answer, distinct from a program's requirement list.
    recommended_course_codes: tuple[str, ...] = Field(default=(), max_length=30)
    preparation_check_ids: tuple[str, ...] = ()
    preparation_claims: tuple[PreparationClaim, ...] = ()
    unresolved_questions: tuple[str, ...] = Field(
        default=(), max_length=MAX_UNRESOLVED_QUESTIONS
    )


# --- Pure structural checks --------------------------------------------------


def decision_text_bytes(decision: FinalDecision) -> int:
    """Total UTF-8 bytes of every block's text, as the output bound counts it."""
    return sum(len(block.text.encode("utf-8")) for block in decision.blocks)


def question_blocks(decision: FinalDecision) -> int:
    return sum(1 for block in decision.blocks if block.kind == "question")


def validate_decision_limits(decision: FinalDecision) -> list[str]:
    """Return structural problems in a proposed decision. Empty means no problem.

    These are shape and budget checks only. Whether a ``fact`` is actually
    supported by its cited evidence is a separate, semantic question.
    """
    problems: list[str] = []
    if len(decision.blocks) > MAX_DECISION_BLOCKS:
        problems.append(f"more than {MAX_DECISION_BLOCKS} blocks")
    total = decision_text_bytes(decision)
    if total > MAX_DECISION_TEXT_BYTES:
        problems.append(f"block text is {total} bytes, over {MAX_DECISION_TEXT_BYTES}")
    if question_blocks(decision) > MAX_QUESTIONS_PER_TURN:
        problems.append(f"more than {MAX_QUESTIONS_PER_TURN} questions in one turn")
    if len(decision.unresolved_questions) > MAX_UNRESOLVED_QUESTIONS:
        problems.append(f"more than {MAX_UNRESOLVED_QUESTIONS} unresolved questions")
    return problems


def validate_evidence_references(
    decision: FinalDecision, supplied: set[str]
) -> list[str]:
    """Reject evidence IDs that were never supplied to the model in this run.

    A stale history citation or a discovery snippet cannot become a valid
    evidence reference. Returns one message per unverifiable reference.
    """
    problems: list[str] = []
    for index, block in enumerate(decision.blocks):
        for evidence_id in block.evidence_ids:
            if evidence_id not in supplied:
                problems.append(
                    f"block {index} ({block.kind}) cites evidence {evidence_id!r} "
                    "that was not supplied in this run"
                )
    return problems


def validate_candidate_references(
    decision: FinalDecision, known_candidates: set[str]
) -> list[str]:
    """Reject candidate IDs the run never established."""
    problems: list[str] = []
    for candidate_id in decision.candidate_ids:
        if candidate_id not in known_candidates:
            problems.append(f"unknown candidate id {candidate_id!r}")
    for index, block in enumerate(decision.blocks):
        for candidate_id in block.candidate_ids:
            if candidate_id not in known_candidates:
                problems.append(
                    f"block {index} ({block.kind}) references unknown candidate {candidate_id!r}"
                )
    return problems


def validate_fact_support(decision: FinalDecision) -> list[str]:
    """Every ``fact`` block must name at least one supporting evidence item.

    A ``question`` may stand alone. A ``limitation`` or ``assumption`` needs no
    decorative citation, but a factual prohibition embedded in a limitation is
    still a factual claim and must carry evidence.
    """
    problems: list[str] = []
    for index, block in enumerate(decision.blocks):
        if block.kind == "fact" and not block.evidence_ids:
            problems.append(f"block {index} is a fact with no supporting evidence")
    return problems
