"""Behavior of the research decision/state validators.

These are the structural checks on a model's proposed answer: they bound it and
reject unverifiable references. They deliberately do not prove entailment, so
nothing here asserts that a cited passage supports a claim.
"""

from __future__ import annotations

import json

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
    (_decision(outcome="information", blocks=(DecisionBlock(kind="judgment",
               text="The captured page does not establish next-cycle applicability."),)), "use fact or limitation"),
])
def test_research_finish_rejects_unrenderable_or_unexecuted_references(decision, expected):
    from app.generation.research_final import DecisionRejected, validate_final

    with pytest.raises(DecisionRejected, match=expected):
        validate_final(decision, None, [], set(), {})


def test_cited_program_requirement_is_a_mention_not_an_unread_recommendation():
    """EMO-plan: distinguish required-course mentions from recommendations to enroll."""
    from uuid import uuid4

    from app.advising.research_types import Candidate, EvidenceRecord, FinalDecision, ResearchState
    from app.generation.research_final import DecisionRejected, validate_final

    record = EvidenceRecord(
        evidence_id="e1", source_id=str(uuid4()), source_revision=1,
        source_kind="institutional_catalog", source_name="Chemistry (BA)",
        chunk_id=str(uuid4()), locator_label="Requirements",
        exact_text="Requires CHEM 150 and BIOL 141.", text_digest="a" * 64,
        acquired_step=1, content_completeness="complete", entity_refs=("h_prog",))
    state = ResearchState(revision=0, decision_question="Which major?",
        candidates=(Candidate(id="bio", description="Biology", status="investigating",
                              program_handles=("h_prog",)),))
    decision = FinalDecision(outcome="recommendation", completeness="complete",
        candidate_ids=("bio",), recommended_course_codes=("CHEM 150", "CS 370"),
        blocks=(
            DecisionBlock(kind="fact", text="CHEM 150 is required.", evidence_ids=("e1",)),
            DecisionBlock(kind="judgment", text="Chemistry fits you.", candidate_ids=("bio",),
                          evidence_ids=("e1",)),
        ))
    # CS 370 is not in the cited program evidence, so it still needs its own read.
    with pytest.raises(DecisionRejected, match="read recommended course CS 370"):
        validate_final(decision, state, [record], {"e1"}, {})
    # CHEM 150 is a cited program requirement mention, not an unread recommendation.
    mentioned_only = decision.model_copy(update={"recommended_course_codes": ("CHEM 150",)})
    validate_final(mentioned_only, state, [record], {"e1"}, {})


