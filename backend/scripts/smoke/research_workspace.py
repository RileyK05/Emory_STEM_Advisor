"""Capped real-provider workspace checks in a disposable database, using fixture data only.

Never uses DATABASE_URL or the development corpus. Prints diagnostics; writes no
report. The database, raw fixture files and audit rows are removed after a clean
Postgres shutdown. No real embedding model is loaded.
"""

import argparse
import hashlib
import json
import os
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import pgserver
from fastapi import HTTPException

from app.advising.research_repository import build_scope, read_document
from app.advising.research_store import ResearchStore
from app.api.query_service import query_response
from app.api.schemas import QueryRequest
from app.config import load_settings
from app.conversations.store import ConversationStore, research_history_prompt
from app.db import queries
from app.db.connection import close_pools, connect
from app.db.migrate import apply_migrations
from app.generation.research_tools import ResearchSession, StateInput
from app.ingest.pipeline import ingest_file
from app.llm import get_provider
from app.programs.catalog_pdf import CatalogSection
from app.programs.importer import import_programs
from app.programs.requirements import parse_program, to_record
from app.retrieval.config import load_retrieval_config
from scripts.scrape_programs import render_section
from scripts.smoke.live_advising import RecordingProvider
from tests.fakes import FakeEmbedder, whitespace_tokens


def _seed(conn, settings, config, embedder, *, report, source_id=None):
    conversations = ConversationStore(conn, "development")
    conversation = UUID(conversations.create("Disposable workspace probe")["conversationId"])
    request = uuid4()
    conversations.begin(conversation, request, 0, {"query": report,
        "effective": {"collectionId": settings.default_collection, "sourceIds": None}})
    collection = conn.execute(queries.get("get_collection_by_name"), {
        "name": settings.default_collection}).fetchone()["collection_id"]
    scope = build_scope(conn, collection_id=collection, workspace_key="development", source_ids=None)
    store = ResearchStore(conn, "development")
    run, _ = store.start(scope, run_id=uuid4(), prompt_version="fixture", config_version=str(config.version),
                         conversation_id=conversation, request_id=request)
    session = ResearchSession(conn, scope, store, run, settings=settings, config=config, embedder=embedder)
    ids = session.acquire(list(read_document(conn, scope, source_id).chunks), action="read_document") if source_id else []
    note = {"id": "next", "text": "Reopen the earlier catalog and verify restrictions.",
            "basis": "evidence_summary", "evidence_ids": ids} if source_id else {
                "id": "next", "text": "Hypothesis only: perhaps large lectures fit; check the student's original report.",
                "basis": "hypothesis"}
    session.update(StateInput.model_validate({"state": {"revision": 0,
        "decision_question": "Continue the student's investigation", "active_evidence_ids": ids,
        "plan": [{"id": "verify", "task": "Reopen prior context and update the notebook", "status": "pending"}],
        "notes": [note]}}), None)
    conversations.finish(conversation, request, {"type": "answer", "answer": "Fixture checkpoint saved.",
        "research": {"runId": str(run.run_id), "outcome": "information", "completeness": "partial"}})
    return conversation, scope


