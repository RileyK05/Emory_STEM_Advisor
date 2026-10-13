"""Research data layer: pinned scope, exact scoped reads, and the durable run store.

Real Postgres, real ingest and program binding, no model.
"""

import json
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from psycopg import errors

from app.advising import research_repository, research_store
from app.advising.comparison import MENTION_OVERLAP_ONLY, compare_mentions
from app.advising.research_repository import (
    NotInScope,
    ScopeChanged,
    build_scope,
    list_programs,
    read_courses,
    read_document,
    read_program,
)
from app.advising.research_store import (
    Acquired,
    EvidenceLimit,
    InvalidResearchRecord,
    ResearchStore,
    RunClosed,
    RunNotFound,
    StateConflict,
)
from app.advising.research_types import DecisionBlock, FinalDecision, ResearchState
from app.db import queries
from app.db.connection import connect
from app.programs.importer import import_programs, program_id
from app.programs.requirements import parse_program, to_record
from tests.helpers import ingest_text
from tests.test_atlas_import import _import as import_atlas_sample
from tests.test_programs import CS_BS, _ingest_catalog_file

CS_MINOR = """Computer Science (Minor)
DEGREE AWARDED N/A
DEPARTMENT CONTACT Computer Science
Requirements
• CS 170
• One from the following: CS 370, Math 111
"""


def _corpus(db, collection, tmp_path):
    """An atlas, two bound programs, a policy page, and another workspace's upload."""
    atlas = import_atlas_sample(db, collection, tmp_path)
    records = []
    for title, text, name in (("Computer Science (BS)", CS_BS, "cs-bs.md"),
                              ("Computer Science (Minor)", CS_MINOR, "cs-minor.md")):
        identity = _ingest_catalog_file(db, collection, tmp_path, title, text, name=name)
        record = to_record(parse_program(title, text), catalog="Test Catalog",
                           source_url="https://example.test/catalog.pdf",
                           first_page=1, last_page=1)
        records.append({**record, "source": identity})
    path = tmp_path / "programs.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    assert import_programs(db, collection_id=collection, path=path)["bound"] == 2
    policy = ingest_text(db, collection, tmp_path, "grading-policy.txt",
                         "\n".join(f"Rule {n}: letter grades apply." for n in range(60)))
    private = ingest_text(db, collection, tmp_path, "other-notes.txt", "Private notes.",
                          workspace_key="other", commit=True)
    return {"atlas": atlas, "policy": policy, "private": private,
            "bs": program_id(collection, "Computer Science (BS)"),
            "minor": program_id(collection, "Computer Science (Minor)")}


def _scope(db, collection, source_ids=None, **kwargs):
    return build_scope(db, collection_id=collection, workspace_key="development",
                       source_ids=source_ids, **kwargs)


def test_scope_pins_accessible_current_sources_and_their_bound_programs(db, collection, tmp_path):
    corpus = _corpus(db, collection, tmp_path)
    everything = _scope(db, collection)
    assert corpus["private"] not in everything.sources  # another workspace's upload
    assert {corpus["atlas"], corpus["policy"]} <= set(everything.sources)
    assert set(everything.programs) == {corpus["bs"], corpus["minor"]}
    # The atlas is classified by its configured importer; the plain upload is not.
    assert everything.sources[corpus["atlas"]].source_kind == "institutional_catalog"
    assert everything.sources[corpus["policy"]].source_kind == "unclassified"
    # An explicit empty selection is empty, not "everything".
    nothing = _scope(db, collection, source_ids=[])
    assert not nothing.sources and not nothing.programs
    # Selecting only the policy page leaves no program readable.
    assert not _scope(db, collection, source_ids=[corpus["policy"]]).programs
    assert list_programs(everything, kind="minor")[0].title == "Computer Science (Minor)"
    # A frozen conversation snapshot must still be current at its revisions.
    pinned = {str(corpus["policy"]): everything.sources[corpus["policy"]].index_revision}
    assert set(_scope(db, collection, frozen_snapshot=pinned).sources) == {corpus["policy"]}
    with pytest.raises(ScopeChanged):
        _scope(db, collection, frozen_snapshot={str(corpus["policy"]): 999})
    # The digest identifies the pinned manifest exactly.
    assert _scope(db, collection).digest == everything.digest


