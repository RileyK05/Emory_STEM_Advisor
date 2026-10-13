"""Behavior of the research decision/state validators.

These are the structural checks on a model's proposed answer: they bound it and
reject unverifiable references. They deliberately do not prove entailment, so
nothing here asserts that a cited passage supports a claim.
"""

from __future__ import annotations

import pytest

from app.advising.research_types import (
    DecisionBlock,
    FinalDecision,
    validate_candidate_references,
    validate_decision_limits,
    validate_evidence_references,
    validate_fact_support,
)


def _decision(**kwargs) -> FinalDecision:
    kwargs.setdefault("outcome", "recommendation")
    kwargs.setdefault("completeness", "complete")
    return FinalDecision(**kwargs)


@pytest.mark.parametrize(
    ("blocks", "expected_problem"),
    [
        ((DecisionBlock(kind="fact", text="CS 377 exists.", evidence_ids=("e1",)),), None),
        # A question may stand alone with no evidence at all.
        ((DecisionBlock(kind="question", text="Which matters more?"),), None),
        # An assumption rests on the student's report; no decorative citation.
        ((DecisionBlock(kind="assumption", text="You have taken calculus."),), None),
        # A limitation describing investigation scope needs no citation either.
        ((DecisionBlock(kind="limitation", text="I could not check every program."),), None),
        # But an uncited fact is a factual claim with no support.
        ((DecisionBlock(kind="fact", text="CS 377 exists."),), "fact with no supporting evidence"),
    ],
)
def test_fact_blocks_need_support_and_other_kinds_do_not(blocks, expected_problem):
    problems = validate_fact_support(_decision(blocks=blocks))
    if expected_problem is None:
        assert problems == []
    else:
        assert any(expected_problem in problem for problem in problems)


def test_evidence_outside_the_run_is_rejected_but_supplied_evidence_is_not():
    block = DecisionBlock(kind="fact", text="MATH 221 is 4 credits.", evidence_ids=("e2", "e9"))
    decision = _decision(blocks=(block,))
    problems = validate_evidence_references(decision, {"e1", "e2"})
    assert len(problems) == 1
    assert "e9" in problems[0]


def test_stale_history_citation_is_not_a_valid_reference():
    # A previously read but now inactive passage must not be attachable.
    decision = _decision(blocks=(DecisionBlock(kind="fact", text="Old claim.", evidence_ids=("e7",)),))
    assert validate_evidence_references(decision, set()) != []


def test_candidate_references_must_exist_in_the_run():
    decision = _decision(candidate_ids=("option-a", "option-ghost"))
    problems = validate_candidate_references(decision, {"option-a"})
    assert len(problems) == 1
    assert "option-ghost" in problems[0]


def test_block_candidate_references_are_checked_too():
    decision = _decision(
        blocks=(DecisionBlock(kind="judgment", text="Lean toward this.", candidate_ids=("gone",)),)
    )
    assert validate_candidate_references(decision, set()) != []


@pytest.mark.parametrize(
    ("blocks", "expected_problem"),
    [
        # Too many questions in one turn; the plan bounds this at two.
        (
            tuple(
                DecisionBlock(kind="question", text=f"Question {index}?")
                for index in range(3)
            ),
            "more than 2 questions in one turn",
        ),
        # Block text is counted in UTF-8 bytes, so multi-byte characters count.
        (
            (DecisionBlock(kind="judgment", text="x" * 20_000),),
            "block text is",
        ),
    ],
)
def test_decision_limits_are_enforced(blocks, expected_problem):
    problems = validate_decision_limits(_decision(blocks=blocks))
    assert any(expected_problem in problem for problem in problems)


def test_a_bounded_decision_reports_no_problems():
    decision = _decision(
        blocks=(
            DecisionBlock(kind="fact", text="The CS BS requires CS 377.", evidence_ids=("e1",)),
            DecisionBlock(kind="judgment", text="This fits you better.", candidate_ids=()),
            DecisionBlock(kind="question", text="Depth or flexibility?"),
        )
    )
    assert validate_decision_limits(decision) == []


@pytest.mark.parametrize("decision, expected", [
    (_decision(outcome="information"), "at least one"),
    (_decision(outcome="information", blocks=(DecisionBlock(kind="limitation", text="See [12]."),)), "raw citation"),
    (_decision(outcome="information", preparation_check_ids=("ghost",),
               blocks=(DecisionBlock(kind="question", text="Which goal?"),)), "not executed"),
    (_decision(outcome="recommendation", candidate_ids=("ghost",),
               blocks=(DecisionBlock(kind="judgment", text="Choose this."),)), "unknown candidate"),
    (_decision(outcome="recommendation", recommended_course_codes=("CS 170",),
               blocks=(DecisionBlock(kind="question", text="Which goal?"),)), "read recommended course"),
])
def test_research_finish_rejects_unrenderable_or_unexecuted_references(decision, expected):
    from app.generation.research_final import DecisionRejected, validate_final

    with pytest.raises(DecisionRejected, match=expected):
        validate_final(decision, None, [], set(), {})


@pytest.mark.parametrize("prompt_bytes", [9000, 12000])
def test_context_compaction_preserves_exact_passages_and_accounts_omission_metadata(prompt_bytes):
    import json
    from dataclasses import replace
    from types import SimpleNamespace
    from uuid import uuid4

    from app.advising.research_types import EvidenceRecord
    from app.config import load_settings
    from app.generation.research_controller import ResearchController
    from app.llm.base import AssistantToolMessage, ToolCall, ToolResultMessage
    from app.operations.budget import estimate_prompt_reservation

    records = [EvidenceRecord(evidence_id=f"e{n}", source_id=str(uuid4()), source_revision=1,
        source_kind="institutional_catalog", source_name="Catalog", chunk_id=str(uuid4()),
        locator_label="Requirements", exact_text="Conditional requirement λ. " * 80,
        text_digest="a" * 64, acquired_step=n, content_completeness="complete") for n in range(1, 21)]
    session = SimpleNamespace(settings=replace(load_settings(), research_max_prompt_bytes=prompt_bytes),
        store=SimpleNamespace(evidence=lambda _run: records), run=SimpleNamespace(run_id=uuid4()),
        active=[r.evidence_id for r in records], state=None, scope=SimpleNamespace(sources={}, programs={}),
        handles={}, checks={}, actions=[], program_reads={}, supplied=set(), remaining_limits={})
    previous = [AssistantToolMessage((ToolCall("read", "advisor_read_document", {}),)),
                ToolResultMessage("read", "advisor_read_document", "x" * 7000)]
    request, user = ResearchController().request(session, SimpleNamespace(definitions=()),
        "Which option?", None, previous, "", False)
    context = json.loads(user)
    assert estimate_prompt_reservation(request) <= prompt_bytes
    assert context["omitted_evidence_ids"] and context["exact_evidence_untrusted"]
    assert len(request.messages) == 2  # Drop the whole native round, never an orphan result.
    supplied = {e["evidence_id"] for e in context["exact_evidence_untrusted"]}
    assert session.supplied == supplied
    assert supplied.isdisjoint(context["omitted_evidence_ids"])
    assert all(e["exact_text"] == records[int(e["evidence_id"][1:]) - 1].exact_text
               for e in context["exact_evidence_untrusted"])