@pytest.mark.parametrize("prompt_bytes,workspace_loaded", [(9000, False), (12000, False), (48000, True)])
@pytest.mark.parametrize("scope_status,edition,notice", [
    ("historical", "2025-26", "historical_source"),
    ("unknown", None, "edition_not_recorded"),
    ("reviewed_default", "2026-27", "guidance_requires_passage_scope_check"),
])
def test_context_compaction_preserves_exact_passages_and_accounts_omission_metadata(
        prompt_bytes, workspace_loaded, scope_status, edition, notice):
    import json
    from dataclasses import replace
    from types import SimpleNamespace
    from uuid import UUID, uuid4

    from app.advising.research_repository import ChunkRead, SourcePin
    from app.advising.research_store import Acquisition
    from app.advising.research_types import EvidenceRecord
    from app.config import load_settings
    from app.generation.research_controller import ResearchController
    from app.generation.research_tools import ResearchSession
    from app.llm.base import AssistantToolMessage, ToolCall, ToolResultMessage
    from app.operations.budget import estimate_prompt_bytes
    from tests.helpers import lenient_config

    source = str(uuid4())
    records = [EvidenceRecord(evidence_id=f"e{n}", source_id=source, source_revision=1,
        source_kind="institutional_publication", source_name="Handbook", chunk_id=str(uuid4()),
        edition=edition, applicability_status=scope_status, metadata_revision=1,
        document_roles=("requirements", "advising_guidance"),
        applicability={"school": ("College",), "academic_year": ("2025-26",)},
        locator_label="Requirements", exact_text="Conditional requirement λ. " * 80,
        text_digest="a" * 64, acquired_step=n, content_completeness="complete") for n in range(1, 21)]
    session = ResearchSession(None, SimpleNamespace(sources={}, programs={}),
        SimpleNamespace(evidence=lambda _run: records), SimpleNamespace(run_id=uuid4()),
        settings=replace(load_settings(), research_max_prompt_bytes=prompt_bytes),
        config=lenient_config(), embedder=None)
    session.active = [r.evidence_id for r in records]
    registry = SimpleNamespace(definitions=())
    history = None
    if workspace_loaded:
        from app.advising.research_types import ResearchNote, ResearchState
        from app.conversations.store import research_history_prompt

        session.workspace = object()
        session.prior_workspace = {"notebook": ResearchState(revision=0, decision_question="Continue",
            notes=tuple(ResearchNote(id=f"n{i}", text="z" * 850, basis="user_report") for i in range(8))
        ).model_dump(mode="json")}
        for _ in range(40):
            sid = uuid4()
            session.scope.sources[sid] = SourcePin(sid, 1, "λ" * 180, "text/plain", None,
                "institutional_publication", title="A long discovery title " * 20)
            session.handle("document", sid)
        session.recall_page = {"data": {"references": [{"handle": f"h_memory{i}",
            "source_name": "λ" * 180, "locator_label": "λ" * 180} for i in range(12)]}}
        session.conversation_page = {"status": "ok", "data": {"turns_untrusted": [
            {"sequence": 1, "role": "user", "text": "Remember this preference. " * 200}]},
            "next_cursor": "c_next"}
        history = research_history_prompt([{"sequence": 2, "role": "user", "text": "h" * 5000,
                                           "evidence": {}}], max_bytes=6000)
        registry = session.registry()
    previous = [AssistantToolMessage((ToolCall("read", "advisor_read_document", {}),)),
                ToolResultMessage("read", "advisor_read_document", "x" * 7000)]
    request, user = ResearchController().request(session, registry,
        "Which option?", history, previous, "", False)
    context = json.loads(user)
    assert estimate_prompt_bytes(request) <= prompt_bytes
    assert context["omitted_evidence_ids"] and context["exact_evidence_untrusted"]
    if workspace_loaded:
        assert context["conversation_page_untrusted"] == session.conversation_page
        assert context["prior_workspace_untrusted"] == session.prior_workspace
        assert context["context_omissions"]
    assert len(request.messages) == 2  # Drop the whole native round, never an orphan result.
    supplied = {e["evidence_id"] for e in context["exact_evidence_untrusted"]}
    assert session.supplied == supplied
    assert supplied.isdisjoint(context["omitted_evidence_ids"])
    assert all(e["exact_text"] == records[int(e["evidence_id"][1:]) - 1].exact_text
               for e in context["exact_evidence_untrusted"])
    # Compaction retains the cited edition/scope, and groups reminders only for
    # actually supplied evidence. Neither a discovery lead nor an omitted chunk
    # can establish applicability by surviving in a source-level summary.
    assert all(e["edition"] == edition and e["applicability_status"] == scope_status
               and e["applicability"]["academic_year"] == ["2025-26"]
               for e in context["exact_evidence_untrusted"])
    assert len(context["source_scope_notices"]) == 1
    source_notice = context["source_scope_notices"][0]
    assert set(source_notice["evidence_ids"]) == supplied
    assert notice in source_notice["notices"]
    # A read of a previously omitted chunk promotes its existing server-issued
    # ID; it does not authorize other omitted chunks or synthesize a new passage.
    reread = records[-1]
    assert reread.evidence_id in context["omitted_evidence_ids"]
    source_id = UUID(reread.source_id)
    session.scope.sources[source_id] = SourcePin(source_id, 1, "handbook.md", "text/markdown",
        None, "institutional_publication", edition=edition, applicability_status=scope_status)
    session.store.acquire = lambda *_args, **_kwargs: Acquisition(0, (reread.evidence_id,), True)
    session.config = lenient_config()
    session.execution_id = None
    session.entity_evidence, session.new_evidence, session.read_count = {}, 0, 0
    ResearchSession.acquire(session, [ChunkRead(UUID(reread.chunk_id), source_id, 1, 0, None,
        reread.locator_label, reread.exact_text)], action="read_document")
    request, user = ResearchController().request(session, registry,
        "Which option?", history, [], "", False)
    refreshed = json.loads(user)
    actual = {record["evidence_id"] for record in refreshed["exact_evidence_untrusted"]}
    assert reread.evidence_id in actual and session.supplied == actual
    assert set(refreshed["omitted_evidence_ids"]).isdisjoint(actual)
    assert refreshed["omitted_evidence_ids"]
    exact = next(record for record in refreshed["exact_evidence_untrusted"]
                 if record["evidence_id"] == reread.evidence_id)
    assert exact["chunk_id"] == reread.chunk_id and exact["exact_text"] == reread.exact_text
    assert estimate_prompt_bytes(request) <= prompt_bytes