def test_curated_career_reference_is_classified_readable_and_scoped(db, collection, tmp_path):
    from app.ingest.pipeline import ingest_file
    from app.ingest.provenance import provenance_from_manifest
    from tests.fakes import FakeEmbedder, whitespace_tokens
    from tests.helpers import MODEL, lenient_config

    corpus = _corpus(db, collection, tmp_path)
    path = tmp_path / "data-engineers.md"
    path.write_text("# Data engineers\n\nThey design and maintain data pipelines.\n",
                    encoding="utf-8")
    # The manifest entry is the verified import; the document cannot classify itself.
    entry = {"origin_key": "careers:fixture:data-engineers", "acquired_at": "2026-10-12",
             "source_url": "https://careers.example.test/data-engineers",
             "provenance": {"source_category": "career_reference", "publisher": "Fixture",
                            "geography": "United States", "published_at": "2026-01-15"}}
    career = ingest_file(db, collection_id=collection, path=path, content_type="text/markdown",
                         count_tokens=whitespace_tokens, embedder=FakeEmbedder(8),
                         embed_model=MODEL, config=lenient_config(),
                         origin_key=entry["origin_key"],
                         provenance=provenance_from_manifest(entry))
    db.commit()
    scope = _scope(db, collection)
    pin = scope.sources[career]
    assert (pin.source_kind, pin.publisher, pin.published_at) == (
        "career_reference", "Fixture", "2026-01-15")
    page = read_document(db, scope, career)
    assert "data pipelines" in page.chunks[0].text
    # Selecting only institutional files keeps the career reference out.
    institutional = _scope(db, collection, source_ids=[corpus["atlas"], corpus["policy"]])
    with pytest.raises(NotInScope):
        read_document(db, institutional, career)


def test_program_read_cites_only_lines_whose_evidence_still_matches(db, collection, tmp_path):
    corpus = _corpus(db, collection, tmp_path)
    scope = _scope(db, collection)
    bs = read_program(db, scope, corpus["bs"])
    assert all(item.line.cited for item in bs.items) and not bs.unresolved_positions
    assert {"title", "degree", "hours", "courses_required", "department"} <= set(bs.fields)
    chunk, start, end = bs.items[2].line.support[0]
    assert chunk.text[start:end].endswith("Math 111")
    # Evidence that no longer matches its chunk turns that line into a lead.
    db.execute("UPDATE program_item_evidence SET text_digest = 'stale' "
               "WHERE program_id = %(p)s AND position = 2", {"p": corpus["bs"]})
    db.commit()
    assert read_program(db, scope, corpus["bs"]).unresolved_positions == (2,)
    # Out of scope reads like absent; a re-imported projection stops the run.
    with pytest.raises(NotInScope):
        read_program(db, _scope(db, collection, source_ids=[corpus["policy"]]), corpus["bs"])
    db.execute("UPDATE programs SET parsed_content_digest = 'reimported' "
               "WHERE program_id = %(p)s", {"p": corpus["bs"]})
    db.commit()
    with pytest.raises(ScopeChanged):
        read_program(db, scope, corpus["bs"])


def test_mention_comparison_is_overlap_only_and_keeps_raw_lines(db, collection, tmp_path):
    corpus = _corpus(db, collection, tmp_path)
    scope = _scope(db, collection)
    result = compare_mentions([read_program(db, scope, corpus["bs"]),
                               read_program(db, scope, corpus["minor"])])
    assert result.kind == MENTION_OVERLAP_ONLY
    assert set(result.shared) == {"CS 170", "CS 370", "MATH 111"}
    # The minor mentions CS 370 only as one alternative; that stays visible.
    minor_refs = [ref for ref in result.shared["CS 370"] if ref.program_id == corpus["minor"]]
    assert minor_refs[0].text == "One from the following: CS 370, Math 111"
    assert "CS 371W" in result.unique[corpus["bs"]]
    with pytest.raises(ValueError):
        compare_mentions([read_program(db, scope, corpus["bs"])])


