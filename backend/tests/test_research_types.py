"""Behavior of the research decision/state validators.

These are the structural checks on a model's proposed answer: they bound it and
reject unverifiable references. They deliberately do not prove entailment, so
nothing here asserts that a cited passage supports a claim.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.advising.comparison import MENTION_OVERLAP_ONLY, MentionComparison, MentionRef
from app.advising.research_types import (
    ArtifactRequest,
    ArtifactRevisionReference,
    DecisionBlock,
    FinalDecision,
    validate_candidate_references,
    validate_decision_limits,
    validate_evidence_references,
    validate_fact_support,
)
from app.generation.research_tools import CompareInput, ResearchSession


def _decision(**kwargs) -> FinalDecision:
    kwargs.setdefault("outcome", "recommendation")
    kwargs.setdefault("completeness", "complete")
    return FinalDecision(**kwargs)


@pytest.mark.parametrize("purpose,revises,error", [
    ("create", {"artifact_id": uuid4(), "revision_id": uuid4()}, "create forbids revises"),
    ("revise", None, "revise requires revises"),
    ("summary", None, "summary requires a document"),
])
def test_artifact_request_requires_a_consistent_purpose(purpose, revises, error):
    with pytest.raises(ValueError, match=error):
        ArtifactRequest(kind="workbook" if purpose == "summary" else "document",
                        title="Plan", brief="Make it.", purpose=purpose, revises=revises)


def test_artifact_request_accepts_referenced_revision():
    revises = ArtifactRevisionReference(artifact_id=uuid4(), revision_id=uuid4())
    request = ArtifactRequest(kind="document", title="Plan", brief="Revise it.",
                              purpose="revise", revises=revises)
    assert request.revises == revises


def test_research_preparation_serializes_bounded_grouped_alternatives(monkeypatch):
    from app.atlas.requisites import AllOf, AnyOf, Condition, CourseRef
    from app.generation import research_tools
    from app.generation.research_tools import PreparationInput, ResearchSession

    requirement = AnyOf((AllOf((CourseRef("MATH 111"), CourseRef("MATH 112"))),
                         Condition("equivalent transfer credit"),
                         *(CourseRef(f"X {index}") for index in range(140))))
    monkeypatch.setattr(research_tools, "read_courses", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(research_tools, "load_advising_context", lambda *_args, **_kwargs: {
        "courses": [{"code": "ECON 201", "title": "Economics", "parse_status": "parsed",
                     "requirements_evidence_complete": True, "prerequisites": requirement,
                     "corequisites": None, "requisites_raw": "test", "blocker_reason": None}],
    })
    session = ResearchSession.__new__(ResearchSession)
    # The preparation check reads the collection's campus equivalences; this
    # collection has none.
    session.conn = SimpleNamespace(
        commit=lambda: None,
        execute=lambda *_args, **_kwargs: SimpleNamespace(fetchall=list),
    )
    session.scope = SimpleNamespace(collection_id="collection", sources={})
    session.acquire = lambda *_args, **_kwargs: []
    session.checks = {}
    session.store = SimpleNamespace(record_action=lambda *_args, **_kwargs: None)
    session.run = SimpleNamespace(run_id="run")
    result = session.preparation(PreparationInput(selected_codes=["ECON 201"], completed_codes=[]), None)
    blocker = next(item for item in result.data["blockers"] if item["reason"] == "alternative_required")
    assert blocker["alternative_groups"][0] == ["MATH 111", "MATH 112"]
    assert blocker["alternatives"] == []
    assert blocker["alternative_groups_truncated"] is True
    assert len(blocker["alternative_groups"]) == 128


@pytest.mark.parametrize(("question", "history", "completed", "unstated"), [
    # The sweep's FREN 351 miss: FREN 314 required FREN 310, so the model
    # counted FREN 310 as done and answered "yes".
    ("i've taken FREN 314. can i take FREN 351?", None, ["FREN 314", "FREN 310"], "FREN 310"),
    ("i've taken FREN 314. can i take FREN 351?", None, ["FREN 314"], None),
    # The other campus's offering of a stated course is the same course.
    ("i've taken FREN 314. can i take FREN 351?", None, ["FREN_OX 314"], None),
    # A course named in an earlier turn was stated.
    ("can i take FREN 351 now?", "Student: I finished FREN 310 and FREN 314.", ["FREN 310"], None),
])
def test_research_preparation_counts_only_courses_the_student_stated(
        monkeypatch, question, history, completed, unstated):
    from app.generation import research_tools
    from app.generation.research_tools import (
        InvalidResearchRecord,
        PreparationInput,
        ResearchSession,
    )

    monkeypatch.setattr(research_tools, "read_courses", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(research_tools, "load_advising_context", lambda *_a, **_k: {"courses": []})
    monkeypatch.setattr(research_tools, "load_campus_equivalents", lambda *_a, **_k: {
        "FREN 314": frozenset({"FREN_OX 314"}), "FREN_OX 314": frozenset({"FREN 314"})})
    session = ResearchSession.__new__(ResearchSession)
    session.conn = SimpleNamespace(commit=lambda: None)
    session.scope = SimpleNamespace(collection_id="collection", sources={})
    session.acquire = lambda *_args, **_kwargs: []
    session.checks = {}
    session.store = SimpleNamespace(record_action=lambda *_args, **_kwargs: None)
    session.run = SimpleNamespace(run_id="run")
    session.question, session.history_context = question, history
    value = PreparationInput(selected_codes=["FREN 351"], completed_codes=completed)
    if unstated:
        with pytest.raises(InvalidResearchRecord, match=unstated):
            session.preparation(value, None)
    else:
        assert session.preparation(value, None).data["completed_codes"] == completed


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
            "reply is",
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


@pytest.mark.parametrize("decision, rejected", [
    # Whatever the model wrote, the student sees only the fixed sentence.
    (_decision(outcome="declined", completeness="partial", blocks=(
        DecisionBlock(kind="limitation", text="Arr, I be no pirate."),)), None),
    (_decision(outcome="declined"), None),
    # A decline that cites or recommends is an answer, and is sent back.
    (_decision(outcome="declined", blocks=(
        DecisionBlock(kind="fact", text="CS 170 is offered.", evidence_ids=("e1",)),)), "declined"),
    (_decision(outcome="declined", recommended_course_codes=("CS 170",)), "declined"),
])
def test_a_declined_finish_publishes_only_the_fixed_scope_sentence(decision, rejected):
    from app.generation.research_final import (
        SCOPE_DECLINE_TEXT,
        DecisionRejected,
        declined_decision,
    )

    if rejected:
        with pytest.raises(DecisionRejected, match=rejected):
            declined_decision(decision)
        return
    published = declined_decision(decision)
    assert [(b.kind, b.text) for b in published.blocks] == [("limitation", SCOPE_DECLINE_TEXT)]
    assert published.completeness == "complete" and not published.unresolved_questions


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
    # A program mention can be stated as a sourced fact, but a course listed as
    # recommended requires its own read, including when the mention is positive.
    mentioned_only = decision.model_copy(update={"recommended_course_codes": ()})
    validate_final(mentioned_only, state, [record], {"e1"}, {})
    recommended = decision.model_copy(update={"recommended_course_codes": ("CHEM 150",)})
    with pytest.raises(DecisionRejected, match="read recommended course CHEM 150"):
        validate_final(recommended, state, [record], {"e1"}, {})
    course = record.model_copy(update={"evidence_id": "e2", "source_name": "CHEM 150",
                                       "exact_text": "CHEM 150: Introductory Chemistry.",
                                       "entity_refs": ("CHEM 150",)})
    supported = recommended.model_copy(update={"blocks": (
        DecisionBlock(kind="fact", text="CHEM 150 is required.", evidence_ids=("e1", "e2")),
        DecisionBlock(kind="judgment", text="Chemistry fits you.", candidate_ids=("bio",),
                      evidence_ids=("e1",)),
    )})
    validate_final(supported, state, [record, course], {"e1", "e2"}, {})
    excluded = record.model_copy(update={"exact_text": "Electives exclude BIOL 120."})
    excluded_decision = decision.model_copy(update={
        "recommended_course_codes": ("BIOL 120",),
        "blocks": (DecisionBlock(kind="fact", text="BIOL 120 is excluded.", evidence_ids=("e1",)),
                   DecisionBlock(kind="judgment", text="Biology fits you.", candidate_ids=("bio",),
                                 evidence_ids=("e1",))),
    })
    with pytest.raises(DecisionRejected, match="read recommended course BIOL 120"):
        validate_final(excluded_decision, state, [excluded], {"e1"}, {})


def test_fact_block_cannot_name_a_course_absent_from_its_cited_evidence():
    from uuid import uuid4

    from app.advising.research_types import EvidenceRecord, FinalDecision
    from app.generation.research_final import DecisionRejected, validate_final

    record = EvidenceRecord(
        evidence_id="e1", source_id=str(uuid4()), source_revision=1,
        source_kind="institutional_catalog", source_name="Data Science (BS)",
        chunk_id=str(uuid4()), locator_label="Requirements",
        exact_text="QTM 110 and QTM 150 are required.", text_digest="a" * 64,
        acquired_step=1, content_completeness="complete", entity_refs=("h_prog",))
    bad = FinalDecision(outcome="information", completeness="complete", blocks=(
        DecisionBlock(kind="fact", text="DATASCI 350 is in the Data Science BS QTM list.",
                      evidence_ids=("e1",)),))
    with pytest.raises(DecisionRejected, match="DATASCI 350"):
        validate_final(bad, None, [record], {"e1"}, {})
    good = bad.model_copy(update={"blocks": (
        DecisionBlock(kind="fact", text="QTM 110 is required.", evidence_ids=("e1",)),)})
    validate_final(good, None, [record], {"e1"}, {})


def test_entry_point_claims_need_a_read_course_without_prerequisites():
    from uuid import uuid4

    from app.advising.research_types import EvidenceRecord, FinalDecision
    from app.generation.research_final import DecisionRejected, validate_final

    def record(evidence_id, text, refs):
        return EvidenceRecord(
            evidence_id=evidence_id, source_id=str(uuid4()), source_revision=1,
            source_kind="institutional_catalog", source_name="record",
            chunk_id=str(uuid4()), locator_label="Requirements", exact_text=text,
            text_digest="a" * 64, acquired_step=1, content_completeness="complete",
            entity_refs=refs)

    records = [
        record("e1", "ECON 101 introduces macroeconomics.", ("ECON 101",)),
        record("e2", "ECON 112. Prerequisite: ECON 101.", ("ECON 112",)),
        record("e3", "CS 453. Prerequisite: CS 350.", ("CS 453",)),
        record("e4", "ECON 112 is required.", ("h_prog",)),
    ]

    def decision(text, evidence_id):
        return FinalDecision(outcome="information", completeness="complete", blocks=(
            DecisionBlock(kind="fact", text=text, evidence_ids=(evidence_id,)),))

    with pytest.raises(DecisionRejected, match="without citing its course record"):
        validate_final(decision("ECON 112 is the entry point.", "e4"), None, records, {"e4"}, {})
    with pytest.raises(DecisionRejected, match="listing a prerequisite"):
        validate_final(decision("ECON 112 is the entry point.", "e2"), None, records, {"e2"}, {})
    validate_final(decision("ECON 112 is the starting point after ECON 101.", "e2"),
                   None, records, {"e2"}, {})
    validate_final(decision("ECON 101 is the entry point.", "e1"), None, records, {"e1"}, {})
    validate_final(decision("CS 453 is a later-stage step rather than an entry point.", "e3"),
                   None, records, {"e3"}, {})


@pytest.mark.parametrize("prompt_bytes,workspace_loaded", [(15000, False), (16000, False), (48000, True)])
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

    # Explicit sequence requests retain the deterministic check tool while
    # initial working state is being established. This prevents a malformed
    # optional state proposal from blocking the requested check.
    sequence_registry = session.registry()
    compact_limit = session.settings.research_max_prompt_bytes
    session.settings = replace(session.settings, research_max_prompt_bytes=100_000)
    sequence_request, sequence_user = ResearchController().request(
        session, sequence_registry, "Use the deterministic sequence tool for CS 350 prerequisites",
        history, [], "", False, state_required=True)
    sequence_context = json.loads(sequence_user)
    assert sequence_context["state_update_requested"] is True
    assert "advisor_check_preparation" in {tool.name for tool in sequence_request.tools}
    factual_request, _ = ResearchController().request(
        session, sequence_registry, "What does CS 350 cover?", history, [], "", False,
        state_required=True)
    assert "advisor_check_preparation" not in {tool.name for tool in factual_request.tools}
    session.settings = replace(session.settings, research_max_prompt_bytes=compact_limit)
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


_STATE = {"revision": 3, "decision_question": "Which path?", "notes": []}


@pytest.mark.parametrize("sent", [
    {**_STATE, "revision": "3"},
    {**_STATE, "revision": {"item": "3"}},
    [_STATE],
])
def test_state_input_undoes_provider_encodings_of_the_same_state(sent):
    from app.generation.research_tools import StateInput
    from app.tools.registry import repair_arguments

    expected = StateInput.model_validate(repair_arguments({"state": _STATE}, StateInput))
    assert StateInput.model_validate(repair_arguments({"state": sent}, StateInput)) == expected
    assert expected.state.revision == 3


_DECISION = {"outcome": "information", "completeness": "complete",
             "recommended_course_codes": ["ECON 315"], "blocks": [
                 {"kind": "fact", "text": "ECON 315 counts [1].", "evidence_ids": ["e1"]}]}


@pytest.mark.parametrize("sent", [
    # EMO-006: MiMo sends the decision as a JSON string, sometimes double-wrapped.
    json.dumps(_DECISION),
    json.dumps({"decision": _DECISION}),
    # deepseek via Ollama wraps field values as {"item": ...} and sends "" for [].
    {**_DECISION, "blocks": {"item": [{"kind": "fact", "text": "ECON 315 counts [1].",
                                       "evidence_ids": {"item": "e1"}}]},
     "recommended_course_codes": {"item": "ECON 315"}, "candidate_ids": "",
     "unresolved_questions": ""},
])
def test_finish_input_undoes_provider_encodings_of_the_same_decision(sent):
    from app.generation.research_tools import FinishInput

    expected = FinishInput.model_validate({"decision": _DECISION}).decision
    assert FinishInput.model_validate({"decision": sent}).decision == expected
    assert expected.blocks[0].evidence_ids == ("e1",)


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


def test_document_snippet_shows_the_query_dense_part_of_a_long_passage():
    from app.generation.research_tools import _query_snippet

    text = ("Library room reservations are unrelated. " * 40
            + "The S/U cap is 20 credit hours and ARCHE credit counts toward it. "
            + "More unrelated parking and transportation text. " * 40)
    snippet = _query_snippet(text, "S/U credit hours ARCHE cross-registration")
    assert "20 credit hours" in snippet and "ARCHE" in snippet
    assert snippet.index("20 credit hours") < 400


def test_policy_search_read_round_is_narrowed_to_the_matching_sections():
    from types import SimpleNamespace
    from uuid import uuid4

    from app.config import load_settings
    from app.generation.research_controller import ResearchController
    from app.llm.base import ToolDefinition

    session = SimpleNamespace(
        settings=load_settings(),
        store=SimpleNamespace(evidence=lambda _run: []), run=SimpleNamespace(run_id=uuid4()),
        active=[], state=None, entity_evidence={},
        scope=SimpleNamespace(sources={"source-1": SimpleNamespace(source_kind="institutional_policy")},
                              programs={}), handles={}, checks={}, actions=[],
        program_reads={}, program_positions={}, program_sizes={}, program_cursors={},
        supplied=set(), remaining_limits={}, pending_policy_reads=("h_policy", "h_policy_2"),
    )
    definitions = (
        ToolDefinition("advisor_read_document", "Read.", {"type": "object",
            "properties": {"handle": {"type": "string"}}, "required": []}),
        ToolDefinition("advisor_search", "Search.", {"type": "object", "properties": {}}),
        ToolDefinition("advisor_finish", "Finish.", {"type": "object", "properties": {}}),
    )
    request, _user = ResearchController().request(
        session, SimpleNamespace(definitions=definitions), "What is the policy?", None,
        [], "", False, read_required=True, policy_required=True)
    assert [tool.name for tool in request.tools] == ["advisor_read_document", "advisor_finish"]
    # Every section the search matched stays reachable: the rule may sit in a
    # section the keyword seam did not rank first.
    assert request.tools[0].input_schema["properties"]["handle"]["enum"] == [
        "h_policy", "h_policy_2"]


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
            20000 if support == "preparation" else 13000 if support == "oversized_preparation" else 16000)),
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


def test_an_exhausted_run_publishes_only_the_blocks_that_pass_alone(monkeypatch):
    from types import SimpleNamespace

    from app.advising.research_types import EvidenceRecord
    from app.generation import research_tools
    from app.generation.research_tools import ResearchSession

    monkeypatch.setattr(research_tools, "load_campus_equivalents", lambda *_a, **_k: {})
    record = EvidenceRecord(evidence_id="e1", source_id=str(uuid4()), source_revision=1,
        source_kind="institutional_policy", source_name="Policy", chunk_id=str(uuid4()),
        locator_label="Rules", exact_text="Students must earn 124 credit hours.",
        text_digest="a" * 64, acquired_step=1, content_completeness="complete")
    session = ResearchSession(None, SimpleNamespace(sources={}, programs={}, collection_id=None),
        SimpleNamespace(evidence=lambda _run: [record]), SimpleNamespace(run_id=uuid4()),
        settings=None, config=None, embedder=None)
    session._course_codes = frozenset()
    good = DecisionBlock(kind="fact", text="You need 124 credit hours.", evidence_ids=("e1",))
    unsupported = DecisionBlock(kind="fact", text="You need a 3.0.", evidence_ids=("e9",))
    assert session.salvage() is None  # Nothing was proposed.

    session.rejected_decision = _decision(blocks=(good, unsupported), candidate_ids=("gone",))
    salvaged = session.salvage()
    assert salvaged.blocks == (good,)
    assert (salvaged.outcome, salvaged.completeness, salvaged.candidate_ids) == (
        "information", "partial", ())

    session.rejected_decision = _decision(blocks=(unsupported,))
    assert session.salvage() is None  # No supported fact means no reply to keep.

    # The master settles a course that does not exist, so that sentence always survives.
    session.question = "what are the prereqs for PSYC 219"
    session.rejected_decision = _decision(blocks=(good, unsupported))
    assert [(b.kind, b.text) for b in session.salvage().blocks] == [
        ("limitation", "There is no PSYC 219 in the Course Atlas."), ("fact", good.text)]
    session.rejected_decision = _decision(blocks=(unsupported,))
    assert session.salvage().blocks[0].text == "There is no PSYC 219 in the Course Atlas."


@pytest.mark.parametrize("failure", ["TransientProviderError", "RateLimitError", "ToolConversationError"])
def test_a_provider_dropout_keeps_what_the_run_already_proposed(monkeypatch, failure):
    from types import SimpleNamespace

    from app import llm
    from app.generation.controller import ToolConversationError
    from app.generation.research_controller import ResearchController

    if failure == "ToolConversationError":
        error = ToolConversationError("final decision failed its bounded correction")
    else:
        error = getattr(llm.base, failure)("dropped", provider="fake")

    def dropped_after_one_call(session, *_args, **_kwargs):
        session.last_call = ("system", "user", "model")
        raise error

    controller = ResearchController()
    monkeypatch.setattr(controller, "_run", dropped_after_one_call)
    salvaged = _decision()
    session = SimpleNamespace(salvage=lambda: salvaged)
    decision, *_ = controller.run(session, None, None)
    assert decision is session.decision is salvaged

    with pytest.raises(type(error)) as exc_info:  # Nothing to keep, so the student is told to retry.
        controller.run(SimpleNamespace(salvage=lambda: None, last_call=("system", "user", "model")), None, None)
    if failure == "ToolConversationError":
        assert str(exc_info.value) == "The advisor couldn't answer that. Try again."


def test_opening_many_courses_is_one_evidence_step(monkeypatch):
    from types import SimpleNamespace

    from app.generation import research_tools
    from app.generation.research_tools import ResearchSession

    def fake_read(_conn, _scope, codes):
        assert len(codes) <= 4
        return [SimpleNamespace(code=code, canonical_code=code, chunks=tuple(
            SimpleNamespace(chunk_id=f"{code}-{n}", text="x" * 6_000) for n in range(4)))
            for code in codes]

    monkeypatch.setattr(research_tools, "read_courses", fake_read)
    session = ResearchSession(SimpleNamespace(commit=lambda: None), None, None, None,
                              settings=None, config=None, embedder=None)
    steps = []
    session.acquire = lambda chunks, **_kwargs: steps.append(len(chunks))
    session.entity_evidence["CS 100"] = {"e1"}

    opened = session._open_courses([f"CS {code}" for code in range(100, 110)])
    # One step, or the ledger breaks. One record a course, cut to the read limit, or
    # the term cards overflow the read and the student gets no answer.
    assert steps == [8]
    assert "CS 100" not in opened and len(opened) == 9



@pytest.mark.parametrize("provider,own_effort,research_effort,expected", [
    ("openai-compatible", None, "medium", "medium"),
    ("openai", None, "low", "low"),
    ("openai-compatible", "high", "medium", "high"),  # A connection's own choice stands.
    ("openai-compatible", "none", "medium", "none"),
    ("openai-compatible", None, None, None),  # Empty setting: greedy.
    ("anthropic", None, "medium", None),  # These reject the field outright.
    ("hf-local", None, "medium", None),
])
def test_research_asks_only_a_supporting_provider_for_reasoning(
        provider, own_effort, research_effort, expected):
    from app.api.query_service import research_sampling

    fields = research_sampling(SimpleNamespace(
        llm_provider=provider, llm_reasoning_effort=own_effort,
        research_reasoning_effort=research_effort))
    assert fields.get("llm_reasoning_effort", own_effort) == expected
    assert bool(fields) == (own_effort is None and expected is not None)
    if fields:  # The temperature rule: reasoning effort never travels with greedy.
        assert fields["llm_sampling_policy"] == "model_default"


def test_compare_rejects_duplicate_program_handles():
    session = ResearchSession.__new__(ResearchSession)
    session.program_reads = {"h1": object()}
    result = ResearchSession.compare(
        session, CompareInput(handles=["h1", "h1"]), _scope=None
    )
    assert result.status == "conflict"
    assert "distinct" in result.limitations[0].lower()


def test_compare_keeps_all_mention_refs_for_retained_codes():
    pid_a, pid_b = uuid4(), uuid4()
    refs = tuple(
        MentionRef(program_id=pid_a, position=i, text=f"line {i}", choose=None, cited=True)
        for i in range(6)
    )
    comparison = MentionComparison(
        kind=MENTION_OVERLAP_ONLY,
        programs=(pid_a, pid_b),
        shared={"CS 170": refs},
        unique={pid_a: {}, pid_b: {"MATH 111": refs[:3]}},
        unresolved_positions={},
    )

    session = ResearchSession.__new__(ResearchSession)
    session.program_reads = {"h1": object(), "h2": object()}
    session.program_positions = {"h1": [0, 1], "h2": [0, 1]}
    session.scope = object()
    session.conn = SimpleNamespace(commit=lambda: None)
    session.resolve = lambda handle, kind: object()

    import app.generation.research_tools as mod

    original = mod.compare_mentions
    original_read = mod.read_program
    mod.compare_mentions = lambda programs: comparison
    mod.read_program = lambda *args, **kwargs: SimpleNamespace(items=[0, 1])
    try:
        result = ResearchSession.compare(
            session, CompareInput(handles=["h1", "h2"]), _scope=None
        )
    finally:
        mod.compare_mentions = original
        mod.read_program = original_read

    assert result.status == "ok"
    shared_refs = result.data["comparison"]["shared"]["CS 170"]
    assert len(shared_refs) == 6


@pytest.mark.parametrize(("question", "asked"), [
    ("I've done CS 170 and MATH 111. Make a plan for the rest of the Computer Science BS.", True),
    ("Make a checklist of everything the Neuroscience and Behavioral Biology BS requires.", True),
    ("Compare the Computer Science BA and BS in a document I can keep.", True),
    ("I took MATH_OX 111 at Oxford. Plan my Chemistry BS from here.", True),
    ("A semester-by-semester spreadsheet for the Economics and Human Health BA.", True),
    ("A one-page roadmap of the Applied Mathematics minor.", True),
    ("Summarize this conversation into a document I can keep.", True),
    ("Turn this chat into a document I can print.", True),
    ("What does CS 253 require?", False),
    ("Is the plan to take MATH 111 good?", False),
    ("How do I make the dean's list?", False),
    ("A question about the plan.", False),
    ("My plan is to take CS 170.", False),
    ("What does the checklist say about CS 170?", False),
    ("Can you summarize what CS 170 covers?", False),
])
def test_research_is_told_when_the_student_asks_for_something_to_keep(question, asked):
    from app.generation.research_controller import _asks_for_artifact

    assert _asks_for_artifact(question) is asked


_PROGRAMS = ["NEUROSCIENCE AND BEHAVIORAL BIOLOGY (BS)", "APPLIED MATHEMATICS (MINOR)",
             "MATHEMATICS AND COMPUTER SCIENCE (BS)", "PSYCHOLOGY (BS)"]


@pytest.mark.parametrize(("question", "missing"), [
    ("Make me a plan for the Underwater Basket Weaving BS.", "Underwater Basket Weaving"),
    ("Plan my Neuroscience and Behavioral Biology BS from here.", None),
    ("A roadmap of the Applied Math minor.", None),        # a word and its prefix
    ("Plan my CS and Math BS.", None),                       # an acronym is not judged
    ("Plan my Astrology BS.", None),                         # one word is not judged
    ("Make a plan for the Mathematics and Computer Science BS.", None),
    ("What does MATH 111 require?", None),
])
def test_a_named_program_that_is_not_in_scope_is_found(question, missing):
    from app.generation.research_controller import _missing_program

    assert _missing_program(question, _PROGRAMS) == missing
    assert _missing_program(question, []) is None


@pytest.mark.parametrize(("question", "decision", "expected"), [
    # Research left out the artifact the student asked for: their words become the request.
    ("Plan my Psychology BS.", {}, ("document", "create")),
    ("A semester-by-semester spreadsheet for the Psychology BS.", {}, ("workbook", "create")),
    ("Make a web page roadmap of the Psychology BS.", {}, ("html", "create")),
    ("Summarize this chat.", {}, ("document", "summary")),
    # A summary asked for as a document is built from the saved turns.
    ("Summarize this conversation into a document I can keep.",
     {"artifact": ArtifactRequest(kind="document", title="Summary", brief="Summarize.")},
     ("document", "summary")),
    # Never for a program that does not exist, or an answer that is not a recommendation.
    ("Make me a plan for the Underwater Basket Weaving BS.", {}, None),
    ("Make me a plan for the Underwater Basket Weaving BS.",
     {"artifact": ArtifactRequest(kind="document", title="Plan", brief="Plan.")}, None),
    ("Plan my Psychology BS.", {"outcome": "insufficient_evidence"}, None),
    ("Plan my Psychology BS.", {"outcome": "clarification"}, None),
    ("What does PSYC 110 require?", {}, None),
])
def test_research_hands_the_students_request_to_the_artifact_pipeline(
        question, decision, expected):
    from app.generation.research_controller import _with_requested_artifact

    result = _with_requested_artifact(_decision(**decision), question, _PROGRAMS, referenced=False)
    assert (result.artifact and (result.artifact.kind, result.artifact.purpose))         == (expected or None)
    revised = _with_requested_artifact(_decision(**decision), question, _PROGRAMS, referenced=True)
    assert (revised.artifact is None) == (not decision.get("artifact") and expected is not None
                                          or expected is None)