@pytest.mark.parametrize("notes,valid", [
    ([], True),
    ([{"id": "idea", "text": "Consider a minor", "basis": "hypothesis"}], True),
    ([{"id": "source", "text": "A summary", "basis": "evidence_summary", "evidence_ids": ["e1"]}], True),
    ([{"id": "source", "text": "A summary", "basis": "evidence_summary"}], False),
])
def test_state_input_allows_an_omitted_expected_revision(notes, valid):
    """A provider that omits the revision must not strand its candidates."""
    from pydantic import ValidationError

    from app.generation.research_tools import StateInput

    arguments = {"state": {"revision": 0, "decision_question": "Which path?", "notes": notes}}
    if not valid:
        with pytest.raises(ValidationError):
            StateInput.model_validate(arguments)
        return
    parsed = StateInput.model_validate(
        arguments)
    assert parsed.expected_revision is None
    assert parsed.state.decision_question == "Which path?"


def test_finish_input_accepts_json_encoded_and_double_wrapped_decision():
    """EMO-006: MiMo sends the decision as a JSON string, sometimes double-wrapped."""
    from app.generation.research_tools import FinishInput

    decision = {"outcome": "information", "completeness": "complete", "blocks": [
        {"kind": "fact", "text": "ECON 315 counts [1].", "evidence_ids": ["e1"]}]}
    direct = FinishInput.model_validate({"decision": decision})
    encoded = FinishInput.model_validate({"decision": json.dumps(decision)})
    wrapped = FinishInput.model_validate({"decision": json.dumps({"decision": decision})})
    assert direct.decision == encoded.decision == wrapped.decision
    assert direct.decision.blocks[0].text == "ECON 315 counts [1]."


@pytest.mark.parametrize("transport", ["direct", "wrapped", "encoded", "fenced", "bare_fenced"])
def test_terminal_json_uses_the_finish_validation_path(transport):
    from app.generation.research_controller import _finish_text_call
    from app.generation.research_tools import FinishInput

    decision = {"outcome": "insufficient_evidence", "completeness": "partial", "blocks": [
        {"kind": "limitation", "text": "The required evidence is unavailable."}]}
    payload = json.dumps(decision)
    if transport == "wrapped":
        payload = json.dumps({"decision": decision})
    elif transport == "encoded":
        payload = json.dumps({"decision": payload})
    elif transport == "fenced":
        payload = "```json\n" + payload + "\n```"
    elif transport == "bare_fenced":
        payload = "```\n" + payload + "\n```"
    call = _finish_text_call(payload)
    assert call.name == "advisor_finish"
    assert FinishInput.model_validate(call.arguments).decision.model_dump(mode="json")["blocks"][0]["text"] == decision["blocks"][0]["text"]