def test_course_and_document_reads_stay_in_scope_and_page_whole_chunks(db, collection, tmp_path):
    corpus = _corpus(db, collection, tmp_path)
    scope = _scope(db, collection)
    (econ,) = read_courses(db, scope, ["ECON 201"])
    assert econ.title == "Intermediate Macroeconomics" and econ.requisites_raw == "ECON 101"
    assert econ.chunks and "ECON 201" in econ.chunks[0].text
    assert read_courses(db, _scope(db, collection, source_ids=[corpus["policy"]]),
                        ["ECON 201"]) == []
    with pytest.raises(ValueError):
        read_courses(db, scope, ["ECON 101", "ECON 201", "MATH 111", "MATH 112", "ECON 301"])
    whole = read_document(db, scope, corpus["policy"], page_bytes=10**6)
    assert len(whole.chunks) > 1 and whole.next_chunk is None and whole.sections
    # A page holds whole chunks only; the next page starts at the next chunk.
    page = len(whole.chunks[0].text.encode())
    first = read_document(db, scope, corpus["policy"], page_bytes=page)
    assert [c.chunk_id for c in first.chunks] == [whole.chunks[0].chunk_id]
    second = read_document(db, scope, corpus["policy"], from_chunk=first.next_chunk,
                           page_bytes=10**6)
    assert [c.chunk_id for c in second.chunks] == [c.chunk_id for c in whole.chunks[1:]]
    tiny = read_document(db, scope, corpus["policy"], page_bytes=10)
    assert tiny.incomplete and not tiny.chunks  # never cut a chunk mid-text
    with pytest.raises(NotInScope):
        read_document(db, scope, corpus["private"])


@pytest.mark.parametrize("read", ["program", "courses", "document"])
def test_a_reindex_cannot_land_between_a_reads_check_and_its_content(
        db, collection, tmp_path, migrated_dsn, monkeypatch, read):
    corpus = _corpus(db, collection, tmp_path)
    db.commit()
    scope = _scope(db, collection)
    checked = research_repository.verify_sources
    attempted, blocked = [], []

    def check_then_reindex(conn, scope, source_ids):
        checked(conn, scope, source_ids)
        # Another process claims the next revision right after the check passes.
        with connect(migrated_dsn) as other:
            other.execute("SET lock_timeout = '200ms'")
            for source_id in source_ids:
                attempted.append(source_id)
                try:
                    other.execute(queries.get("claim_source_index_attempt"), {
                        "source_id": source_id, "expected_index_revision": None})
                    other.commit()
                except errors.LockNotAvailable:
                    other.rollback()
                    blocked.append(source_id)

    monkeypatch.setattr(research_repository, "verify_sources", check_then_reindex)
    {"program": lambda: read_program(db, scope, corpus["bs"]),
     "courses": lambda: read_courses(db, scope, ["ECON 201"]),
     "document": lambda: read_document(db, scope, corpus["policy"])}[read]()
    # The reindex waits for the read, so content is never stamped with a stale revision.
    assert attempted and blocked == attempted


def _run(db, collection, tmp_path, **kwargs):
    corpus = _corpus(db, collection, tmp_path)
    scope = _scope(db, collection)
    store = ResearchStore(db, "development")
    run, created = store.start(scope, run_id=uuid4(), prompt_version="t1",
                               config_version="6", **kwargs)
    assert created
    return corpus, scope, store, run


def test_evidence_ids_are_stable_deduplicated_and_replayed(db, collection, tmp_path, monkeypatch):
    corpus, scope, store, run = _run(db, collection, tmp_path)
    chunks = [c for course in read_courses(db, scope, ["ECON 101", "ECON 201", "MATH 111"])
              for c in course.chunks]
    atlas = scope.sources[corpus["atlas"]]
    tool = uuid4()
    first = store.acquire(run.run_id, [Acquired(chunks[0], atlas)], action_kind="read_courses",
                          summary="Read ECON 101", tool_execution_id=tool)
    assert first.evidence_ids == ("e1",) and not first.replayed
    # The same chunk again keeps its ID; a new chunk gets the next one.
    second = store.acquire(run.run_id, [Acquired(chunks[0], atlas), Acquired(chunks[1], atlas)],
                           action_kind="read_courses", summary="Read two")
    assert second.evidence_ids == ("e1", "e2")
    # Replaying the first tool execution restores its step and IDs exactly.
    replay = store.acquire(run.run_id, [Acquired(chunks[2], atlas)], action_kind="read_courses",
                           summary="Replay", tool_execution_id=tool)
    assert replay.replayed and replay.evidence_ids == ("e1",) and replay.step_ordinal == 0
    records = store.evidence(run.run_id)
    assert [r.evidence_id for r in records] == ["e1", "e2"]
    assert records[0].exact_text == chunks[0].text
    assert records[0].source_kind == "institutional_catalog"
    # A full register keeps nothing from the step that overflows it.
    monkeypatch.setattr(research_store, "MAX_EVIDENCE_ITEMS", 2)
    with pytest.raises(EvidenceLimit):
        store.acquire(run.run_id, [Acquired(chunks[2], atlas)], action_kind="read_courses",
                      summary="Too many")
    assert len(store.evidence(run.run_id)) == 2 and len(store.steps(run.run_id)) == 2


