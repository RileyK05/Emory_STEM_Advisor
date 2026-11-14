"""Research data layer: pinned scope, exact scoped reads, and the durable run store.

Real Postgres, real ingest and program binding, no model.
"""

import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID, uuid4, uuid5

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
from tests.helpers import ingest_text, send_turn
from tests.test_atlas_import import _import as import_atlas_sample
from tests.test_programs import CS_BS, _ingest_catalog_file

CS_MINOR = """Computer Science (Minor)
DEGREE AWARDED N/A
DEPARTMENT CONTACT Computer Science
Requirements
• CS 170
• One from the following: CS 370, Math 111
• Courses for the minor must be taken for a letter grade.
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


@pytest.mark.parametrize("admits", [True, False])
@pytest.mark.parametrize(("grounding", "kind", "mode"), [
    ("corpus", "document", "grounded"),
    ("authoring", "html", "authoring"),
    # A document always stays grounded, even if the request says authoring.
    ("authoring", "document", "grounded"),
])
def test_published_turn_with_artifact_request_queues_one_run(
        db, migrated_dsn, collection, admits, grounding, kind, mode):
    from app.config import load_settings
    from app.conversations.store import ConversationStore
    from app.conversations.worker import ConversationTurnExecutor, ConversationTurnResult
    from app.db.collections import resolve_collection
    from app.jobs.store import JobStore

    store = ConversationStore(db, "development")
    conversation_id = UUID(store.create("Artifact turn")["conversationId"])
    request_id = uuid4()
    snapshot = {"request": {"query": "Make a plan"}, "query": "Make a plan",
                "effective": {}, "context": {}}
    accepted = store.begin_stream(conversation_id, request_id, 0, snapshot)
    jobs = JobStore(lambda: connect(migrated_dsn))
    job = jobs.get(accepted["job_id"])
    run, _ = ResearchStore(db, "development").start(
        _scope(db, collection), run_id=uuid4(), prompt_version="test", config_version="6",
        conversation_id=conversation_id, request_id=request_id)
    artifact = {
        "kind": kind, "title": "Four-semester plan",
        "brief": "Make a plan for the student's next four semesters.", "purpose": "create",
        "grounding": grounding,
    }
    research = {"runId": str(run.run_id), "outcome": "information", "completeness": "complete",
                "blocks": [], "artifact": artifact, "unresolvedQuestions": []}
    result = ConversationTurnResult(
        response={"type": "answer", "answer": "A plan is being made [1].",
                  "citations": [], "trace": {"traceId": str(uuid4()), "contextBlock": []},
                  "research": research},
        source_snapshot={}, collection_id=collection, operation_id=None,
        profile_selections=(),
        # Without a job store the run cannot be admitted; the answer still publishes.
        job_store=jobs if admits else None, settings=load_settings(),
        collection_name=f"c-{collection}",
        request_text="Make a plan for the student's next four semesters.",
    )

    published = ConversationTurnExecutor.publish_success(db, job, result)
    assert published["type"] == "answer"
    turn = store.turns(conversation_id)["turns"][-1]
    runs = db.execute(
        "SELECT payload FROM jobs WHERE kind = 'artifact.run' ORDER BY created_at"
    ).fetchall()
    if not admits:
        assert "artifactRun" not in turn
        assert turn["artifactNotice"] == "The answer is saved, but the artifact could not be started."
        assert runs == []
        return
    queued = turn["artifactRun"]
    assert (queued["kind"], queued["title"], queued["purpose"]) == (
        kind, "Four-semester plan", "create")
    assert queued["runId"]
    assert len(runs) == 1
    payload = runs[0]["payload"]
    assert payload["kind"] == kind
    assert payload["inputs"]["mode"] == mode
    assert payload["inputs"]["requestText"] == "Make a plan for the student's next four semesters."
    assert payload["inputs"]["researchRunId"] == str(run.run_id)
    assert "Published answer:" in payload["inputs"]["prompt"]
    # The run must find the collection the turn answered from.
    resolved, _ = resolve_collection(db, load_settings(), payload["inputs"].get("collectionId"))
    assert resolved == collection


@pytest.mark.parametrize("referenced,accepted", [
    (lambda artifact, revision: set(), False),
    (lambda artifact, revision: {(artifact, str(uuid4()))}, False),
    (lambda artifact, revision: {(artifact, revision)}, True),
])
def test_artifact_revision_must_name_the_exact_referenced_revision(referenced, accepted):
    from app.advising.research_types import ArtifactRequest, ArtifactRevisionReference
    from app.generation.research_final import DecisionRejected, validate_final

    artifact_id, revision_id = uuid4(), uuid4()
    decision = FinalDecision(
        outcome="information", completeness="complete",
        blocks=(DecisionBlock(kind="limitation", text="Revision requested."),),
        artifact=ArtifactRequest(
            kind="document", title="Plan", brief="Revise the plan.", purpose="revise",
            revises=ArtifactRevisionReference(artifact_id=artifact_id, revision_id=revision_id),
        ),
    )
    refs = referenced(str(artifact_id), str(revision_id))
    if accepted:
        validate_final(decision, None, [], set(), {}, artifact_references=refs)
    else:
        with pytest.raises(DecisionRejected, match="not referenced"):
            validate_final(decision, None, [], set(), {}, artifact_references=refs)


def test_scope_pins_accessible_current_sources_and_their_bound_programs(db, collection, tmp_path):
    corpus = _corpus(db, collection, tmp_path)
    everything = _scope(db, collection)
    assert corpus["private"] not in everything.sources  # another workspace's upload
    assert {corpus["atlas"], corpus["policy"]} <= set(everything.sources)
    assert set(everything.programs) == {corpus["bs"], corpus["minor"]}
    # The atlas is classified by its configured importer; the plain upload is not.
    assert everything.sources[corpus["atlas"]].source_kind == "institutional_catalog"
    assert everything.sources[corpus["policy"]].source_kind == "unclassified"
    assert everything.sources[corpus["policy"]].applicability_status == "unknown"
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


@pytest.mark.parametrize("category,status,large_metadata,metadata_only", [
    ("career_reference", "historical", False, False),
    ("institutional_publication", "unknown", False, False),
    ("institutional_publication", "unknown", True, False),
    ("institutional_publication", "unknown", False, True),
    ("institutional_catalog", "unknown", False, False)])
def test_curated_reference_is_classified_readable_and_scoped(
        db, collection, tmp_path, category, status, large_metadata, metadata_only):
    from app.advising.research_types import EvidenceRecord
    from app.config import load_settings
    from app.generation.research_tools import ReadInput, ResearchSession, SearchInput
    from app.ingest.pipeline import ingest_file
    from app.ingest.provenance import provenance_from_manifest
    from tests.fakes import FakeEmbedder, whitespace_tokens
    from tests.helpers import MODEL, lenient_config

    corpus = _corpus(db, collection, tmp_path)
    path = tmp_path / "data-engineers.md"
    prefix = ("# Library administration\n\n" + "Room booking is unrelated to this question.\n" * 400
              + "\n") if category == "institutional_catalog" else ""
    path.write_text(prefix + "# Data engineers\n\nThey design and maintain data pipelines.\n",
                    encoding="utf-8")
    # The manifest entry is the verified import; the document cannot classify itself.
    entry = {"origin_key": "careers:fixture:data-engineers", "acquired_at": "2026-10-12",
             "source_url": "https://careers.example.test/data-engineers",
             "provenance": {"source_category": category, "publisher": "Fixture",
                            "geography": "United States", "published_at": "2026-01-15",
                            "title": "Data engineering preparation", "institution": "Fixture University",
                            "issuing_unit": "Advising office", "edition": "2026",
                            "document_roles": ["requirements"] if category == "institutional_catalog"
                            else ["career_preparation"],
                            "applicability": {"school": ["College"], "audience": ["undergraduate"]},
                            "applicability_status": status}}
    if large_metadata:
        # Accepted provenance near the 8 KiB ceiling still fits a discovery page.
        entry["provenance"]["applicability"].update({
            f"additional_scope_{dimension}": [f"{label:02d}" + "x" * 198 for label in range(9)]
            for dimension in range(4)})
    career = ingest_file(db, collection_id=collection, path=path, content_type="text/markdown",
                         count_tokens=whitespace_tokens, embedder=FakeEmbedder(8),
                         embed_model=MODEL, config=lenient_config(),
                         origin_key=entry["origin_key"],
                         provenance=provenance_from_manifest(entry))
    db.commit()
    scope = _scope(db, collection)
    pin = scope.sources[career]
    assert (pin.source_kind, pin.publisher, pin.published_at) == (
        category, "Fixture", "2026-01-15")
    assert pin.document_roles == tuple(entry["provenance"]["document_roles"])
    with pytest.raises(TypeError):
        pin.applicability["school"] = ("Other school",)
    page = read_document(db, scope, career)
    if category != "institutional_catalog":
        assert "data pipelines" in page.chunks[0].text
    store = ResearchStore(db, "development")
    run, _ = store.start(scope, run_id=uuid4(), prompt_version="t1", config_version="6")
    config = lenient_config()
    if metadata_only:
        config = replace(config, seams=replace(config.seams,
            keyword=replace(config.seams.keyword, enabled=False),
            embed=replace(config.seams.embed, enabled=False),
            prereq=replace(config.seams.prereq, enabled=False)))
    session = ResearchSession(db, scope, store, run,
        settings=replace(load_settings(), embedding_model_id=MODEL),
        config=config, embedder=FakeEmbedder(8))
    # Historical/unknown applicability is surfaced, never guessed or filtered away.
    leads = session.search(SearchInput(
        query="Fixture University Advising office" if metadata_only else "data pipelines",
        kind="policy" if category == "institutional_catalog" else
        "document" if metadata_only else "any"), None).data["leads"]
    if metadata_only:
        assert leads[0]["filename"] == path.name
        assert all(lead["kind"] == "document" and lead["snippet"] == "" for lead in leads)
    lead = next(lead for lead in leads if lead["kind"] == "document"
                and lead["filename"] == path.name)
    document = session.document(ReadInput(handle=lead["handle"]), None).data
    if category == "institutional_catalog":
        assert any("data pipelines" in e.exact_text for e in store.evidence(run.run_id))
        assert document["earlier_chunks_unread_by_this_read"]
        fallback = session.search(SearchInput(query="Data engineering preparation requirements",
                                              kind="program"), None).data["requirement_documents"]
        captured = next(item for item in fallback if item["filename"] == path.name)
        assert session.document(ReadInput(handle=captured["handle"]), None).data["evidence_ids"]
    for result in (lead, document):
        assert result["title"] == "Data engineering preparation"
        assert result["institution"] == "Fixture University"
        assert result["issuing_unit"] == "Advising office"
        assert result["document_roles"] == entry["provenance"]["document_roles"]
        assert result["applicability_status"] == status
    assert document["applicability"] == entry["provenance"]["applicability"]
    if large_metadata:
        assert lead["metadata_truncated"] and "applicability" in lead["metadata_omitted_fields"]
        assert len(json.dumps(lead, ensure_ascii=False).encode("utf-8")) < 2048
        assert lead["applicability_status"] == "unknown"
        assert lead["applicability"]["school"] == ["College"]
    else:
        assert lead["applicability"] == document["applicability"]
        assert not lead["metadata_truncated"] and lead["metadata_omitted_fields"] == []
    evidence = store.evidence(run.run_id)[0]
    assert evidence.title == pin.title and evidence.applicability_status == status
    assert evidence.applicability["school"] == ("College",)
    with pytest.raises(TypeError):
        evidence.applicability["school"] = ("Other school",)
    # Evidence saved before these fields existed retains honest unknown defaults.
    legacy = evidence.model_dump(mode="json")
    for key in ("title", "institution", "issuing_unit", "document_roles", "applicability",
                "applicability_status", "metadata_revision"):
        legacy.pop(key)
    old = EvidenceRecord.model_validate(legacy, strict=False)
    assert old.applicability_status == "unknown" and old.title is None
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


def test_program_read_marks_unverified_fields_and_rejects_foreign_cursors(db, collection, tmp_path):
    from app.config import load_settings
    from app.generation.research_tools import ReadInput, ResearchSession
    from tests.fakes import FakeEmbedder
    from tests.helpers import MODEL, lenient_config

    corpus = _corpus(db, collection, tmp_path)
    db.execute("UPDATE program_field_evidence SET text_digest = 'stale' "
               "WHERE program_id = %(p)s AND field = 'degree'", {"p": corpus["bs"]})
    db.commit()
    scope, store = _scope(db, collection), ResearchStore(db, "development")
    run, _ = store.start(scope, run_id=uuid4(), prompt_version="test", config_version="6")
    session = ResearchSession(db, scope, store, run,
        settings=replace(load_settings(), embedding_model_id=MODEL),
        config=lenient_config(), embedder=FakeEmbedder(8))
    session.handles["h_bs"] = ("program", corpus["bs"])
    session.handles["h_minor"] = ("program", corpus["minor"])
    read = session.program(ReadInput(handle="h_bs"), None)
    assert read.data["fields"]["degree"]["discovery_only"] is True
    assert read.data["fields"]["degree"]["evidence_ids"] == []
    assert read.data["fields"]["title"]["discovery_only"] is False
    assert any("degree" in limitation for limitation in read.limitations)
    # Seed a cursor for the minor handle without needing a long fixture.
    _, cursor = session.page(list(range(20)), ReadInput(handle="h_minor"), 8, "program")
    assert cursor
    with pytest.raises(InvalidResearchRecord, match="different program handle"):
        session.program(ReadInput(handle="h_bs", cursor=cursor), None)


def test_course_and_document_reads_stay_in_scope_and_page_whole_chunks(db, collection, tmp_path):
    corpus = _corpus(db, collection, tmp_path)
    scope = _scope(db, collection)
    (econ,) = read_courses(db, scope, ["ECON 201"])
    assert econ.title == "Intermediate Macroeconomics" and econ.requisites_raw == "ECON 101"
    assert econ.chunks and "ECON 201" in econ.chunks[0].text
    assert read_courses(db, _scope(db, collection, source_ids=[corpus["policy"]]),
                        ["ECON 201"]) == []
    with pytest.raises(ValueError):
        read_courses(db, scope, ["ECON 101", "ECON 201", "MATH 111", "MATH 112", "ECON 301",
                                 "ECON 302", "ECON 303", "ECON 304", "ECON 305"])
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


def _atlas_terms(db, source_id, shown):
    """One offering snapshot per term; each code maps to its review status."""
    hash_of = "0" * 64
    for number, (term, codes) in enumerate(shown.items()):
        capture = f"{number:064x}"
        held = sum(status != "eligible" for status in codes.values())
        db.execute(queries.get("insert_atlas_offering_snapshot"), {
            "source_id": source_id, "capture_set_hash": capture, "career": "Emory College",
            "term": term, "index_evidence_hash": hash_of, "announced_count": 1,
            "result_row_count": max(len(codes), 1), "eligible_row_count": max(len(codes), 1) - held,
            "held_row_count": held, "search_criteria": "test", "capture_hashes": "[]",
            "observed_from": "2026-01-01T00:00:00Z", "observed_until": "2026-01-01T00:00:00Z"})
        for ordinal, (code, status) in enumerate(codes.items()):
            db.execute(queries.get("insert_atlas_offering_row"), {
                "source_id": source_id, "capture_set_hash": capture, "row_ordinal": ordinal,
                "result_cursor": ordinal, "raw_course_code": code, "title": code,
                "class_number": None, "group_identity": code, "is_grouped": True,
                "matched_identity": code, "srcdb": "1", "credits": None, "description": code,
                "restrictions": None, "notes": None, "notices": "[]", "raw_metadata": "{}",
                "evidence_hash": hash_of, "review_status": status,
                "observed_at": "2026-01-01T00:00:00Z", "capture_envelope_hash": hash_of})


@pytest.mark.parametrize(("terms", "lifecycle", "expected"), [
    (["Fall 2026", "Spring 2024", "Fall 2024", "Spring 2027"], "retired", {
        "ECON 101": None,
        "ECON 201": "from Spring 2024 to Spring 2027, but ECON 201W, ECON_OX 201",
        "MATH 111": "from Spring 2024 to Spring 2027. It is no longer offered"}),
    # The Atlas import kept the master row current, so a listing gap is not retirement.
    (["Fall 2026", "Spring 2024", "Fall 2024", "Spring 2027"], "current", {
        "ECON 101": None,
        "ECON 201": "from Spring 2024 to Spring 2027, but ECON 201W, ECON_OX 201",
        "MATH 111": None}),
    # Too few terms to tell a slower cycle from a retired course.
    (["Fall 2026", "Spring 2024", "Spring 2027"], "retired",
     {"ECON 101": None, "ECON 201": None, "MATH 111": None}),
])
def test_course_read_states_what_the_atlas_terms_show(
        db, collection, tmp_path, terms, lifecycle, expected):
    from app.config import load_settings
    from app.generation.research_tools import ResearchSession
    from tests.fakes import FakeEmbedder
    from tests.helpers import lenient_config

    corpus, scope, store, run = _run(db, collection, tmp_path)
    db.execute("UPDATE courses SET lifecycle = %s WHERE collection_id = %s AND code = 'MATH 111'",
               (lifecycle, collection))
    _atlas_terms(db, corpus["policy"], {term: {"ECON 101": "eligible"} for term in terms[:-1]} | {
        terms[-1]: {"ECON 101": "eligible", "ECON 201W": "eligible", "ECON_OX 201": "eligible",
                    "MATH 111": "held_placeholder", "QTM 190": "eligible"}})
    session = ResearchSession(db, scope, store, run, settings=load_settings(),
                              config=lenient_config(), embedder=FakeEmbedder(8))
    read = session.registry().invoke(
        "advisor_read_courses", {"course_codes": list(expected)}, session.tool_scope(lambda: None))
    courses = {course["code"]: course for course in read.data["courses"]}
    assert set(courses) == set(expected)
    for code, note in expected.items():
        assert bool(courses[code]["offered_terms"]) == (code == "ECON 101")
        assert (note in courses[code]["offering_note"]) if note else (
            "offering_note" not in courses[code])
    # A long list is a queue, read in the order asked: the first batch now, the
    # rest handed back unread, so the model stops once it has what it needs.
    wanted = [*expected, *(f"ECON {n}" for n in range(901, 908))]
    batch = session.registry().invoke(
        "advisor_read_courses", {"course_codes": wanted}, session.tool_scope(lambda: None))
    assert {course["code"] for course in batch.data["courses"]} == set(expected)
    assert batch.data["queued_codes"] == wanted[8:]
    # Advice naming an unread course is sent back with that record already
    # opened, so the next round can keep the advice instead of dropping it.
    rejected = session.registry().invoke("advisor_finish", {"decision": {
        "outcome": "recommendation", "completeness": "complete", "blocks": [{
            "kind": "judgment", "text": "MATH 112 is a good next step.",
            "evidence_ids": batch.data["evidence_ids"][:1]}]}}, session.tool_scope(lambda: None))
    assert "the course records for MATH 112 are now in" in rejected.limitations[0]
    # Grouping options by interest leaves a retired course out of every group.
    grouped = session.registry().invoke(
        "advisor_group_courses", {"course_codes": list(expected)}, session.tool_scope(lambda: None))
    assert grouped.data["not_offered"] == (["MATH 111"] if expected["MATH 111"] else [])
    answer = SimpleNamespace(recommended_course_codes=(), candidate_ids=(), blocks=[
        SimpleNamespace(text="ECON 101, ECON 201 and MATH 111")])
    assert session._retired_courses(answer) == (
        {"MATH 111"} if expected["MATH 111"] else frozenset())
    # A listing keeps the code the Atlas printed: QTM 190 is DATASCI 190.
    assert session._offerings(["DATASCI 190"])[0] == {"DATASCI 190": [terms[-1]]}


@pytest.mark.parametrize(("question", "read"), [
    ("can i take the cs minor courses s/u", True),
    ("what does the cs minor require", False),
])
def test_a_grading_question_reads_the_programs_own_grading_line(
        db, collection, tmp_path, monkeypatch, question, read):
    from app.config import load_settings
    from app.generation import research_tools
    from tests.fakes import FakeEmbedder
    from tests.helpers import lenient_config

    corpus, scope, store, run = _run(db, collection, tmp_path)
    monkeypatch.setattr(research_tools, "PROGRAM_PAGE_ITEMS", 1)
    session = research_tools.ResearchSession(db, scope, store, run, settings=load_settings(),
                                             config=lenient_config(), embedder=FakeEmbedder(8))
    session.question = question
    result = session.registry().invoke(
        "advisor_read_program", {"handle": session.handle("program", corpus["minor"])},
        session.tool_scope(lambda: None))
    graded = [item for item in result.data["items"] if "letter grade" in item["text"]]
    assert bool(graded and graded[0]["evidence_ids"]) is read
    assert bool(session.grading_rules) is read
    assert result.next_cursor


@pytest.mark.parametrize("read", ["program", "courses", "document", "metadata"])
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
                    if read == "metadata":
                        from app.ingest.provenance import SourceProvenance

                        source = other.execute("SELECT file_hash FROM sources WHERE source_id = %s",
                                               (source_id,)).fetchone()
                        other.execute(queries.get("upsert_source_provenance"),
                            SourceProvenance("institutional_policy", publisher="Verified office").row(
                                source_id, source["file_hash"]))
                    else:
                        other.execute(queries.get("claim_source_index_attempt"), {
                            "source_id": source_id, "expected_index_revision": None})
                    other.commit()
                except errors.LockNotAvailable:
                    other.rollback()
                    blocked.append(source_id)

    monkeypatch.setattr(research_repository, "verify_sources", check_then_reindex)
    {"program": lambda: read_program(db, scope, corpus["bs"]),
     "courses": lambda: read_courses(db, scope, ["ECON 201"]),
     "document": lambda: read_document(db, scope, corpus["policy"]),
     "metadata": lambda: read_document(db, scope, corpus["policy"])}[read]()
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


@pytest.mark.parametrize("mutation", ["reindex", "metadata"])
def test_evidence_comes_only_from_the_runs_pinned_sources(db, collection, tmp_path, mutation):
    corpus = _corpus(db, collection, tmp_path)
    if mutation == "metadata":
        from app.ingest.provenance import SourceProvenance

        row = db.execute("SELECT file_hash FROM sources WHERE source_id = %s",
                         (corpus["policy"],)).fetchone()
        db.execute(queries.get("upsert_source_provenance"),
            SourceProvenance("institutional_policy", publisher="Original office").row(
                corpus["policy"], row["file_hash"]))
        db.commit()
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
        Acquired(policy_chunk, replace(policy, source_kind="institutional_catalog")),  # relabeled
        Acquired(policy_chunk, replace(policy, publisher="Forged publisher")),
    ):
        with pytest.raises(InvalidResearchRecord):
            store.acquire(run.run_id, [bad], action_kind="read_document", summary="Bad read")
    assert store.evidence(run.run_id) == [] and store.steps(run.run_id) == []
    good = store.acquire(run.run_id, [Acquired(policy_chunk, policy)],
                         action_kind="read_document", summary="Read the policy")
    assert good.evidence_ids == ("e1",)
    # Content or metadata updates stop a run; saved evidence keeps its original labels.
    if mutation == "reindex":
        db.execute(queries.get("claim_source_index_attempt"), {
            "source_id": corpus["policy"], "expected_index_revision": None})
    else:
        from app.ingest.provenance import SourceProvenance

        db.execute(queries.get("upsert_source_provenance"),
            SourceProvenance("institutional_policy", publisher="Verified office",
                institution="Fixture University", title="Grading information",
                applicability_status="reviewed_default").row(corpus["policy"], row["file_hash"]))
    db.commit()
    assert store.evidence(run.run_id)[0].publisher == policy.publisher
    with pytest.raises(ScopeChanged):
        store.acquire(run.run_id, [Acquired(policy_chunk, policy)],
                      action_kind="read_document", summary="Read again")
    with pytest.raises(ScopeChanged):
        read_document(db, everything, corpus["policy"])
    with pytest.raises(ScopeChanged):
        store.record_decision(run.run_id, FinalDecision(outcome="information", completeness="complete",
            blocks=(DecisionBlock(kind="fact", text="Letter grades apply.", evidence_ids=("e1",)),)),
            render_version="r1")
    if mutation == "metadata":
        refreshed = _scope(db, collection)
        assert refreshed.digest != everything.digest
        assert refreshed.sources[corpus["policy"]].index_revision == policy.index_revision
        # Format 1 is readable, but cannot approve metadata it never pinned.
        legacy = everything.manifest()
        legacy["format"] = "1"
        legacy["sources"] = [source[:3] for source in legacy["sources"]]
        with pytest.raises(ScopeChanged):
            research_repository.verify_manifest(db, legacy)


def test_state_is_optimistic_and_cites_only_issued_evidence(db, collection, tmp_path):
    from app.advising.research_types import ResearchNote, ResearchTask

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
    for field, item in (
        ("plan", ResearchTask(id="read", task="Check the course", evidence_ids=("e9",))),
        ("notes", ResearchNote(id="read", text="A working summary", basis="evidence_summary",
                               evidence_ids=("e9",))),
    ):
        with pytest.raises(InvalidResearchRecord, match="never issued"):
            store.update_state(run.run_id, state.model_copy(update={field: (item,)}), expected_revision=1)


@pytest.mark.parametrize("scope_kind, publication", [
    ("all", "answer"), ("crowded", "answer"), ("legacy_null", "answer"), ("narrow", "answer"), ("empty", "answer"),
    ("other_workspace", "answer"), ("other_conversation", "answer"),
    ("changed", "answer"), ("all", "failed"), ("all", "abandoned"),
])
def test_conversation_workspace_reuses_only_published_scoped_work(
        db, collection, tmp_path, scope_kind, publication):
    from app.advising.research_types import Candidate, ResearchNote, ResearchTask, StudentContext
    from app.config import load_settings
    from app.conversations.store import ConversationStore
    from app.generation.research_tools import ResearchSession
    from tests.fakes import FakeEmbedder
    from tests.helpers import lenient_config

    corpus = _corpus(db, collection, tmp_path)
    if scope_kind == "crowded":
        from tests.test_atlas_import import _SAMPLE

        extra = "\n".join(json.dumps({"code": f"TEST {100 + i}", "title": f"Fixture {i}"})
                          for i in range(99))
        corpus["atlas"] = import_atlas_sample(db, collection, tmp_path, text=_SAMPLE + "\n" + extra)
    conversations = ConversationStore(db, "development")
    conversation = UUID(conversations.create("Workspace")["conversationId"])
    request = uuid4()
    conversations.begin(conversation, request, 0, {"query": "Compare paths"})
    scope, store = _scope(db, collection), ResearchStore(db, "development")
    run, _ = store.start(scope, run_id=uuid4(), prompt_version="test", config_version="6",
                         conversation_id=conversation, request_id=request)
    course = read_courses(db, scope, ["ECON 201"])[0]
    store.acquire(run.run_id, [Acquired(c, scope.sources[c.source_id]) for c in course.chunks],
                  action_kind="read_courses", summary="Read course")
    if scope_kind == "crowded":
        for start in range(0, 99, 4):
            reads = read_courses(db, scope, [f"TEST {100 + i}" for i in range(start, min(start + 4, 99))])
            store.acquire(run.run_id, [Acquired(c, scope.sources[c.source_id]) for r in reads for c in r.chunks],
                          action_kind="read_courses", summary="Additional fixture readings")
    evidence_ids = tuple(r.evidence_id for r in store.evidence(run.run_id))
    old_handle = "h_" + uuid5(run.run_id, f"program:{corpus['bs']}").hex
    store.update_state(run.run_id, ResearchState(revision=0, decision_question="Compare paths",
        student_context=(StudentContext(statement="I prefer flexibility", basis="user_report"),),
        candidates=(Candidate(id="cs", program_handles=(old_handle,), description="Investigate CS",
                              status="conditional", evidence_ids=("e1",)),),
        notes=(ResearchNote(id="course", text="Check this course's restrictions", basis="evidence_summary",
                            evidence_ids=("e1",)), *(ResearchNote(id=f"preference{i}", text="p" * 650,
                            basis="user_report") for i in range(7) if scope_kind == "crowded")),
        plan=(ResearchTask(id="compare", task="Compare the alternatives", status="active"),),
        active_evidence_ids=evidence_ids), expected_revision=0)
    result = {"type": publication, "answer": "Saved work", "research": {
        "runId": str(run.run_id), "outcome": "information", "completeness": "complete"}}
    if scope_kind == "legacy_null":
        result.update(citations=None, trace={"contextBlock": None})
    conversations.finish(conversation, request, result,
                         state={"answer": "completed", "failed": "failed", "abandoned": "abandoned"}[publication])
    if scope_kind == "changed":
        import_atlas_sample(db, collection, tmp_path, allow_course_removal=True, text=(
            '{"code":"ECON 201","title":"New edition","description":"Different material."}'))
    workspace_key = "other" if scope_kind == "other_workspace" else "development"
    sources = [] if scope_kind == "empty" else [corpus["policy"]] if scope_kind == "narrow" else None
    scope = build_scope(db, collection_id=collection, workspace_key=workspace_key, source_ids=sources)
    store = ResearchStore(db, workspace_key)
    new_run, _ = store.start(scope, run_id=uuid4(), prompt_version="test", config_version="6")
    session = ResearchSession(db, scope, store, new_run, settings=load_settings(),
                              config=lenient_config(), embedder=FakeEmbedder(8))
    selected = UUID(conversations.create("Separate")["conversationId"]) if (
        scope_kind == "other_conversation") else conversation
    session.open_workspace(selected)
    checkpoint = session.prior_workspace
    if publication != "answer" or scope_kind in {"other_workspace", "other_conversation"}:
        assert checkpoint is None and not session.recall_page["data"]["references"]
    else:
        notebook = checkpoint["notebook"]
        assert notebook["student_context"][0]["statement"] == "I prefer flexibility"
        assert notebook["plan"][0]["task"] == "Compare the alternatives"
        assert "evidence_ids" not in json.dumps(notebook) and not session.active
        if scope_kind in {"all", "crowded", "legacy_null"}:
            assert notebook["notes"][0]["recall_handles"]
            assert notebook["candidates"][0]["program_handles"] != [old_handle]
            assert notebook["candidates"][0]["status"] == "investigating"
            assert [identity for kind, identity in session.handles.values() if kind == "program"] == [corpus["bs"]]
            memory_handle = session.recall_page["data"]["references"][0]["handle"]
            registry = session.registry()
            wrong_tool = registry.invoke("advisor_read_courses", {"handles": [memory_handle]},
                                         session.tool_scope(lambda: None))
            assert wrong_tool.status == "conflict"
            assert "advisor_recall" in wrong_tool.limitations[0]
            mixed_recall = registry.invoke("advisor_recall", {
                "handles": [memory_handle], "query": "different search"},
                session.tool_scope(lambda: None))
            assert mixed_recall.status == "conflict"
            assert "omit query and cursor" in mixed_recall.limitations[0]
            unissued = registry.invoke("advisor_read_courses", {"handles": ["h_unissued"]},
                                       session.tool_scope(lambda: None))
            assert unissued.status == "not_found" and not session.active
            reopened = registry.invoke("advisor_recall", {"handles": [memory_handle]},
                                       session.tool_scope(lambda: None))
            assert reopened.status == "ok" and reopened.data["evidence_ids"]
            reopened_ids = set(reopened.data["evidence_ids"])
            assert reopened_ids <= set(session.active)
            if scope_kind == "crowded":
                assert len(notebook["notes"]) == 8
                assert len(notebook["recall_handles"]) < len(evidence_ids)
                assert checkpoint["omitted_items"] > 0
                cursor = session.recall_page["next_cursor"]
                registry = session.registry()
                denied = registry.invoke("advisor_read_conversation", {"cursor": cursor},
                                         session.tool_scope(lambda: None))
                assert denied.status == "not_found" and not session.conversation_page
                continued = registry.invoke("advisor_recall", {"cursor": cursor, "query": "reworded"},
                                            session.tool_scope(lambda: None))
                assert continued.status == "ok" and continued.data["query"] == ""
                assert continued.data["references"] and set(session.active) == reopened_ids
        else:
            assert not notebook["notes"] and not notebook["candidates"]
            assert checkpoint["unavailable_references"]


@pytest.mark.parametrize("resume", ["evidence", "conversation", "escaped_conversation", "reworded_conversation", "natural_query_conversation", "empty_search_conversation"])
def test_saved_research_reopens_evidence_beyond_recent_history_and_continues_its_plan(
        db, collection, tmp_path, monkeypatch, resume):
    from app.advising.research_workspace import ConversationWorkspace
    from app.api.routes.conversations import ConversationQuery, _prepare_stream_snapshot
    from app.config import load_settings
    from app.conversations.store import ConversationStore
    from app.generation.research_tools import ResearchSession, StateInput
    from app.llm.base import ToolCall
    from tests.fakes import FakeEmbedder
    from tests.helpers import api_client, collection_name, lenient_config

    corpus = _corpus(db, collection, tmp_path)
    extra = [ingest_text(db, collection, tmp_path, f"seminar-{i}.txt", f"Seminar {i}: one credit.")
             for i in range(14)]
    conversations = ConversationStore(db, "development")
    conversation = UUID(conversations.create("Long investigation")["conversationId"])
    first = uuid4()
    filler = '\\"\x01' * 2500 if resume == "escaped_conversation" else "λ" * 7000
    original_report = "I prefer flexibility. " + filler + " I prefer afternoon classes."
    conversations.begin(conversation, first, 0, {"query": original_report})
    scope, store = _scope(db, collection), ResearchStore(db, "development")
    run, _ = store.start(scope, run_id=uuid4(), prompt_version="test", config_version="6",
                         conversation_id=conversation, request_id=first)
    session = ResearchSession(db, scope, store, run, settings=load_settings(),
                              config=lenient_config(), embedder=FakeEmbedder(8))
    course = read_courses(db, scope, ["ECON 201"])[0]
    ids = session.acquire(list(course.chunks), refs=("ECON 201",), action="read_courses")
    session.update(StateInput.model_validate({"state": {"revision": 0,
        "decision_question": "Explore economics", "active_evidence_ids": ids,
        "plan": [{"id": "next", "task": "Check the preparation rules", "status": "pending"}],
        "notes": [{"id": "macro", "text": "Reopen the economics course", "basis": "evidence_summary",
                   "evidence_ids": ids}]}}), None)
    conversations.finish(conversation, first, {"type": "answer", "answer": "Economics is worth exploring.",
        "research": {"runId": str(run.run_id), "outcome": "information", "completeness": "partial"}})
    noise = uuid4()
    selected_sources = [str(corpus["atlas"]), *map(str, extra)]
    conversations.begin(conversation, noise, 2, {"query": "List seminars", "effective": {
        "collectionId": collection_name(db, collection), "sourceIds": selected_sources}})
    bodies = [{"chunkId": str(c.chunk_id), "sourceId": str(c.source_id), "sourceName": "Seminar",
               "locatorLabel": c.locator_label, "text": c.text}
              for sid in extra for c in read_document(db, scope, sid).chunks]
    conversations.finish(conversation, noise, {"type": "answer", "answer": "Seminars listed.",
        "citations": bodies, "trace": {"contextBlock": bodies}})
    rows = conversations.context(conversation)
    # The last advising scope must survive falling outside the 50-turn recent
    # view; these transcript-only turns do not reset that source selection.
    for sequence in range(4, 56, 2):
        request = uuid4()
        conversations.begin(conversation, request, sequence, {"query": "history", "request": {"mode": "history"}})
        conversations.finish(conversation, request, {"type": "history", "answer": "Saved transcript view."})
    workspace = ConversationWorkspace(db, scope, store, conversation)
    if resume == "evidence":
        from app.util.digest import canonical_json_bytes

        boundary = UUID(conversations.create("Serialized page boundary")["conversationId"])
        metadata = [{"sequence": 1, "role": "user", "text": "", "text_offset": 0, "complete": True}]
        request = uuid4()
        conversations.begin(boundary, request, 0, {"query": "x" * (6000 - len(canonical_json_bytes(metadata)))})
        conversations.finish(boundary, request, {"type": "history", "answer": ""})
        boundary_reader = ConversationWorkspace(db, scope, store, boundary)
        boundary_page, next_position = boundary_reader.turns()
        assert len(canonical_json_bytes(boundary_page)) <= 6000 and next_position == (2, 0)
        last_page, next_position = boundary_reader.turns(sequence=2)
        assert last_page[0]["text"] == "" and next_position is None
    transcript, position = workspace.turns()
    assert position and not transcript[0]["complete"]
    recovered = transcript[0]["text"]
    while position and position[0] == 1:
        assert len(json.dumps(transcript, ensure_ascii=False, separators=(",", ":")).encode()) <= 6000
        transcript, position = workspace.turns(sequence=position[0], offset=position[1])
        recovered += next(t["text"] for t in transcript if t["sequence"] == 1)
    assert recovered == rows[0]["text"]
    observed = {}
    def script(context, index):
        def call(name, args):
            return [ToolCall(f"workspace-{index}", "advisor_" + name, args)]
        if resume == "empty_search_conversation":
            if index < 2:
                return call("read_conversation", {"query": f"unmatchedtoken{index}"})
            assert context["finish_required"] and not context["exact_evidence_untrusted"]
            return call("finish", {"decision": {"outcome": "insufficient_evidence", "completeness": "partial",
                "blocks": [{"kind": "limitation", "text": "The searches did not locate that earlier report."}]}})
        if resume.endswith("conversation"):
            page = context.get("conversation_page_untrusted")
            if page is None:
                observed["pages"] = []
                return call("read_conversation", {"query": "original afternoon schedule preference"
                                                  if resume == "natural_query_conversation" else "afternoon"})
            text = "".join(turn["text"] for turn in page["data"]["turns_untrusted"])
            if page.get("next_cursor") not in observed["pages"]:
                observed.setdefault("report", "")
                observed["report"] += text
                observed["pages"].append(page.get("next_cursor"))
            if page["next_cursor"]:
                assert not context["finish_required"]
                arguments = {"cursor": page["next_cursor"]}
                if resume != "escaped_conversation":
                    arguments["query"] = "another query" if resume == "reworded_conversation" else "afternoon"
                return call("read_conversation", arguments)
            assert observed["report"] == original_report
            if not context["state_model_assessments"]:
                return call("update_research", {"state": {"revision": 0, "decision_question": "Continue",
                    "plan": [{"id": "next", "task": "Recover the reported preference", "status": "done"}],
                    "notes": [{"id": "preference", "text": "Student prefers afternoon classes", "basis": "user_report"}]}})
            return call("finish", {"decision": {"outcome": "information", "completeness": "complete",
                "blocks": [{"kind": "assumption", "text": "You reported preferring afternoon classes."}]}})
        if index == 0:
            previous = context["prior_workspace_untrusted"]["notebook"]
            assert previous["plan"][0]["task"] == "Check the preparation rules"
            assert not context["exact_evidence_untrusted"]
            assert json.loads(context["history_untrusted"].split("\nExact referenced")[0])["omitted_turns"] > 0
            observed["handle"] = previous["notes"][0]["recall_handles"][0]
            assert context["conversation_recall_index"]["next_cursor"]
            return call("recall", {"cursor": context["conversation_recall_index"]["next_cursor"]})
        if index == 1:
            assert observed["handle"] in {r["handle"] for r in context["conversation_recall_index"]["data"]["references"]}
            return call("recall", {"handles": [observed["handle"]]})
        ids = [e["evidence_id"] for e in context["exact_evidence_untrusted"]]
        assert ids and "Intermediate Macroeconomics" in context["exact_evidence_untrusted"][0]["exact_text"]
        if index == 2:
            return call("update_research", {"state": {"revision": 0, "decision_question": "Continue",
                "active_evidence_ids": ids, "plan": [{"id": "next", "task": "Check preparation",
                                                       "status": "done", "evidence_ids": ids}]}})
        if index == 3:
            return call("recall", {"handles": [observed["handle"]]})
        return call("finish", {"decision": {"outcome": "information", "completeness": "complete",
            "blocks": [{"kind": "fact", "text": "ECON 201 lists ECON 101 as preparation.",
                        "evidence_ids": ids}]}})
    provider = _research_provider(monkeypatch, script)
    client = api_client(db, collection, provider=provider)
    client.app.state.settings = replace(client.app.state.settings, llm_provider="openai", llm_model_id="fake",
        tools_enabled=True, routing_enabled=False, db_pool_max_size=0,
        conversation_context_max_bytes=1024)
    stream_snapshot = _prepare_stream_snapshot(conversation,
        ConversationQuery(query="Continue that work", requestId=uuid4(), expectedSequence=56),
        db, client.app.state.settings, "development", conversations)
    assert json.loads(stream_snapshot["context"]["historyContext"])["omitted_turns"] > 0
    assert set(stream_snapshot["effective"]["sourceIds"]) == set(selected_sources)
    started = send_turn(client, conversation, {"query": "Continue that work",
        "requestId": str(uuid4()), "expectedSequence": 56})
    assert started.status_code == 202, started.text
    answer = client.get(f"/api/conversations/{conversation}?after=56").json()["turns"][-1]
    if resume == "evidence":
        assert answer["evidence"]["citations"][0]["chunkId"] == str(course.chunks[0].chunk_id)
    elif resume != "empty_search_conversation":
        assert not answer["evidence"]["citations"] and "afternoon" in answer["text"]
    else:
        assert not answer["evidence"]["citations"]
    saved = store.get(UUID(answer["evidence"]["research"]["runId"]))
    if resume == "empty_search_conversation":
        assert saved.status == "insufficient_evidence" and saved.state is None
        assert len(provider.requests) == 3
    else:
        assert saved.status == "completed" and saved.state.plan[0].status == "done"
    assert set(saved.scope_manifest["selection"]) == set(selected_sources)


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
    import inspect

    from app.llm.base import GenerationResult
    from tests.fakes import ScriptedProvider

    provider = ScriptedProvider()
    generate = provider.generate
    def scripted(request):
        context = json.loads(request.messages[1].content)
        if len(inspect.signature(script).parameters) >= 3:
            scripted_result = script(context, len(provider.requests), request)
        else:
            scripted_result = script(context, len(provider.requests))
        if isinstance(scripted_result, GenerationResult):
            provider.result = scripted_result
        else:
            provider.result = GenerationResult(tool_calls=tuple(scripted_result),
                finish_reason="tool_calls", usage=provider.result.usage)
        return generate(request)
    monkeypatch.setattr(provider, "generate", scripted)
    return provider


@pytest.mark.parametrize("scenario", ["clarification", "course", "program_comparison", "program_read_before_preparation", "preparation", "preparation_after_state_rejection", "preparation_or_selected", "preparation_or_completed", "preparation_or_completed_long_unselected_branch", "preparation_missing_or_choice", "preparation_missing_prerequisite", "search_document", "saved_clarification", "schema_repair", "document_state_repair", "discovery_read_cadence", "repeated_read_no_progress", "cached_read_priority", "free_json_finish", "finish_repair_extra_field", "finish_repair_malformed_then_validation", "finish_format_slips_spare_corrections", "finish_rejects_unsupported_references", "finish_feedback_roundtrip", "truncated_research_repair", "truncated_research_exhausted", "rate_limited_research_retry", "program_paging", "program_paging_budget", "factual_evidence_retention", "program_audit", "course_suggestions", "which_majors"])
def test_research_answers_progressively_and_persists_its_decision(
        db, collection, tmp_path, monkeypatch, scenario):
    from app.api.query_service import query_response
    from app.api.schemas import QueryRequest
    from app.config import load_settings
    from app.llm.base import GenerationResult, ToolCall
    from app.operations.budget import OperationBudgetController
    from tests.fakes import FakeEmbedder
    from tests.helpers import MODEL, collection_name, lenient_config

    _corpus(db, collection, tmp_path)
    if scenario == "course_suggestions":
        from app.simgraph.build import build_simgraph

        build_simgraph(db, collection_id=collection, model=MODEL, config_version=1,
                       simgraph=lenient_config().simgraph)
        db.commit()
    # FakeEmbedder's vectors are random, so no goal reaches the live similarity floor.
    research_config = replace(lenient_config(), simgraph=replace(
        lenient_config().simgraph, walk_min_similarity=-1.0))
    preparation_or_scenarios = {
        "preparation_or_selected", "preparation_or_completed",
        "preparation_or_completed_long_unselected_branch", "preparation_missing_or_choice"
    }
    if scenario in {"program_paging", "program_paging_budget"}:
        title = "Computer Science (BS)"
        body = CS_BS + "\n" + "\n".join(f"- CS {n}" for n in range(701, 761))
        identity = _ingest_catalog_file(db, collection, tmp_path, title, body, name="long-bs.md")
        record = to_record(parse_program(title, body), catalog="Test Catalog",
                           source_url="https://example.test/catalog.pdf", first_page=3, last_page=4)
        path = tmp_path / "paged-programs.jsonl"
        path.write_text(json.dumps({**record, "source": identity}) + "\n", encoding="utf-8")
        import_programs(db, collection_id=collection, path=path)
    if scenario in preparation_or_scenarios:
        from tests.test_atlas_import import _SAMPLE

        atlas_with_alternatives = (
            _SAMPLE
            + '\n{"code":"BIOL 101","title":"Biology I","description":"Intro biology."}'
            + '\n{"code":"BIOL 102","title":"Biology II","description":"Advanced biology.",'
              '"requisites":"BIOL 101 or BIOL 103 or equivalent transfer credit as a prerequisite."}'
            + "\n" + json.dumps({
                "code": "BIOL 103", "title": "Biology alternate",
                "description": "Alternative preparation.",
                "requisites": "BIOL 104" if scenario == "preparation_or_completed_long_unselected_branch" else None,
            })
        )
        if scenario == "preparation_or_completed_long_unselected_branch":
            atlas_with_alternatives += "\n" + "\n".join(
                json.dumps({
                    "code": f"BIOL {code}", "title": f"Biology {code}",
                    "description": "Unselected alternative branch.",
                    "requisites": f"BIOL {code + 1}" if code < 140 else None,
                })
                for code in range(104, 141)
            )
        import_atlas_sample(db, collection, tmp_path, atlas_with_alternatives)
    settings = replace(load_settings(), llm_provider="openai", llm_model_id="fake",
        tools_enabled=True, routing_enabled=False,
        embedding_model_id=MODEL, db_pool_max_size=0,
        research_max_tool_calls=8 if scenario in {"program_read_before_preparation", "preparation_after_state_rejection"} else 24,
        research_max_calls=8 if scenario == "preparation_after_state_rejection" else load_settings().research_max_calls)
    if scenario == "preparation_or_completed_long_unselected_branch":
        # This chain sits at the default budget; the point is its length, not the limit.
        settings = replace(settings, research_max_prompt_bytes=56_000)
    if scenario == "program_paging_budget":
        # "Help choose a path" is advice, so its budget is the advice budget.
        settings = replace(settings, research_max_calls=5, research_advice_max_calls=5)
    observed = {}
    def script(context, index, request=None):
        def call(name, args):
            return [ToolCall(f"call-{index}", "advisor_" + name, args)]
        if scenario in {"program_paging", "program_paging_budget"}:
            if index == 0:
                return call("list_programs", {})
            progress = context["program_read_progress"]
            if not progress:
                handle = next(h["handle"] for h in context["issued_handles"] if h["kind"] == "program")
                return call("read_program", {"handle": handle})
            ids = [e["evidence_id"] for e in context["exact_evidence_untrusted"]]
            if context["state_update_requested"]:
                return call("update_research", {"state": {"revision": 0,
                    "decision_question": "Read all requirements", "active_evidence_ids": ids}})
            current = progress[0]
            if current["next_cursor"] and not context["finish_required"]:
                return call("read_program", {"handle": current["handle"],
                                             "cursor": current["next_cursor"]})
            observed["coverage"] = (current["read_items"], current["total_items"])
            return call("finish", {"decision": {"outcome": "information", "completeness": "complete",
                "blocks": [{"kind": "fact", "text": "CS 170 is listed in the program.",
                            "evidence_ids": ids}]}})
        if scenario == "factual_evidence_retention":
            if index == 0:
                return call("read_courses", {"course_codes": ["ECON 101"]})
            if index == 1:
                observed["target"] = context["exact_evidence_untrusted"][0]["evidence_id"]
                return call("update_research", {"state": {"revision": 0,
                    "decision_question": "Describe ECON 101", "active_evidence_ids": []}})
            if not context["finish_required"]:
                return call("read_courses", {"course_codes": ["ECON 201"]})
            return call("finish", {"decision": {"outcome": "information", "completeness": "complete",
                "blocks": [{"kind": "fact", "text": "ECON 101 is Principles of Macroeconomics.",
                            "evidence_ids": [observed["target"]]}]}})
        if scenario == "preparation_after_state_rejection":
            if index == 0:
                return call("read_courses", {"course_codes": ["ECON 201"]})
            if index == 1:
                assert context["state_update_requested"]
                return call("update_research", {"expected_revision": 0, "state": {
                    "revision": 0, "decision_question": "Plan ECON 201 prerequisites",
                    "active_evidence_ids": [e["evidence_id"] for e in context["exact_evidence_untrusted"]],
                    "candidates": [{"id": "h_" + "a" * 32, "course_codes": ["ECON 201"],
                                    "description": "Plan ECON 201", "status": "conditional"}]}})
            if index == 2:
                assert context["state_update_requested"]
                assert "advisor_check_preparation" in {tool.name for tool in request.tools}
                assert context["executed_preparation_checks"] == []
                observed["malformed_state_rejected"] = "32 characters" in context["instruction"]
                # The user explicitly reported no completions, so an empty list
                # is an asserted input here, not a default for unknown history.
                return call("check_preparation", {
                    "selected_codes": ["ECON 201"], "completed_codes": []})
            check = context["executed_preparation_checks"][0]
            assert check["completed_codes"] == []
            assert check["selected_codes"] == ["ECON 201"]
            observed["context_preparation_check"] = check
            ids = [e["evidence_id"] for e in context["exact_evidence_untrusted"]]
            return call("finish", {"decision": {
                "outcome": "information", "completeness": "complete",
                "preparation_check_ids": [check["check_id"]],
                "preparation_claims": [{"check_id": check["check_id"],
                    "selected_codes": check["selected_codes"],
                    "completed_codes": check["completed_codes"], "conclusion": check["conclusion"]}],
                "blocks": [{"kind": "fact", "text": "The deterministic check reports ECON 201 preparation details.",
                    "evidence_ids": ids}]}})
        if scenario == "program_read_before_preparation":
            observed.setdefault("actions", [])
            available = {tool.name for tool in request.tools}
            state = context.get("state_model_assessments")
            if context["state_update_requested"]:
                handle = next(h["handle"] for h in context["issued_handles"] if h["kind"] == "program")
                ids = [e["evidence_id"] for e in context["exact_evidence_untrusted"]]
                observed["actions"].append("update_state")
                return call("update_research", {"expected_revision": 0, "state": {
                    "revision": 0, "decision_question": "Explore an economics program path",
                    "active_evidence_ids": ids,
                    "candidates": [{"id": "registered_path", "program_handles": [handle],
                                    "description": "Investigate this program path",
                                    "status": "investigating"}]}})
            if state and context["source_read_requested"] and "advisor_read_program" in available:
                candidate_handle = state["candidates"][0]["program_handles"][0]
                observed["actions"].append("read_program")
                observed["program_read_preceded_preparation"] = not any(
                    action == "check_preparation" for action in observed["actions"])
                return call("read_program", {"handle": candidate_handle})
            if context["executed_preparation_checks"]:
                check = context["executed_preparation_checks"][0]
                ids = [e["evidence_id"] for e in context["exact_evidence_untrusted"]]
                observed["actions"].append("finish")
                return call("finish", {"decision": {
                    "outcome": "recommendation", "completeness": "complete",
                    "candidate_ids": ["registered_path"],
                    "preparation_check_ids": [check["check_id"]],
                    "preparation_claims": [{"check_id": check["check_id"],
                        "selected_codes": check["selected_codes"],
                        "completed_codes": check["completed_codes"],
                        "conclusion": check["conclusion"]}],
                    "blocks": [{"kind": "judgment",
                        "text": "I recommend reviewing this program path based on its cited requirement record.",
                        "candidate_ids": ["registered_path"], "evidence_ids": ids}]}})
            if state:
                observed["actions"].append("check_preparation")
                return call("check_preparation", {
                    "selected_codes": ["ECON 201"], "completed_codes": ["ECON 101"]})
            if any(e["source_title"] and "ECON 201" in e["exact_text"]
                   for e in context["exact_evidence_untrusted"]):
                observed["actions"].append("read_courses")
                return call("read_courses", {"course_codes": ["ECON 201"]})
            if any(h["kind"] == "program" for h in context["issued_handles"]):
                observed["actions"].append("read_courses")
                return call("read_courses", {"course_codes": ["ECON 201"]})
            observed["actions"].append("list_programs")
            return call("list_programs", {})
        if scenario in {"clarification", "saved_clarification"}:
            return call("finish", {"decision": {"outcome": "clarification", "completeness": "complete",
                "blocks": [{"kind": "question", "text": "Which career are you considering?"}]}})
        if scenario == "cached_read_priority":
            ids = [e["evidence_id"] for e in context["exact_evidence_untrusted"]]
            if index == 0:
                return call("read_courses", {"course_codes": ["ECON 101"]})
            if index == 1:
                observed["first"] = ids[0]
                return call("update_research", {"expected_revision": 0, "state": {
                    "revision": 0, "decision_question": "What is ECON 101?", "active_evidence_ids": ids}})
            if index == 2:
                # The round that read it is gone; what no passage holds must not be.
                assert context["course_facts"]["ECON 101"]["evidence_ids"] == [observed["first"]]
                return call("read_courses", {"course_codes": ["ECON 201"]})
            if index == 3:
                assert ids[0] != observed["first"]
                observed["second"] = ids[0]
            if index in {3, 4}:
                if index >= 4:
                    assert ids[0] == observed["first"]
                    assert not context["finish_required"]
                return call("read_courses", {"course_codes": ["ECON 101"]})
            assert context["finish_required"] and ids[0] == observed["first"]
            return call("finish", {"decision": {"outcome": "information", "completeness": "complete",
                "blocks": [{"kind": "fact", "text": "ECON 101 is in the catalog.",
                            "evidence_ids": [observed["first"]]}]}})
        if scenario == "discovery_read_cadence":
            if index in {0, 1, 2}:
                if index == 2:
                    assert context["source_read_requested"]
                return call("search", {"query": "letter grades" if index < 2 else "letter grade policy",
                                       "kind": "document"})
            if index == 3:
                assert context["source_read_requested"]
                source = next(h for h in context["issued_handles"] if h["kind"] == "document"
                              and h.get("title") == "grading-policy.txt")
                return call("read_document", {"handle": source["handle"]})
            ids = [e["evidence_id"] for e in context["exact_evidence_untrusted"]]
            if index == 4:
                return call("update_research", {"expected_revision": 0, "state": {
                    "revision": 0, "decision_question": "What does this policy cover?",
                    "active_evidence_ids": ids, "candidates": []}})
            return call("finish", {"decision": {"outcome": "information", "completeness": "complete",
                "blocks": [{"kind": "fact", "text": "The policy describes letter grades.",
                            "evidence_ids": ids}]}})
        if scenario == "repeated_read_no_progress":
            if index == 0:
                return call("search", {"query": "letter grades", "kind": "document"})
            handle = next(h["handle"] for h in context["issued_handles"]
                          if h["kind"] == "document" and h.get("title") == "grading-policy.txt")
            if index == 1:
                return call("read_document", {"handle": handle})
            ids = [e["evidence_id"] for e in context["exact_evidence_untrusted"]]
            if index == 2:
                return call("update_research", {"expected_revision": 0, "state": {
                    "revision": 0, "decision_question": "What does this policy cover?",
                    "active_evidence_ids": ids, "candidates": []}})
            if index in {3, 4}:
                return call("read_document", {"handle": handle})
            assert context["finish_required"] is True
            return call("finish", {"decision": {"outcome": "information", "completeness": "complete",
                "blocks": [{"kind": "fact", "text": "The policy describes letter grades.",
                            "evidence_ids": ids}]}})
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
        if scenario == "document_state_repair":
            if index == 0:
                return call("search", {"query": "letter grades", "kind": "document"})
            handle = next(h["handle"] for h in context["issued_handles"] if h["kind"] == "document")
            if index == 1:
                return call("read_document", {"handle": handle})
            ids = [e["evidence_id"] for e in context["exact_evidence_untrusted"]]
            if index in {2, 3}:
                if index == 3:
                    assert "never document or section handles" in context["instruction"]
                return call("update_research", {"expected_revision": 0, "state": {
                    "revision": 0, "decision_question": "What does this policy cover?",
                    "active_evidence_ids": ids, "candidates": [{"id": "document_lookup",
                        "program_handles": [handle], "description": "Read a policy", "status": "investigating"}]
                        if index == 2 else []}})
            # A sourced judgment with no candidate is advice; it now finishes the run.
            return call("finish", {"decision": {"outcome": "information", "completeness": "complete",
                "blocks": [{"kind": "judgment",
                    "text": "The policy describes letter grades.", "evidence_ids": ids}]}})
        if scenario in {"free_json_finish", "finish_repair_extra_field",
                        "finish_repair_malformed_then_validation", "finish_rejects_unsupported_references",
                        "finish_format_slips_spare_corrections"}:
            if index == 0:
                return call("read_courses", {"course_codes": ["ECON 201"]})
            ids = [e["evidence_id"] for e in context["exact_evidence_untrusted"]]
            valid = {"outcome": "information", "completeness": "complete", "blocks": [{
                "kind": "fact", "text": "ECON 201 is in the catalog.", "evidence_ids": ids}]}
            if scenario == "free_json_finish":
                return GenerationResult(text=json.dumps({"decision": valid}), finish_reason="stop",
                                        usage=provider.result.usage)
            if index == 1 and scenario == "finish_repair_extra_field":
                invalid = {**valid, "program_handles": ["h_not_authorized"]}
                return GenerationResult(text=json.dumps({"decision": invalid}), finish_reason="stop",
                                        usage=provider.result.usage)
            if index == 1 and scenario == "finish_repair_malformed_then_validation":
                return GenerationResult(text='{"decision": {"outcome":', finish_reason="stop",
                                        usage=provider.result.usage)
            # Two format slips, then one real content rejection: the slips have
            # their own allowance, so the content correction still gets its round.
            if scenario == "finish_format_slips_spare_corrections" and index in {1, 2, 3}:
                if index == 1:
                    return GenerationResult(text='{"decision": {"outcome":', finish_reason="stop",
                                            usage=provider.result.usage)
                if index == 2:
                    return [ToolCall("no-kinds", "advisor_finish", {"decision": {
                        "outcome": "information", "completeness": "complete",
                        "blocks": [{"evidence_ids": ids}]}})]
                return [ToolCall("bad-reference", "advisor_finish", {"decision": {
                    "outcome": "information", "completeness": "complete", "blocks": [{
                        "kind": "fact", "text": "Unsupported claim.", "evidence_ids": ["e999"]}]}})]
            if index == 1 and scenario == "finish_rejects_unsupported_references":
                bad = {"outcome": "information", "completeness": "complete", "blocks": [{
                    "kind": "fact", "text": "Unsupported claim.", "evidence_ids": ["e999"]}]}
                return GenerationResult(text=json.dumps(bad), finish_reason="stop",
                                        usage=provider.result.usage)
            if scenario == "finish_repair_malformed_then_validation" and index == 2:
                assert context["finish_required"]
                assert "advisor_finish" in context["instruction"]
                assert "advisor_read_courses" not in context["instruction"]
                return [ToolCall("bad-judgment", "advisor_finish", {"decision": {
                    "outcome": "information", "completeness": "complete", "blocks": [{
                        "kind": "judgment", "text": "Unsupported judgment.", "evidence_ids": ids}]}})]
            if scenario == "finish_rejects_unsupported_references":
                # Even after the bounded repair opportunity, an invented evidence ID
                # remains subject to the normal final validator.
                return GenerationResult(text=json.dumps({"decision": {
                    "outcome": "information", "completeness": "complete", "blocks": [{
                        "kind": "fact", "text": "Still unsupported.", "evidence_ids": ["e999"]}]}}),
                    finish_reason="stop", usage=provider.result.usage)
            if index > 1:
                assert context["finish_required"]
                assert "advisor_finish" in context["instruction"]
                assert "advisor_read_courses" not in context["instruction"]
                if scenario == "finish_repair_extra_field":
                    assert "program_handles" in context["instruction"]
                if scenario == "finish_repair_malformed_then_validation" and index == 3:
                    assert "judgment" in context["instruction"].lower()
            return GenerationResult(text=json.dumps({"decision": valid}), finish_reason="stop",
                                    usage=provider.result.usage)
        if scenario == "rate_limited_research_retry" and not observed.get("rate_limited"):
            # A rate limit retries the same round instead of failing the whole run.
            from app.llm.base import RateLimitError
            observed["rate_limited"] = True
            raise RateLimitError("slow down", provider="fake", retry_after=0.01)
        if scenario in {"truncated_research_repair", "truncated_research_exhausted",
                        "rate_limited_research_retry"}:
            # A cut-off reply retries the same round with a doubled allowance; only
            # after the retries run out does the correction path demand a finish.
            retries = {"truncated_research_exhausted": 2, "rate_limited_research_retry": -1}.get(scenario, 0)
            if index <= retries:
                return GenerationResult(text='{"partial":', tool_calls=(
                    ToolCall("partial-read", "advisor_read_courses", {"course_codes": ["ECON 201"]}),
                ), finish_reason="length", usage=provider.result.usage)
            if index == 1 and scenario == "truncated_research_repair":
                assert not context["finish_required"]
                assert "advisor_read_courses" in {tool.name for tool in request.tools}
                assert "output allowance" in context["instruction"]
                assert request.max_output_tokens > provider.requests[0].max_output_tokens
            if scenario == "truncated_research_exhausted":
                assert context["finish_required"]
                assert "truncat" in context["instruction"].lower()
                assert {tool.name for tool in request.tools} == {"advisor_finish"}
            return GenerationResult(text=json.dumps({"decision": {
                "outcome": "insufficient_evidence", "completeness": "partial",
                "blocks": [
                    {"kind": "limitation", "text": "The prior response was truncated, so its partial tool request was discarded and no research action was executed."},
                    {"kind": "question", "text": "Would you like me to retry with a shorter, focused query?"},
                ],
                "unresolved_questions": ["A complete answer was not produced before the response was truncated."]
            }}), finish_reason="stop", usage=provider.result.usage)
        if scenario == "finish_feedback_roundtrip":
            if index == 0:
                return call("finish", {"decision": {
                    "outcome": "information", "completeness": "complete", "blocks": [
                        {"kind": "judgment", "text": "No captured admissions source establishes whether this application is guaranteed.",
                         "candidate_ids": [], "evidence_ids": []},
                        {"kind": "fact", "text": "The student reports a 3.7 GPA and Chemistry major.",
                         "evidence_ids": []},
                    ]}})
            if index == 1:
                from app.llm.base import AssistantToolMessage, ToolResultMessage

                assert context["finish_required"]
                assistant = next(m for m in request.messages if isinstance(m, AssistantToolMessage))
                result = next(m for m in request.messages if isinstance(m, ToolResultMessage))
                assert len(assistant.tool_calls) == 1
                assert assistant.tool_calls[0].name == "advisor_finish"
                assert assistant.tool_calls[0].arguments["decision"]["blocks"][0]["kind"] == "judgment"
                assert result.tool_call_id == assistant.tool_calls[0].id
                rejection = json.loads(result.content)
                assert rejection["status"] == "conflict"
                assert any("judgment requires supporting evidence" in message
                           for message in rejection["limitations"])
                assert any("fact with no supporting evidence" in message
                           for message in rejection["limitations"])
            return GenerationResult(text=json.dumps({"decision": {
                "outcome": "insufficient_evidence", "completeness": "partial",
                "blocks": [
                    {"kind": "assumption", "text": "Student-reported: GPA 3.7 and Chemistry major."},
                    {"kind": "limitation", "text": "No captured Emory MD admissions source here establishes whether this profile guarantees admission."},
                    {"kind": "question", "text": "Which admissions cycle are you considering?"},
                ],
                "unresolved_questions": ["No captured MD admissions source establishes a guarantee."]
            }}), finish_reason="stop", usage=provider.result.usage)
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
        if scenario == "which_majors":
            if index == 0:
                return call("read_courses", {"course_codes": ["MATH 111"]})
            assert {tool.name for tool in request.tools} == {"advisor_finish"}
            ids = [e["evidence_id"] for e in context["exact_evidence_untrusted"]]
            return call("finish", {"decision": {"outcome": "information", "completeness": "complete",
                "blocks": [{"kind": "fact", "text": "Two programs name MATH 111.",
                            "evidence_ids": ids}]}})
        if scenario == "program_audit":
            from app.llm.base import ToolResultMessage

            def tool_result():
                return json.loads(next(message for message in reversed(request.messages)
                                       if isinstance(message, ToolResultMessage)).content)
            handles = [h["handle"] for h in context["issued_handles"] if h["kind"] == "program"]
            audit = {"handle": handles[0] if handles else "", "completed_codes": ["CS 170"]}
            ids = [e["evidence_id"] for e in context["exact_evidence_untrusted"]]
            if index == 0:
                return call("list_programs", {})
            if index == 1:
                return call("audit_program", audit)
            if index == 2:
                observed["unread"] = tool_result()
                return call("read_program", {"handle": handles[0]})
            if index == 3:
                return call("update_research", {"expected_revision": 0, "state": {
                    "revision": 0, "decision_question": "What is left?",
                    "active_evidence_ids": ids}})
            if index == 4:
                return call("audit_program", audit)
            if index == 5:
                observed["audit"] = tool_result()
                return call("finish", {"decision": {"outcome": "information", "completeness": "partial",
                    "blocks": [{"kind": "fact", "text": "CS 170 satisfies that requirement.",
                                "evidence_ids": ids}]}})
            observed["overclaim"] = tool_result()
            return call("finish", {"decision": {"outcome": "information", "completeness": "partial",
                "blocks": [{"kind": "fact", "text": "The program names CS 170.",
                            "evidence_ids": ids},
                           {"kind": "limitation", "text": "This is not an official degree audit."}]}})
        if scenario == "course_suggestions":
            from app.llm.base import ToolResultMessage

            if index == 0:
                return call("suggest_courses", {"goal": "macroeconomics"})
            if index == 1:
                observed["suggested"] = json.loads(next(
                    message for message in reversed(request.messages)
                    if isinstance(message, ToolResultMessage)).content)
                code = observed["suggested"]["data"]["suggestions"][0]["courses"][0]["code"]
                return call("read_courses", {"course_codes": [code]})
            ids = [e["evidence_id"] for e in context["exact_evidence_untrusted"]]
            if index == 2:
                return call("update_research", {"expected_revision": 0, "state": {
                    "revision": 0, "decision_question": "What fits?",
                    "active_evidence_ids": ids}})
            return call("finish", {"decision": {"outcome": "information", "completeness": "complete",
                "blocks": [{"kind": "fact", "text": "This course is listed.", "evidence_ids": ids}]}})
        preparation_scenarios = {
            "preparation", "preparation_or_selected", "preparation_or_completed",
            "preparation_or_completed_long_unselected_branch",
            "preparation_missing_or_choice", "preparation_missing_prerequisite",
        }
        if index == 0:
            checks = {
                "preparation": (["ECON 201"], ["ECON 101"]),
                "preparation_or_selected": (["BIOL 101", "BIOL 102"], []),
                "preparation_or_completed": (["BIOL 102"], ["BIOL 101"]),
                "preparation_or_completed_long_unselected_branch": (["BIOL 102"], ["BIOL 101"]),
                "preparation_missing_or_choice": (["BIOL 102"], []),
                "preparation_missing_prerequisite": (["ECON 201"], []),
            }
            if scenario in preparation_scenarios:
                selected, completed = checks[scenario]
                return call("check_preparation", {
                    "selected_codes": selected, "completed_codes": completed,
                })
            return call("read_courses", {"course_codes": ["ECON 201"]})
        if scenario in preparation_scenarios:
            check = context["executed_preparation_checks"][0]
            assert {"stages", "blockers", "complete", "unexplored_codes"} <= set(check)
            observed["context_preparation_check"] = check
        ids = [e["evidence_id"] for e in context["exact_evidence_untrusted"]]
        # The block must name the courses its cited evidence names: the identity
        # gate rejects a block that names a course the evidence does not
        # contain, and the evidence differs per scenario (ECON 201 for the
        # economics scenarios, the BIOL alternatives for the others).
        check = context["executed_preparation_checks"][0] if scenario in preparation_scenarios else None
        if check is not None and check["completed_codes"]:
            block_text = (f"{check['selected_codes'][0]} lists "
                          f"{check['completed_codes'][0]} as preparation.")
        elif check is not None:
            block_text = f"{check['selected_codes'][0]} is an unmet preparation requirement."
        else:
            block_text = "ECON 201 lists ECON 101 as preparation."
        decision = {"outcome": "information", "completeness": "complete",
                    "blocks": [{"kind": "fact", "text": block_text, "evidence_ids": ids}]}
        if scenario in preparation_scenarios:
            check = context["executed_preparation_checks"][0]
            decision.update(preparation_check_ids=[check["check_id"]], preparation_claims=[{
                "check_id": check["check_id"], "selected_codes": check["selected_codes"],
                "completed_codes": check["completed_codes"], "conclusion": check["conclusion"]}])
        return call("finish", {"decision": decision})
    provider = _research_provider(monkeypatch, script)
    sources = [] if scenario in {"clarification", "saved_clarification", "finish_feedback_roundtrip"} else None
    conversation_args = {}
    if scenario == "saved_clarification":
        from app.conversations.store import ConversationStore

        conversations = ConversationStore(db, "development")
        conversation_id = UUID(conversations.create("Research question")["conversationId"])
        request_id = uuid4()
        conversations.begin(conversation_id, request_id, 0, {"query": "Help choose a path"})
        conversation_args = {"conversation_id": conversation_id, "conversation_request_id": request_id}
    question = ("I have a 3.7 GPA and a Chemistry major. Does that guarantee admission to Emory MD?"
                if scenario == "finish_feedback_roundtrip" else
                "Use the deterministic sequence tool to plan ECON 201 alone. I have completed no courses."
                if scenario == "preparation_after_state_rejection" else
                "What is ECON 201?" if scenario in {"truncated_research_repair", "truncated_research_exhausted",
                                                     "rate_limited_research_retry"} else
                "Describe ECON 101 Principles of Macroeconomics." if scenario == "factual_evidence_retention"
                # A preparation check counts only courses the student stated.
                else "Help choose a path. I have completed ECON 101."
                if scenario in {"preparation", "program_read_before_preparation"}
                else "Help choose a path. I have completed BIOL 101."
                if scenario in {"preparation_or_completed",
                                "preparation_or_completed_long_unselected_branch"}
                else "Help choose a path")
    if scenario == "finish_rejects_unsupported_references":
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            query_response(QueryRequest(query=question, collectionId=collection_name(db, collection),
                sourceIds=sources), db, settings, lenient_config(), FakeEmbedder(8), provider, **conversation_args)
        assert len(provider.requests) >= 3
        last_context = json.loads(provider.requests[-1].messages[1].content)
        assert "e999" not in {e["evidence_id"] for e in last_context["exact_evidence_untrusted"]}
        return
    if scenario == "which_majors":
        question = "which majors need math 111"
    response = query_response(QueryRequest(query=question, collectionId=collection_name(db, collection),
        sourceIds=sources), db, settings, research_config, FakeEmbedder(8), provider, **conversation_args)
    assert response.type == "answer"
    assert response.research.completeness == (
        "partial" if scenario in {"program_paging_budget", "finish_feedback_roundtrip", "program_audit",
                                  "truncated_research_repair", "truncated_research_exhausted", "rate_limited_research_retry"} else "complete")
    if scenario in {"program_paging", "program_paging_budget"}:
        read, total = observed["coverage"]
        assert (read == total) == (scenario == "program_paging")
        if scenario == "program_paging_budget":
            assert f"{read} of {total} items read" not in response.answer
            assert any("did not check every requirement" in question
                       for question in response.research.unresolvedQuestions)
    if scenario == "preparation_after_state_rejection":
        assert len(provider.requests) <= settings.research_max_calls
        assert observed["malformed_state_rejected"]
        recovery_steps = ResearchStore(db, "development").steps(UUID(response.research.runId))
        assert len(recovery_steps) <= settings.research_max_tool_calls + 1
        persisted_check = next(step["details"] for step in recovery_steps
                               if step["action_kind"] == "preparation_check")
        assert persisted_check == observed["context_preparation_check"]
        assert observed["context_preparation_check"]["completed_codes"] == []
    run = ResearchStore(db, "development").get(UUID(response.research.runId))
    if scenario == "program_read_before_preparation":
        assert observed["program_read_preceded_preparation"]
        assert observed["actions"].index("read_program") < observed["actions"].index("check_preparation")
        assert observed["actions"][-1] == "finish"
        assert response.research.outcome == "recommendation"
        assert response.research.blocks[0].candidateIds == ["registered_path"]
    if scenario in {
        "preparation", "preparation_or_selected", "preparation_or_completed",
        "preparation_or_completed_long_unselected_branch",
        "preparation_missing_or_choice", "preparation_missing_prerequisite",
    }:
        check = next(step["details"] for step in ResearchStore(db, "development").steps(run.run_id)
                     if step["action_kind"] == "preparation_check")
        if scenario in {"preparation", "preparation_or_selected", "preparation_or_completed",
                        "preparation_or_completed_long_unselected_branch"}:
            assert check["conclusion"] == "dependency_order", check
            assert check["blockers"] == []
            context_check = observed["context_preparation_check"]
            assert context_check["stages"] == check["stages"]
            assert context_check["blockers"] == [] and context_check["complete"]
            if scenario == "preparation_or_selected":
                assert check["stages"] == [["BIOL 101"], ["BIOL 102"]]
            if scenario == "preparation_or_completed":
                assert check["stages"] == [["BIOL 102"]]
            if scenario == "preparation_or_completed_long_unselected_branch":
                assert check["unexplored_codes"]
                assert context_check["unexplored_codes"]
        elif scenario == "preparation_missing_or_choice":
            assert check["conclusion"] == "blocked" and not check["complete"]
            assert any(blocker["code"] == "BIOL 102" and blocker["reason"] == "alternative_required"
                       for blocker in check["blockers"])
        else:
            assert check["conclusion"] == "blocked" and not check["complete"]
            assert any(blocker["code"] == "ECON 201" and blocker["reason"] == "missing_prerequisite"
                       for blocker in check["blockers"])
    if scenario == "document_state_repair":
        assert run.state.revision == 1 and not run.state.candidates
        assert run.state.active_evidence_ids
        assert [block.kind for block in response.research.blocks] == ["judgment"]
    if scenario == "discovery_read_cadence":
        steps = ResearchStore(db, "development").steps(run.run_id)
        assert any(step["action_kind"] == "read_document" for step in steps)
        assert not any(step["action_kind"] == "search"
                       and step["details"]["query"] == "letter grade policy" for step in steps)
        assert any(step["action_kind"] == "rejected_finish" for step in steps)
    if scenario == "cached_read_priority":
        evidence = ResearchStore(db, "development").evidence(run.run_id)
        assert {record.evidence_id for record in evidence} == {observed["first"], observed["second"]}
        steps = ResearchStore(db, "development").steps(run.run_id)
        assert sum(step["action_kind"] == "read_courses" for step in steps) == 2
    if scenario == "saved_clarification":
        assert run.status == "running"
        conversations.finish(conversation_id, request_id, response.model_dump(mode="json"))
        run = ResearchStore(db, "development").get(run.run_id)
        evidence = conversations.turns(conversation_id)["turns"][-1]["evidence"]
        assert evidence["research"]["runId"] == response.research.runId
        assert evidence["research"]["outcome"] == "clarification"
    assert run.status == ("clarification" if scenario in {"clarification", "saved_clarification"}
                          else "partial" if scenario == "program_audit"
                          else "insufficient_evidence" if scenario in {"finish_feedback_roundtrip",
                                                                       "truncated_research_repair",
                                                                       "truncated_research_exhausted",
                                                                       "rate_limited_research_retry"}
                          else "partial" if scenario in {"program_paging_budget"}
                          else "completed")
    if scenario in {"clarification", "saved_clarification", "finish_feedback_roundtrip",
                    "truncated_research_repair", "truncated_research_exhausted",
                    "rate_limited_research_retry"}:
        assert response.citations == [] and response.trace.contextBlock == []
        if scenario == "finish_feedback_roundtrip":
            assert response.research.outcome == "insufficient_evidence"
            assert {block.kind for block in response.research.blocks} >= {"assumption", "limitation"}
            assert not any(block.kind == "fact" for block in response.research.blocks)
        if scenario in {"truncated_research_repair", "truncated_research_exhausted",
                        "rate_limited_research_retry"}:
            assert len(provider.requests) == {"truncated_research_repair": 2,
                                              "truncated_research_exhausted": 4}.get(scenario, 1)
            assert response.research.outcome == "insufficient_evidence"
            steps = ResearchStore(db, "development").steps(run.run_id)
            assert not any(step["action_kind"] == "read_courses" for step in steps)
            assert ResearchStore(db, "development").evidence(run.run_id) == []
    else:
        assert response.citations and response.trace.contextBlock
        assert "[1]" in response.answer
        if scenario == "program_audit":
            assert observed["unread"]["status"] == "conflict"
            result = observed["audit"]
            assert result["status"] == "incomplete" and result["data"]["reviewed_rules"] is False
            lines = {line["text"]: line for line in result["data"]["lines"]}
            assert lines["CS 170"]["state"] == "all_named_reported"
            assert lines["CS 170"]["evidence_ids"]
            assert "not that a requirement is met" in result["limitations"][0]
            assert "cannot be said to meet" in observed["overclaim"]["limitations"][0]
        if scenario == "course_suggestions":
            result = observed["suggested"]
            assert result["data"]["discovery_only"] is True
            assert "not requirements" in result["limitations"][0]
        if scenario == "program_comparison":
            assert response.claims == []  # Subjective suitability is not an entailment claim.
    status = OperationBudgetController().get(db, run.operation_id)
    # The rate-limited attempt never reached the fake but is still a billed call.
    assert status.call_count == len(provider.requests) + (scenario == "rate_limited_research_retry")
    assert all(sum(len(e["exact_text"]) for e in json.loads(r.messages[1].content)[
        "exact_evidence_untrusted"]) <= settings.research_max_prompt_bytes for r in provider.requests)


@pytest.mark.parametrize("correction", ["inactive_evidence", "artifact_from_words"])
def test_research_allows_a_bounded_finish_correction(
        db, collection, tmp_path, monkeypatch, correction):
    from app.api.query_service import query_response
    from app.api.schemas import QueryRequest
    from app.config import load_settings
    from app.llm.base import ToolCall
    from tests.fakes import FakeEmbedder, FakeProvider
    from tests.helpers import MODEL, collection_name, lenient_config

    _corpus(db, collection, tmp_path)
    settings = replace(load_settings(), llm_provider="openai", llm_model_id="fake",
        tools_enabled=True, routing_enabled=False,
        embedding_model_id=MODEL, db_pool_max_size=0)
    if correction != "inactive_evidence":
        provider = FakeProvider("The program awards a BS.")
        generate = provider.generate
        finishes = []

        def finish_as_json(request):
            result = generate(request)
            call, = result.tool_calls
            if call.name != "advisor_finish":
                return result
            context = json.loads(request.messages[1].content)
            finishes.append(context["instruction"])
            decision = dict(call.arguments["decision"])
            return replace(result, text=json.dumps(decision), tool_calls=(), finish_reason="stop")

        monkeypatch.setattr(provider, "generate", finish_as_json)
        response = query_response(QueryRequest(query="Plan my Computer Science BS from here.",
            collectionId=collection_name(db, collection)), db, settings, lenient_config(),
            FakeEmbedder(8), provider)
        # A finish without the requested artifact is not sent back for another
        # round: the student's own words become the request in code.
        assert len(finishes) == 1
        steps = ResearchStore(db, "development").steps(UUID(response.research.runId))
        assert not any(step["action_kind"] == "rejected_finish" for step in steps)
        assert response.research.artifact.kind == "document"
        assert "Computer Science BS" in response.research.artifact.brief
        assert response.answer
        return

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

    from app.config import load_settings
    from app.generation.research_tools import PreparationInput, ResearchSession
    from tests.fakes import FakeEmbedder
    from tests.helpers import lenient_config

    scope, store = _scope(db, collection), ResearchStore(db, "development")
    run, _ = store.start(scope, run_id=uuid4(), prompt_version="test", config_version="6")
    session = ResearchSession(db, scope, store, run, settings=load_settings(),
                              config=lenient_config(), embedder=FakeEmbedder(8))
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="completed_codes"):
        PreparationInput(selected_codes=["ECON 201"])
    checked = session.preparation(PreparationInput(selected_codes=["ECON 201"], completed_codes=[]), None)
    check = checked.data
    assert check["check_id"] in session.checks and check["evidence_ids"]
    session.unavailable_preparation_check_ids.add(check["check_id"])
    session.supplied = set(check["evidence_ids"])
    hidden_claim = {
        "outcome": "information", "completeness": "complete",
        "blocks": [{"kind": "fact", "text": "A sourced course fact.",
                    "evidence_ids": check["evidence_ids"]}],
        "preparation_check_ids": [check["check_id"]],
        "preparation_claims": [{
            "check_id": check["check_id"], "selected_codes": check["selected_codes"],
            "completed_codes": check["completed_codes"], "conclusion": check["conclusion"]}],
    }
    rejected = session.registry().invoke("advisor_finish", {"decision": hidden_claim},
                                         session.tool_scope(lambda: None))
    assert rejected.status == "conflict"
    assert any("was not executed" in issue for issue in rejected.limitations)
    assert check["check_id"] in session.checks
    assert set(check["evidence_ids"]) <= session.supplied


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
    # Broad discovery still offers a document and course before paging through
    # every program matched by a common requirement mention.
    broad = session.search(SearchInput(query="computer science major"), None).data["leads"]
    assert {lead["kind"] for lead in broad[:3]} == {"program", "document", "course"}
    # A course's exact title outranks code order among course leads, and a title
    # inside a longer one is the weaker match.
    db.execute("UPDATE courses SET title = 'Macroeconomics' "
               "WHERE collection_id = %s AND code = 'ECON 101'", (scope.collection_id,))
    exact = session.search(SearchInput(query="intermediate macroeconomics", kind="course"),
                           None).data["leads"]
    assert [lead["code"] for lead in exact][:2] == ["ECON 201", "ECON 101"]
    # So does a program named in the student's words, the longest name first.
    session.question = "requirements for the computer science minor in anthropology"
    named = session.search(SearchInput(query="requirements", kind="program"), None).data["leads"]
    assert named[0]["title"].startswith("Computer Science")


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
        tools_enabled=True, routing_enabled=False,
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
        assert response.research.completeness == "complete"  # The model's call; a forced finish is not a gap.
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


@pytest.mark.parametrize("question,unknown", [
    ("is econ 999 required", {"ECON 999": []}),
    ("is econ 101 required", {}),
    ("what is a major", {}),
    # The sweep's ENG_OX 330W miss: the Atlanta code has no current record,
    # but the Atlas pairs it with a current Oxford course, so it is real.
    ("i took ECON_OX 998 at oxford, is that the same as ECON 998?", {}),
    # A word before a number is not a course unless it is a subject, or the
    # student wrote it as a code.
    ("can i declare econ with 88 credit hours", {}),
    ("is the top 10 list out", {}),
    ("I'm a junior WITH 88 credit hours", {}),
    ("we meet in ROOM 204", {}),
    ("is XYZ 101 a real class", {"XYZ 101": []}),
    # A retired master row is a real course, not one that does not exist.
    ("can i still take math 111", {}),
    # Oxford kept the QTM name; the DATASCI spelling points to its course.
    ("is DATASCI_OX 100 offered at oxford", {"DATASCI_OX 100": ["QTM_OX 100"]}),
])
def test_a_named_course_that_does_not_exist_is_flagged(db, collection, tmp_path, question, unknown):
    from app.config import load_settings
    from app.generation.research_tools import ResearchSession
    from tests.fakes import FakeEmbedder
    from tests.helpers import lenient_config

    _corpus_ids, scope, store, run = _run(db, collection, tmp_path)
    session = ResearchSession(db, scope, store, run, settings=load_settings(),
                              config=lenient_config(), embedder=FakeEmbedder(8))
    db.execute("INSERT INTO course_campus_equivalents (collection_id, oxford_code, atlanta_code,"
               " basis) VALUES (%s, 'ECON_OX 998', 'ECON 998', 'course_record'),"
               " (%s, 'QTM_OX 100', 'DATASCI 100', 'course_record')", (collection, collection))
    db.execute("UPDATE courses SET lifecycle = 'retired' WHERE collection_id = %s"
               " AND code = 'MATH 111'", (collection,))
    session.question = question
    assert session.unknown_question_codes() == unknown


@pytest.mark.parametrize("question,ready", [
    ("what can i take after econ 101", True),
    ("what is econ 101", False),
])
def test_the_prerequisite_graph_can_hold_the_whole_answer(db, collection, tmp_path, question, ready):
    from app.advising.research_types import FinalDecision
    from app.config import load_settings
    from app.generation.research_final import DecisionRejected
    from app.generation.research_tools import CoursesInput, FinishInput, ResearchSession
    from tests.fakes import FakeEmbedder
    from tests.helpers import lenient_config

    _corpus_ids, scope, store, run = _run(db, collection, tmp_path)
    session = ResearchSession(db, scope, store, run, settings=load_settings(),
                              config=lenient_config(), embedder=FakeEmbedder(8))
    session.question = question
    session.courses(CoursesInput(course_codes=["ECON 101"]), None)
    assert bool(session.answer_in_hand) is ready
    session.must_name = {"ECON 201"}
    decision = FinalDecision(outcome="information", completeness="complete", blocks=(
        {"kind": "limitation", "text": "ECON 301 follows it."},))
    with pytest.raises(DecisionRejected, match="ECON 201"):
        session.finish(FinishInput.model_construct(decision=decision), None)
    session.must_name = set()
    session.question = "is PHYS 413 a good class"
    with pytest.raises(DecisionRejected, match="PHYS 413"):
        session.finish(FinishInput.model_construct(decision=decision), None)
    session.question = "what classes do i need for the economics major"
    session.must_name_degrees = {"BA"}
    with pytest.raises(DecisionRejected, match="also exists as a BA"):
        session.finish(FinishInput.model_construct(decision=decision), None)


@pytest.mark.parametrize("question,hits", [
    ("how many credits do i need to graduate", True),
    ("how many credit hours to graduate from emory college", True),
    ("credits needed to graduate", True),
    ("can a graduate course count for credit", False),
    ("how many credits is cs 170", False),
])
def test_a_known_source_conflict_is_found_by_its_question(question, hits):
    from app.generation.research_tools import source_conflicts

    assert bool(source_conflicts(question)) is hits


@pytest.mark.parametrize(("title", "kind", "degree", "others"), [
    ("Computer Science (BS)", "major", "BS", {"COMPUTER SCIENCE (BA)"}),
    ("Computer Science (Minor)", "minor", None, set()),
    ("Mathematics and Computer Science (BS)", "major", "BS", set()),
])
def test_a_degree_finds_the_same_subject_under_its_other_degree(title, kind, degree, others):
    from app.generation.research_tools import other_degrees

    def pin(title, kind, degree):
        return SimpleNamespace(program_id=title, title=title, kind=kind, degree=degree)

    catalog = [pin("COMPUTER SCIENCE (BA)", "major", "BA"), pin("Computer Science (BS)", "major", "BS"),
               pin("Computer Science (Minor)", "minor", None),
               pin("Mathematics and Computer Science (BS)", "major", "BS")]
    assert {p.title for p in other_degrees(pin(title, kind, degree), catalog)} == others


def test_a_program_question_gets_citable_requirement_lines(db, collection, tmp_path):
    from app.config import load_settings
    from app.generation.research_tools import CoursesInput, ResearchSession
    from tests.fakes import FakeEmbedder
    from tests.helpers import lenient_config

    _corpus_ids, scope, store, run = _run(db, collection, tmp_path)
    session = ResearchSession(db, scope, store, run, settings=load_settings(),
                              config=lenient_config(), embedder=FakeEmbedder(8))
    session.question = "which majors need math 111"
    read = session.courses(CoursesInput(course_codes=["MATH 111"]), None)
    lines = read.data["courses"][0]["program_lines"]
    assert {line["program"] for line in lines} == {"Computer Science (BS)",
                                                   "Computer Science (Minor)"}
    held = {record.evidence_id for record in store.evidence(run.run_id)}
    assert all(line["evidence_ids"] and set(line["evidence_ids"]) <= held for line in lines)