@pytest.mark.parametrize("payload", [
    'Here is the answer: {"decision": {}}',
    '{"decision": {}} trailing text',
    '{"decision": {}} {"decision": {}}',
    '{"decision": {}, "decision": {}}',
    '{"decision": {"outcome": NaN}}',
    '{"name": "advisor_search", "arguments": {}}',
    '[{"decision": {}}]',
    '{"decision": {}, "unresolved_questions": []}',
])
def test_terminal_json_rejects_ambiguous_or_nonterminal_text(payload):
    from app.generation.research_controller import _finish_text_call

    assert _finish_text_call(payload) is None


@pytest.mark.parametrize("support", ["program", "course", "preparation", "oversized_preparation"])
def test_finish_request_retains_declared_candidate_program_evidence(support):
    """A compared candidate's program evidence must survive prompt compaction.

    Otherwise the model cannot cite it (omitted IDs may never be cited) and the
    recommendation can never pass the finish validator.
    """
    from dataclasses import replace
    from types import SimpleNamespace
    from uuid import uuid4

    from app.advising.research_types import Candidate, EvidenceRecord, ResearchState
    from app.config import load_settings
    from app.generation.research_controller import ResearchController

    records = [EvidenceRecord(evidence_id=f"e{n}", source_id=str(uuid4()), source_revision=1,
        source_kind="institutional_catalog", source_name="Catalog", chunk_id=str(uuid4()),
        locator_label="Requirements", exact_text="Conditional requirement λ. " * 80,
        text_digest="a" * 64, acquired_step=n, content_completeness="complete")
        for n in range(1, 21)]
    state = ResearchState(revision=0, decision_question="Which option?",
        candidates=(Candidate(id="bio", description="Biology", status="investigating",
                              program_handles=("h_a",)),))
    session = SimpleNamespace(
        settings=replace(load_settings(), research_max_prompt_bytes=(
            16000 if support == "preparation" else 9000 if support == "oversized_preparation" else 12000)),
        store=SimpleNamespace(evidence=lambda _run: records), run=SimpleNamespace(run_id=uuid4()),
        active=[r.evidence_id for r in records], state=state, entity_evidence={"h_a": {"e20"}},
        scope=SimpleNamespace(sources={}, programs={}),
        handles={}, checks={}, actions=[], program_reads={}, supplied=set(), remaining_limits={})
    if support == "program":
        session.active = [r.evidence_id for r in records[:-1]]
    if support == "course":
        session.state = ResearchState(revision=0, decision_question="Which course?",
            candidates=(Candidate(id="bio", course_codes=("BIOL 101",), description="Biology",
                                  status="investigating"),))
        session.entity_evidence = {"BIOL 101": {"e20"}}
        session.active = [r.evidence_id for r in records[:-1]]
    elif support != "program":
        session.state = None
        session.checks = {"p_test": {"check_id": "p_test", "selected_codes": ["BIOL 101"],
            "completed_codes": [], "stages": [["BIOL 101"]], "blockers": [], "complete": True,
            "conclusion": "dependency_order", "evidence_ids": ["e19", "e20"],
            "unexplored_codes": [], "enrollment_eligibility_verified": False,
            "scope": "prerequisite_order_only"}}
        # A model notebook update may have deactivated the check's evidence.
        session.active = [r.evidence_id for r in records[:-2]]
    history = json.dumps({"kind": "selected_conversation_history", "total_turns": 1,
        "omitted_turns": 0, "turns": [{"sequence": 1, "text": "Older discussion. " * 200}]})
    _request, user = ResearchController().request(
        session, SimpleNamespace(definitions=()), "Which option?", history, [], "", True)
    if support == "oversized_preparation":
        context = json.loads(user)
        assert context["executed_preparation_checks"] == []
        assert context["unavailable_preparation_check_ids"] == ["p_test"]
        assert "readiness claims" in context["instruction"]
        return
    # Required evidence must survive even when later reads fill the context.
    assert "e20" in session.supplied
    assert "e20" in user
    assert len(session.supplied) < 20  # the bound really did omit other evidence
    if support == "preparation":
        assert {"e19", "e20"} <= session.supplied
        assert json.loads(user)["executed_preparation_checks"][0]["check_id"] == "p_test"