def test_evidence_comes_only_from_the_runs_pinned_sources(db, collection, tmp_path):
    corpus = _corpus(db, collection, tmp_path)
    everything = _scope(db, collection)
    (course,) = read_courses(db, everything, ["ECON 101"])
    atlas_chunk, atlas = course.chunks[0], everything.sources[corpus["atlas"]]
    policy_chunk = read_document(db, everything, corpus["policy"]).chunks[0]
    policy = everything.sources[corpus["policy"]]
    store = ResearchStore(db, "development")
    run, _ = store.start(_scope(db, collection, source_ids=[corpus["policy"]]), run_id=uuid4(),
                         prompt_version="t1", config_version="6")
    for bad in (
        Acquired(atlas_chunk, atlas),  # a source the run never selected
        Acquired(policy_chunk, atlas),  # chunk and source disagree
        Acquired(replace(policy_chunk, text="Rule 1: pass/fail applies."), policy),  # forged
        Acquired(policy_chunk, replace(policy, source_kind="institutional_policy")),  # relabeled
    ):
        with pytest.raises(InvalidResearchRecord):
            store.acquire(run.run_id, [bad], action_kind="read_document", summary="Bad read")
    assert store.evidence(run.run_id) == [] and store.steps(run.run_id) == []
    good = store.acquire(run.run_id, [Acquired(policy_chunk, policy)],
                         action_kind="read_document", summary="Read the policy")
    assert good.evidence_ids == ("e1",)
    # A pinned source reindexed since the run started stops it instead.
    db.execute(queries.get("claim_source_index_attempt"), {
        "source_id": corpus["policy"], "expected_index_revision": None})
    db.commit()
    with pytest.raises(ScopeChanged):
        store.acquire(run.run_id, [Acquired(policy_chunk, policy)],
                      action_kind="read_document", summary="Read again")


def test_state_is_optimistic_and_cites_only_issued_evidence(db, collection, tmp_path):
    corpus, scope, store, run = _run(db, collection, tmp_path)
    (course,) = read_courses(db, scope, ["ECON 101"])
    store.acquire(run.run_id, [Acquired(course.chunks[0], scope.sources[corpus["atlas"]])],
                  action_kind="read_courses", summary="Read ECON 101")
    state = ResearchState(revision=0, decision_question="Which economics path?",
                          active_evidence_ids=("e1",))
    saved = store.update_state(run.run_id, state, expected_revision=0)
    assert saved.revision == 1 and store.get(run.run_id).state.active_evidence_ids == ("e1",)
    with pytest.raises(StateConflict):
        store.update_state(run.run_id, state, expected_revision=0)
    with pytest.raises(InvalidResearchRecord, match="never issued"):
        store.update_state(run.run_id, state.model_copy(update={
            "active_evidence_ids": ("e9",)}), expected_revision=1)


def test_decision_facts_need_issued_evidence_and_runs_are_workspace_private(
        db, collection, tmp_path):
    corpus, scope, store, run = _run(db, collection, tmp_path)
    (course,) = read_courses(db, scope, ["ECON 101"])
    store.acquire(run.run_id, [Acquired(course.chunks[0], scope.sources[corpus["atlas"]])],
                  action_kind="read_courses", summary="Read ECON 101")
    fact = DecisionBlock(kind="fact", text="ECON 101 is macroeconomics.", evidence_ids=("e1",))
    decision = FinalDecision(outcome="information", completeness="complete", blocks=(fact,))
    assert store.record_decision(run.run_id, decision, render_version="r1")
    for bad in (DecisionBlock(kind="fact", text="Uncited fact."),
                DecisionBlock(kind="fact", text="Invented.", evidence_ids=("e7",))):
        with pytest.raises(InvalidResearchRecord):
            store.record_decision(run.run_id, FinalDecision(
                outcome="information", completeness="complete", blocks=(bad,)),
                render_version="r2")
    store.finish(run.run_id, "completed", "finished")
    assert store.get(run.run_id).status == "completed"
    with pytest.raises(RunNotFound):
        ResearchStore(db, "other").get(run.run_id)