def _probe(conn, settings, config, embedder, provider, conversation, *, case, question, sources=None):
    sources = None if sources is None else [str(source) for source in sources]
    conversations = ConversationStore(conn, "development")
    rows = conversations.context(conversation, max_bytes=12000, archive=True)
    sequence = conversations.get(conversation)["last_sequence"]
    request = uuid4()
    conversations.begin(conversation, request, sequence, {"query": question,
        "effective": {"collectionId": settings.default_collection, "sourceIds": sources}})
    provider.case_id = case
    before = len(provider.calls)
    started = time.perf_counter()
    try:
        response = query_response(QueryRequest(query=question, collectionId=settings.default_collection,
            sourceIds=sources), conn, settings, config, embedder, provider,
            history_context=research_history_prompt(rows, max_bytes=6000),
            conversation_id=conversation, conversation_request_id=request)
        conversations.finish(conversation, request, response.model_dump(mode="json"))
        tools = [call["name"] for row in provider.calls[before:] for call in row.get("tool_calls", [])]
        run = ResearchStore(conn, "development").get(UUID(response.research.runId))
        print(json.dumps({"case": case, "answer": response.answer,
            "citations": [c.chunkId for c in response.citations], "outcome": response.research.outcome,
            "calls": len(provider.calls) - before, "tools": tools,
            "seconds": round(time.perf_counter() - started, 3),
            "completeness": response.research.completeness,
            "notebook_saved": run.state is not None}), flush=True)
        return response, tools, run
    except HTTPException as exc:
        conn.rollback()
        conversations.finish(conversation, request, {"type": "failed", "reason": type(exc).__name__}, state="failed")
        print(json.dumps({"case": case,
            "error_type": "ProbeBudgetExhausted" if provider.exhausted else type(exc).__name__,
            "detail": "Live probe budget exhausted" if provider.exhausted else str(exc.detail)[:500]}), flush=True)
        return None, [], None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-calls", type=int, default=12)
    parser.add_argument("--token-budget", type=int, default=400000)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--case", action="append", choices=["recover-older-evidence",
        "recover-long-student-report", "empty-source-fence", "read-policy-tail",
        "read-all-requirements"])
    args = parser.parse_args()
    settings = load_settings()
    if not 1 <= args.max_calls <= 30 or not 1000 <= args.token_budget <= 1000000 or not 0 < args.timeout <= 180:
        parser.error("probe limits are outside the supported bounds")
    if settings.llm_provider == "hf-local" or not settings.llm_api_key:
        parser.error("configure a real tools-capable provider and key locally")
    provider = RecordingProvider(get_provider(settings), max_calls=args.max_calls, token_budget=args.token_budget)
    def progress():
        row = provider.calls[-1]
        context = json.loads(row["messages"][1]["content"])
        page = context.get("conversation_page_untrusted") or {}
        print(json.dumps({"case": provider.case_id, "calls": len(provider.calls),
            "tools": [{"name": c["name"], "query": c["arguments"].get("query"),
                       "cursor": c["arguments"].get("cursor")} for c in row.get("tool_calls", [])],
            "visible_turns": [{"sequence": t["sequence"], "offset": t["text_offset"],
                "characters": len(t["text"]), "complete": t["complete"]}
                for t in page.get("data", {}).get("turns_untrusted", [])],
            "next_cursor": page.get("next_cursor"), "finish_required": context["finish_required"]}), flush=True)
    provider.on_update = progress
    selected = args.case or ["recover-older-evidence", "recover-long-student-report", "empty-source-fence"]
    root = Path(__file__).resolve().parents[3] / "data" / "test-runs"
    root.mkdir(parents=True, exist_ok=True)
    previous_data_dir = os.environ.get("DATA_DIR")
    passed = []
    with tempfile.TemporaryDirectory(prefix="workspace-smoke-", dir=root) as directory:
        scratch = Path(directory).resolve()
        if scratch.parent != root.resolve():
            raise RuntimeError("disposable probe escaped its scratch root")
        os.environ["DATA_DIR"] = str(scratch / "data")
        server = pgserver.get_server(scratch / "pg", cleanup_mode="stop")
        try:
            settings = replace(settings, database_url=server.get_uri(), data_dir=scratch / "data",
                db_pool_max_size=0, default_collection="workspace-fixture", routing_enabled=False,
                tools_enabled=True, advising_research_enabled=True, embedding_model_id="workspace-fixture",
                llm_timeout_seconds=args.timeout, llm_max_output_tokens=4096,
                research_max_calls=min(10, args.max_calls), research_operation_timeout_seconds=300)
            config = load_retrieval_config()
            config = replace(config, seams=replace(config.seams, embed=replace(config.seams.embed, enabled=False)))
            embedder = FakeEmbedder(8)
            with connect(settings.database_url) as conn:
                apply_migrations(conn)
                collection = uuid4()
                conn.execute(queries.get("insert_collection"), {"collection_id": collection, "name": settings.default_collection})
                conn.commit()
                sources = []
                for index in range(15):
                    path = scratch / f"{'course-catalog' if index == 0 else 'seminar-' + str(index)}.txt"
                    text = ("Fixture catalog for testing only. CS 224 Data Structures. Prerequisite: CS 171. "
                            "This captured catalog does not report seat availability.") if index == 0 else f"Fixture seminar {index}: one credit."
                    path.write_text(text, encoding="utf-8")
                    sources.append(ingest_file(conn, collection_id=collection, path=path,
                        content_type="text/plain", count_tokens=whitespace_tokens, embedder=embedder,
                        embed_model=settings.embedding_model_id, config=config))
                conversation, scope = _seed(conn, settings, config, embedder,
                    report="Investigate CS 224's prerequisite and seat availability.", source_id=sources[0])
                evidence_conversation = conversation
                noise = uuid4()
                conversations = ConversationStore(conn, "development")
                conversations.begin(conversation, noise, 2, {"query": "List fixture seminars"})
                bodies = [{"chunkId": str(c.chunk_id), "sourceId": str(c.source_id), "sourceName": "Fixture seminar",
                           "locatorLabel": c.locator_label, "text": c.text}
                          for source in sources[1:] for c in read_document(conn, scope, source).chunks]
                conversations.finish(conversation, noise, {"type": "answer", "answer": "Fixture seminars listed.",
                    "citations": bodies, "trace": {"contextBlock": bodies}})
                if "recover-older-evidence" in selected:
                    response, tools, _run = _probe(conn, settings, config, embedder, provider, conversation,
                        case="recover-older-evidence", question="In this fixture corpus, what prerequisite did CS 224 list, and can its captured catalog verify current open seats? Reopen the earlier evidence and update your notebook.")
                    expected = str(read_document(conn, scope, sources[0]).chunks[0].chunk_id)
                    passed.append(bool(response and expected in {c.chunkId for c in response.citations}
                                       and "CS 171" in response.answer))
                report = "Background on my interests. " * 600 + "My preferred study format is small seminar classes."
                conversation, _scope = _seed(conn, settings, config, embedder, report=report)
                if "recover-long-student-report" in selected:
                    response, tools, run = _probe(conn, settings, config, embedder, provider, conversation,
                        case="recover-long-student-report", question="What study format did I report preferring? Read my original long background message through its ending and update the notebook. Do not substitute its earlier hypothesis for my own report.")
                    passed.append(bool(response and "seminar" in response.answer.casefold()
                        and not response.citations and tools.count("advisor_read_conversation") >= 3
                        and run.state and any(n.basis == "user_report" and "seminar" in n.text.casefold() for n in run.state.notes)))
                if "empty-source-fence" in selected:
                    response, _tools, _run = _probe(conn, settings, config, embedder, provider, evidence_conversation,
                        case="empty-source-fence", sources=[], question="With no sources selected, can you verify the fixture catalog prerequisite for CS 224? Explain the scope limit without using old source facts as current evidence.")
                    passed.append(bool(response and not response.citations
                        and not any(b.kind == "fact" for b in response.research.blocks)))
                if "read-policy-tail" in selected:
                    path = scratch / "synthetic-grading-policy.md"
                    filler = "This section describes library-room scheduling only and establishes no grading or degree rule. "
                    path.write_text("# Synthetic grading policy for fixture testing only\n\n"
                        + "\n\n".join(f"## Administrative section {n}\n\n{filler * 4}" for n in range(1, 40))
                        + "\n\n## Final operative grading rules\n\n"
                        "The S/U maximum is 17 credit hours. The grading-basis deadline is the 41st class day. "
                        "The ordinary 4+1 double-counting cap is 5 credits. Exactly one extra S/U credit may count "
                        "only with written board approval; it is never automatic.\n", encoding="utf-8")
                    policy = ingest_file(conn, collection_id=collection, path=path,
                        content_type="text/markdown", count_tokens=whitespace_tokens,
                        embedder=embedder, embed_model=settings.embedding_model_id, config=config)
                    conversation, _ = _seed(conn, settings, config, embedder,
                        report="Investigate the synthetic grading policy, including its final operative section.")
                    response, tools, _ = _probe(conn, settings, config, embedder, provider, conversation,
                        case="read-policy-tail", sources=[policy], question=
                        "In this SYNTHETIC fixture only, read the grading-policy document through its final operative "
                        "section. State the S/U maximum, grading-basis deadline, ordinary 4+1 cap, and final "
                        "approval exception. Do not substitute real Emory policy or stop at an unread-section gap.")
                    answer = response.answer.casefold() if response else ""
                    passed.append(bool(response and all(term in answer for term in
                        ("17", "41", "5", "written", "board"))))
                if "read-all-requirements" in selected:
                    title = "Fixture Systems (Minor)"
                    body = (title + "\nDEGREE AWARDED N/A\nHOURS TO COMPLETE 38\n"
                        "COURSES REQUIRED 38\nDEPARTMENT CONTACT Computer Science\nRequirements\n"
                        + "\n".join(f"- CS {n}: Synthetic requirement item {n - 900}; one credit."
                                    for n in range(901, 939)))
                    path = scratch / "fixture-systems-minor.md"
                    path.write_text(render_section(CatalogSection(title, body, 1, 2),
                        catalog="Synthetic Fixture Catalog", catalog_id="SMOKE-ONLY"),
                        encoding="utf-8", newline="\n")
                    origin = "smoke:fixture:systems-minor"
                    source = ingest_file(conn, collection_id=collection, path=path,
                        content_type="text/markdown", count_tokens=whitespace_tokens,
                        embedder=embedder, embed_model=settings.embedding_model_id,
                        config=config, origin_key=origin)
                    record = to_record(parse_program(title, body), catalog="Synthetic Fixture Catalog",
                        source_url="https://example.test/fixture", first_page=1, last_page=2)
                    record["source"] = {"origin_key": origin,
                        "content_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "catalog_id": "SMOKE-ONLY"}
                    manifest = scratch / "fixture-programs.jsonl"
                    manifest.write_text(json.dumps(record) + "\n", encoding="utf-8")
                    counts = import_programs(conn, collection_id=collection, path=manifest)
                    if counts.get("bound") != 1:
                        raise RuntimeError("synthetic program did not bind to its fixture source")
                    conversation, _ = _seed(conn, settings, config, embedder,
                        report="Read every requirement of the synthetic Fixture Systems minor.")
                    response, tools, _ = _probe(conn, settings, config, embedder, provider, conversation,
                        case="read-all-requirements", sources=[source], question=
                        "In this SYNTHETIC fixture only, read ALL 38 requirement items for Fixture Systems (Minor). "
                        "Follow every next-page cursor until nothing is unread. List every required CS course code "
                        "in order; do not call an eight-item page the entire checklist, and do not stop early "
                        "with an unread-requirements caveat. Do not infer registration eligibility.")
                    answer = response.answer if response else ""
                    final_context = json.loads(provider.calls[-1]["messages"][1]["content"])
                    progress = final_context.get("program_read_progress", [])
                    complete_reads = bool(progress) and all(
                        p["read_items"] == p["total_items"] and not p["next_cursor"] for p in progress)
                    print(json.dumps({"case": "read-all-requirements", "program_read_progress": progress}), flush=True)
                    passed.append(bool(response and complete_reads
                                       and all(str(n) in answer for n in range(901, 939))))
        finally:
            close_pools()
            server.cleanup()
            if previous_data_dir is None:
                os.environ.pop("DATA_DIR", None)
            else:
                os.environ["DATA_DIR"] = previous_data_dir
    print(json.dumps({"passed": passed, "provider_calls": len(provider.calls),
                     "accounted_tokens": provider.tokens}), flush=True)
    if not all(passed) or len(passed) != len(set(selected)):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