def test_an_ended_run_accepts_no_further_writes(db, collection, tmp_path):
    corpus, scope, store, run = _run(db, collection, tmp_path)
    (course,) = read_courses(db, scope, ["ECON 101"])
    read = Acquired(course.chunks[0], scope.sources[corpus["atlas"]])
    store.acquire(run.run_id, [read], action_kind="read_courses", summary="Read ECON 101")
    store.finish(run.run_id, "cancelled", "user cancelled")
    store.finish(run.run_id, "cancelled", "retried")  # repeating the same ending is a no-op
    state = ResearchState(revision=0, decision_question="Which path?", active_evidence_ids=("e1",))
    fact = DecisionBlock(kind="fact", text="ECON 101 is macroeconomics.", evidence_ids=("e1",))
    decision = FinalDecision(outcome="information", completeness="complete", blocks=(fact,))
    for late_write in (
        lambda: store.finish(run.run_id, "completed", "late worker"),
        lambda: store.update_state(run.run_id, state, expected_revision=0),
        lambda: store.acquire(run.run_id, [read], action_kind="read_courses", summary="Late"),
        lambda: store.record_decision(run.run_id, decision, render_version="r1"),
    ):
        with pytest.raises(RunClosed):
            late_write()
    ended = store.get(run.run_id)
    assert (ended.status, ended.stop_reason, ended.state_revision) == (
        "cancelled", "user cancelled", 0)
    assert len(store.steps(run.run_id)) == 1


def test_a_conversation_request_runs_at_most_one_investigation(db, collection, tmp_path):
    from app.conversations.store import ConversationStore

    conversations = ConversationStore(db, "development")
    conversation = conversations.create("Research")["conversationId"]
    request = uuid4()
    conversations.begin(conversation, request, 0, {"query": "Which major?"})
    _corpus_ids, scope, store, run = _run(db, collection, tmp_path, conversation_id=conversation,
                                          request_id=request)
    again, created = store.start(scope, run_id=uuid4(), prompt_version="t1", config_version="6",
                                 conversation_id=conversation, request_id=request)
    assert not created and again.run_id == run.run_id


def _research_provider(monkeypatch, script):
    """Drive the shared typed fake from successive server-owned request contexts."""
    from app.llm.base import GenerationResult
    from tests.fakes import ScriptedProvider

    provider = ScriptedProvider()
    generate = provider.generate
    def scripted(request):
        context = json.loads(request.messages[1].content)
        provider.result = GenerationResult(tool_calls=tuple(script(context, len(provider.requests))),
                                           finish_reason="tool_calls", usage=provider.result.usage)
        return generate(request)
    monkeypatch.setattr(provider, "generate", scripted)
    return provider


@pytest.mark.parametrize("scenario", ["clarification", "course", "program_comparison", "preparation", "search_document", "saved_clarification", "schema_repair"])
def test_research_answers_progressively_and_persists_its_decision(
        db, collection, tmp_path, monkeypatch, scenario):
    from app.api.query_service import query_response
    from app.api.schemas import QueryRequest
    from app.config import load_settings
    from app.llm.base import ToolCall
    from app.operations.budget import OperationBudgetController
    from tests.fakes import FakeEmbedder
    from tests.helpers import MODEL, collection_name, lenient_config

    _corpus(db, collection, tmp_path)
    settings = replace(load_settings(), llm_provider="openai", llm_model_id="fake",
        tools_enabled=True, advising_research_enabled=True, routing_enabled=False,
        embedding_model_id=MODEL, db_pool_max_size=0)
    def script(context, index):
        def call(name, args):
            return [ToolCall(f"call-{index}", "advisor_" + name, args)]
        if scenario in {"clarification", "saved_clarification"}:
            return call("finish", {"decision": {"outcome": "clarification", "completeness": "complete",
                "blocks": [{"kind": "question", "text": "Which career are you considering?"}]}})
        if scenario == "search_document":
            if index == 0:
                return call("search", {"query": "letter grades", "kind": "any"})
            if index == 1:
                handle = next(h["handle"] for h in context["issued_handles"] if h["kind"] == "document")
                return call("read_document", {"handle": handle})
            return call("finish", {"decision": {"outcome": "information", "completeness": "complete",
                "blocks": [{"kind": "fact", "text": "The policy describes letter grades.",
                    "evidence_ids": [e["evidence_id"] for e in context["exact_evidence_untrusted"]]}]}})
        if scenario == "schema_repair":
            if index == 0:
                return call("read_courses", {"course_codes": ["ECON 201"]})
            ids = [e["evidence_id"] for e in context["exact_evidence_untrusted"]]
            if index in {1, 2}:
                if index == 2:
                    assert "32 characters" in context["instruction"]
                return call("update_research", {"expected_revision": 0, "state": {
                    "revision": 0, "decision_question": "Which economics path?", "active_evidence_ids": ids,
                    "candidates": [{"id": "h_" + "a" * 32 if index == 1 else "econ_path",
                                    "course_codes": ["ECON 201"], "description": "An economics path",
                                    "status": "conditional"}]}})
            return call("finish", {"decision": {"outcome": "recommendation", "completeness": "complete",
                "candidate_ids": ["econ_path"], "blocks": [{"kind": "judgment",
                    "text": "This economics course looks worth exploring.",
                    "evidence_ids": ids, "candidate_ids": ["econ_path"]}]}})
        if scenario == "program_comparison":
            handles = [h["handle"] for h in context["issued_handles"] if h["kind"] == "program"]
            if index == 0:
                return call("list_programs", {})
            if index in {1, 3}:
                return call("read_program", {"handle": handles[0 if index == 1 else 1]})
            if index == 4:
                return call("compare_mentions", {"handles": handles})
            if index == 2:
                ids = [e["evidence_id"] for e in context["exact_evidence_untrusted"]]
                return call("update_research", {"expected_revision": 0, "state": {
                    "revision": 0, "decision_question": "Which CS path?", "active_evidence_ids": ids,
                    "candidates": [{"id": "cs", "program_handles": handles[:1],
                                    "course_codes": ["CS 170"],
                                    "description": "A CS path", "status": "conditional"}]}})
            return call("finish", {"decision": {"outcome": "recommendation", "completeness": "complete",
                "candidate_ids": ["cs"], "blocks": [{"kind": "judgment", "text": "I would lean toward this CS path.",
                    "candidate_ids": ["cs"], "evidence_ids": context["state_model_assessments"]["active_evidence_ids"]}]}})
        if index == 0:
            return call("check_preparation", {"selected_codes": ["ECON 201"], "completed_codes": ["ECON 101"]}) if (
                scenario == "preparation") else call("read_courses", {"course_codes": ["ECON 201"]})
        ids = [e["evidence_id"] for e in context["exact_evidence_untrusted"]]
        decision = {"outcome": "information", "completeness": "complete",
                    "blocks": [{"kind": "fact", "text": "ECON 201 lists ECON 101 as preparation.", "evidence_ids": ids}]}
        if scenario == "preparation":
            check = context["executed_preparation_checks"][0]
            decision.update(preparation_check_ids=[check["check_id"]], preparation_claims=[{
                "check_id": check["check_id"], "selected_codes": check["selected_codes"],
                "completed_codes": check["completed_codes"], "conclusion": check["conclusion"]}])
        return call("finish", {"decision": decision})
    provider = _research_provider(monkeypatch, script)
    sources = [] if scenario in {"clarification", "saved_clarification"} else None
    conversation_args = {}
    if scenario == "saved_clarification":
        from app.conversations.store import ConversationStore

        conversations = ConversationStore(db, "development")
        conversation_id = UUID(conversations.create("Research question")["conversationId"])
        request_id = uuid4()
        conversations.begin(conversation_id, request_id, 0, {"query": "Help choose a path"})
        conversation_args = {"conversation_id": conversation_id, "conversation_request_id": request_id}
    response = query_response(QueryRequest(query="Help choose a path", collectionId=collection_name(db, collection),
        sourceIds=sources), db, settings, lenient_config(), FakeEmbedder(8), provider, **conversation_args)
    assert response.type == "answer"
    assert response.research.completeness == "complete"
    run = ResearchStore(db, "development").get(UUID(response.research.runId))
    if scenario == "saved_clarification":
        assert run.status == "running"
        conversations.finish(conversation_id, request_id, response.model_dump(mode="json"))
        run = ResearchStore(db, "development").get(run.run_id)
        evidence = conversations.turns(conversation_id)["turns"][-1]["evidence"]
        assert evidence["research"]["runId"] == response.research.runId
        assert evidence["research"]["outcome"] == "clarification"
    assert run.status == ("clarification" if scenario in {"clarification", "saved_clarification"} else "completed")
    if scenario in {"clarification", "saved_clarification"}:
        assert response.citations == [] and response.trace.contextBlock == []
    else:
        assert response.citations and response.trace.contextBlock
        assert "[1]" in response.answer
        if scenario == "program_comparison":
            assert response.claims == []  # Subjective suitability is not an entailment claim.
            from app.generation.claim_review import ClaimReviewService

            saved = db.execute(queries.get("get_response_for_trace"), {
                "trace_id": UUID(response.trace.traceId)}).fetchone()
            review_input = db.execute(queries.get("load_claim_review_input"), {
                "response_id": saved["response_id"], "operation_id": run.operation_id,
                "workspace_key": "development"}).fetchone()
            _answer, _context, snapshot = ClaimReviewService._review_input(review_input)
            assert snapshot["research_blocks"][0]["kind"] == "judgment"
    status = OperationBudgetController().get(db, run.operation_id)
    assert status.call_count == len(provider.requests)
    assert all(sum(len(e["exact_text"]) for e in json.loads(r.messages[1].content)[
        "exact_evidence_untrusted"]) <= settings.research_max_prompt_bytes for r in provider.requests)


def test_research_rejects_inactive_evidence_and_allows_one_correction(
        db, collection, tmp_path, monkeypatch):
    from app.api.query_service import query_response
    from app.api.schemas import QueryRequest
    from app.config import load_settings
    from app.llm.base import ToolCall
    from tests.fakes import FakeEmbedder
    from tests.helpers import MODEL, collection_name, lenient_config

    _corpus(db, collection, tmp_path)
    settings = replace(load_settings(), llm_provider="openai", llm_model_id="fake",
        tools_enabled=True, advising_research_enabled=True, routing_enabled=False,
        embedding_model_id=MODEL, db_pool_max_size=0)
    def script(context, index):
        if index == 0:
            return [ToolCall("read", "advisor_read_courses", {"course_codes": ["ECON 201"]})]
        if index == 1:
            return [ToolCall("state", "advisor_update_research", {"expected_revision": 0,
                "state": {"revision": 0, "decision_question": "Which path?", "active_evidence_ids": []}})]
        if index == 2:
            return [ToolCall("bad-finish", "advisor_finish", {"decision": {
                "outcome": "information", "completeness": "complete", "blocks": [{
                    "kind": "fact", "text": "Old inactive fact", "evidence_ids": ["e1"]}]}})]
        return [ToolCall("correction", "advisor_finish", {"decision": {
            "outcome": "clarification", "completeness": "complete", "blocks": [{
                "kind": "question", "text": "What is your goal?"}]}})]
    provider = _research_provider(monkeypatch, script)
    response = query_response(QueryRequest(query="Help me", collectionId=collection_name(db, collection)),
        db, settings, lenient_config(), FakeEmbedder(8), provider)
    assert response.citations == [] and "Old inactive fact" not in response.answer
    steps = ResearchStore(db, "development").steps(UUID(response.research.runId))
    assert any(s["action_kind"] == "rejected_finish" and s["details"]["proposal"] for s in steps)


def test_program_discovery_prefers_title_matches_to_incidental_requirement_mentions(
        db, collection, tmp_path):
    from app.config import load_settings
    from app.generation.research_tools import ResearchSession, SearchInput
    from tests.fakes import FakeEmbedder
    from tests.helpers import MODEL, lenient_config

    _corpus(db, collection, tmp_path)
    title = "Anthropology (BA)"
    text = title + "\nDEGREE AWARDED BA\nRequirements\n• A major may take a computer science elective: CS 170\n"
    identity = _ingest_catalog_file(db, collection, tmp_path, title, text, name="anthropology.md")
    path = tmp_path / "programs.jsonl"
    record = to_record(parse_program(title, text), catalog="Test Catalog",
                       source_url="https://example.test/catalog.pdf", first_page=1, last_page=1)
    path.write_text(path.read_text(encoding="utf-8") + json.dumps({**record, "source": identity}) + "\n",
                    encoding="utf-8")
    import_programs(db, collection_id=collection, path=path)
    scope, store = _scope(db, collection), ResearchStore(db, "development")
    run, _ = store.start(scope, run_id=uuid4(), prompt_version="t1", config_version="6")
    session = ResearchSession(db, scope, store, run,
        settings=replace(load_settings(), embedding_model_id=MODEL),
        config=lenient_config(), embedder=FakeEmbedder(8))
    result = session.search(SearchInput(query="computer science major", kind="program"), None)
    assert result.data["leads"][0]["title"] == "Computer Science (BS)"
    assert all(lead["kind"] == "program" for lead in result.data["leads"])
    assert session.discovery_count == len(result.data["leads"])


@pytest.mark.parametrize("scenario", ["overflow", "changed_call_id", "stale_source", "cancelled"])
def test_research_reserves_a_finish_and_withholds_stale_or_cancelled_results(
        db, collection, tmp_path, monkeypatch, scenario):
    from fastapi import HTTPException

    from app.api.query_service import query_response
    from app.api.schemas import QueryRequest
    from app.config import load_settings
    from app.generation.controller import GenerationCancelled
    from app.llm.base import ToolCall
    from tests.fakes import FakeEmbedder
    from tests.helpers import MODEL, collection_name, lenient_config

    corpus = _corpus(db, collection, tmp_path)
    settings = replace(load_settings(), llm_provider="openai", llm_model_id="fake",
        tools_enabled=True, advising_research_enabled=True, routing_enabled=False,
        embedding_model_id=MODEL, db_pool_max_size=0, research_max_tool_calls=3)
    cancelled = False
    def script(context, index):
        nonlocal cancelled
        if index == 0:
            codes = ["ECON 101", "ECON 201", "MATH 111", "MATH 112"] if scenario == "overflow" else ["ECON 101"]
            return [ToolCall("read-" + str(i), "advisor_read_courses", {"course_codes": [code]})
                    for i, code in enumerate(codes)]
        if scenario == "stale_source":
            db.execute(queries.get("claim_source_index_attempt"), {
                "source_id": corpus["atlas"], "expected_index_revision": None})
            db.commit()
        cancelled = scenario == "cancelled"
        return [ToolCall("read-0" if scenario == "changed_call_id" else "finish", "advisor_finish", {
            "decision": {"outcome": "information", "completeness": "complete", "blocks": [{
                "kind": "fact", "text": "ECON 101 is in the catalog.",
                "evidence_ids": [e["evidence_id"] for e in context["exact_evidence_untrusted"]]}]}})]
    provider = _research_provider(monkeypatch, script)
    def guard():
        if cancelled:
            raise GenerationCancelled("test cancellation")
    def answer():
        return query_response(QueryRequest(query="What is ECON 101?", collectionId=collection_name(db, collection)),
            db, settings, lenient_config(), FakeEmbedder(8), provider, check_cancelled=guard)
    if scenario == "overflow":
        response = answer()
        assert response.research.completeness == "partial"
        assert len(provider.requests) == 2
        steps = ResearchStore(db, "development").steps(UUID(response.research.runId))
        assert sum(s["action_kind"] == "read_courses" for s in steps) == 1
    elif scenario == "cancelled":
        with pytest.raises(GenerationCancelled):
            answer()
    else:
        with pytest.raises(HTTPException) as caught:
            answer()
        assert caught.value.status_code == (409 if scenario == "stale_source" else 422)
    if scenario != "overflow":
        run = db.execute(queries.get("diagnostic_recent_research_runs"), {
            "workspace_key": "development"}).fetchone()
        assert run["status"] == ("cancelled" if scenario == "cancelled" else "failed")
