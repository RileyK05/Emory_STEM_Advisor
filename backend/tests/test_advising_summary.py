"""Tests for deterministic advising summary and plan export (Item 5)."""

from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app.artifacts.advising_summary import (
    build_advising_summary_spec,
    create_advising_summary_artifact,
)
from app.artifacts.storage import ArtifactStore
from app.conversations.store import ConversationStore
from tests.fakes import FakeProvider
from tests.helpers import api_client, collection_name, ingest_text


def _append_turn_pair(
    store,
    conversation_id: UUID,
    sequence: int,
    *,
    question: str = "What are the requirements for CS major?",
    answer: str = "CS major requires CS 170 and CS 171 [1].",
    context_block: list[dict] | None = None,
    research: dict | None = None,
    applicability: dict | None = None,
):
    request_id = uuid4()
    chunk_id = str(uuid4())
    source_id = str(uuid4())
    if context_block is None:
        context_block = [
            {
                "chunkId": chunk_id,
                "sourceId": source_id,
                "sourceName": "cs_bulletin.pdf",
                "locatorLabel": "Section 4.1",
                "text": "CS 170 and CS 171 are foundational prerequisites.",
            }
        ]

    store.begin(
        conversation_id,
        request_id,
        sequence,
        {
            "query": question,
            "request": {"query": question},
            "effective": {"collectionId": "course-docs", "sourceIds": [source_id]},
        },
    )

    result = {
        "type": "answer",
        "answer": answer,
        "citations": [
            {
                "chunkId": b["chunkId"],
                "sourceName": b["sourceName"],
                "locatorLabel": b["locatorLabel"],
            }
            for b in context_block
        ],
        "trace": {"contextBlock": context_block},
    }
    if research:
        result["research"] = research
    if applicability:
        result["applicability"] = applicability

    store.finish(
        conversation_id,
        request_id,
        result,
    )


def test_advising_summary_pins_published_sequence_and_excludes_pending(db):
    workspace = "workspace-test"
    store = ConversationStore(db, workspace)
    conv = store.create("CS Degree Planning")
    conv_id = UUID(conv["conversationId"])

    _append_turn_pair(store, conv_id, 0, question="How do I declare CS?", answer="You declare via the department.")
    _append_turn_pair(store, conv_id, 2, question="Can I take CS 255 concurrently?", answer="No, CS 171 is required first.")

    # Begin turn 4, leaving it pending (input only)
    pending_request = uuid4()
    store.begin(
        conv_id,
        pending_request,
        4,
        {"query": "Pending question?", "request": {"query": "Pending question?"}},
    )

    # Export pinned up to sequence 2
    spec = build_advising_summary_spec(db, conv_id, workspace, through_sequence=2)
    assert spec.provenance["through_sequence"] == 2
    assert spec.title == "Advising Summary: CS Degree Planning"

    # Ensure pending turn 5 is excluded
    turn_heads = [b.text for b in spec.blocks if getattr(b, "level", None) == 3]
    assert turn_heads == ["Guidance (Turn 2)"]

    # When through_sequence is omitted, default to latest published turn (ignoring pending)
    spec_all = build_advising_summary_spec(db, conv_id, workspace)
    assert spec_all.provenance["through_sequence"] == 4
    all_heads = [b.text for b in spec_all.blocks if getattr(b, "level", None) == 3]
    assert all_heads == ["Guidance (Turn 2)", "Guidance (Turn 4)"]


def test_advising_summary_resolves_citations_and_evidence_map(db):
    workspace = "workspace-test"
    store = ConversationStore(db, workspace)
    conv = store.create("Math Minor")
    conv_id = UUID(conv["conversationId"])

    chunk_1 = str(uuid4())
    source_1 = str(uuid4())
    context = [
        {
            "chunkId": chunk_1,
            "sourceId": source_1,
            "sourceName": "math_handbook.pdf",
            "locatorLabel": "Page 12",
            "text": "Math 211 requires Math 111 and 112.",
        }
    ]
    _append_turn_pair(store, conv_id, 0, question="Math minor requirements?", answer="Math 211 is required [1].", context_block=context)

    spec = build_advising_summary_spec(db, conv_id, workspace)
    # Check evidence map links block to ArtifactReference
    assert "b_turn_2_text" in spec.evidence
    refs = spec.evidence["b_turn_2_text"]
    assert len(refs) == 1
    assert str(refs[0].chunk_id) == chunk_1
    assert str(refs[0].source_id) == source_1
    assert refs[0].source_name == "math_handbook.pdf"
    assert refs[0].text_snapshot == "Math 211 requires Math 111 and 112."

    # Sources table exists
    source_tables = [b for b in spec.blocks if getattr(b, "id", None) == "b_sources_table"]
    assert len(source_tables) == 1
    table = source_tables[0]
    assert table.rows[0][0] == "math_handbook.pdf"
    assert table.rows[0][1] == "Page 12"
    assert table.rows[0][3] == "Turn 2 [1]"
    assert len(spec.provenance["snapshot_digest"]) == 64

    # A missing historical passage is disclosed instead of silently claiming support.
    db.execute("UPDATE conversation_turns SET evidence = evidence || %s "
               "WHERE conversation_id = %s AND role = 'assistant'",
               (Jsonb({"contextBlock": []}), conv_id))
    incomplete = build_advising_summary_spec(db, conv_id, workspace)
    assert not incomplete.evidence
    blockers = next(b for b in incomplete.blocks if b.id == "b_open_list")
    assert "no complete saved passage" in blockers.items[0]


def test_advising_summary_surfaces_blockers_and_conflicts(db):
    workspace = "workspace-test"
    store = ConversationStore(db, workspace)
    conv = store.create("Biochem Major")
    conv_id = UUID(conv["conversationId"])

    research_payload = {
        "blocks": [
            {"kind": "fact", "text": "BIOL 141 is required for the major."},
            {"kind": "judgment", "text": "Chemistry track requires advanced lab sequence."},
        ],
        "unresolvedQuestions": ["Whether AP Chemistry credits satisfy CHEM 150 requirement."],
    }
    applicability_payload = {
        "level": "conflict",
        "headline": "Catalog editions conflict between 2024 and 2026 bulletins.",
        "conflicts": ["Catalog editions conflict: course prerequisite numbering changed."],
    }

    _append_turn_pair(
        store,
        conv_id,
        0,
        question="Can I use AP chem?",
        answer="AP policy varies; see findings.",
    )
    db.execute(
        "UPDATE conversation_turns SET evidence = evidence || %s WHERE conversation_id = %s AND sequence = 2",
        (Jsonb({"research": research_payload, "applicability": applicability_payload}), conv_id),
    )
    db.commit()

    spec = build_advising_summary_spec(db, conv_id, workspace)

    # Check findings list
    findings_lists = [b for b in spec.blocks if getattr(b, "id", None) == "b_turn_2_findings"]
    assert len(findings_lists) == 1
    assert "[Fact] BIOL 141 is required for the major." in findings_lists[0].items
    assert "[Judgment] Chemistry track requires advanced lab sequence." in findings_lists[0].items

    # Check open blockers list contains research unresolved questions AND applicability conflicts
    blocker_lists = [b for b in spec.blocks if getattr(b, "id", None) == "b_open_list"]
    assert len(blocker_lists) == 1
    items = blocker_lists[0].items
    assert "Whether AP Chemistry credits satisfy CHEM 150 requirement." in items
    assert "Catalog editions conflict: course prerequisite numbering changed." in items


def test_advising_summary_immutable_across_subsequent_turns(db, tmp_path):
    workspace = "workspace-test"
    store = ConversationStore(db, workspace)
    conv = store.create("Physics Track")
    conv_id = UUID(conv["conversationId"])

    _append_turn_pair(store, conv_id, 0, question="Physics 151?", answer="Physics 151 is offered in Fall.")

    # Create immutable artifact revision
    record = create_advising_summary_artifact(db, tmp_path, workspace, conv_id)
    artifact_id = record["artifact_id"]
    revision_id = record["revision_id"]

    # Read back initial document spec
    art_store = ArtifactStore(db, workspace_key=workspace, data_dir=tmp_path)
    initial_doc = art_store.get(artifact_id, revision_id)
    initial_blocks = initial_doc["spec"]["blocks"]
    assert len([b for b in initial_blocks if b.get("id") == "b_turn_2_text"]) == 1
    assert len([b for b in initial_blocks if b.get("id") == "b_turn_4_text"]) == 0

    # Add new turns to conversation
    _append_turn_pair(store, conv_id, 2, question="Physics 152?", answer="Physics 152 is offered in Spring.")

    # Fetch previously created artifact revision - must remain unchanged
    after_doc = art_store.get(artifact_id, revision_id)
    assert after_doc["revision_id"] == revision_id
    assert after_doc["spec"]["provenance"]["through_sequence"] == 2
    assert len([b for b in after_doc["spec"]["blocks"] if b.get("id") == "b_turn_4_text"]) == 0
    # Selected artifact revisions are checked against ownership and pinned by exact identity.
    reference = {"artifactId": str(artifact_id), "revisionId": str(revision_id)}
    with_reference = build_advising_summary_spec(
        db, conv_id, workspace, referenced_artifacts=[reference],
    )
    assert str(revision_id) in with_reference.provenance["artifact_revisions"]
    other = ConversationStore(db, "other-workspace").create("Other student's discussion")
    other_id = UUID(other["conversationId"])
    with pytest.raises(HTTPException) as denied:
        build_advising_summary_spec(db, other_id, "other-workspace", referenced_artifacts=[reference])
    assert denied.value.status_code == 404


@pytest.mark.parametrize("export_mode", ["direct", "queued", "queued_killed", "queued_cancelled"])
def test_advising_summary_api_endpoint(db, collection, tmp_path, migrated_dsn, monkeypatch, export_mode):
    ingest_text(db, collection, tmp_path, "guide.txt", "Physics requires Calculus I.")
    provider = FakeProvider("Physics requires Calculus I [1].")
    client = api_client(db, collection, provider=provider)

    created_conv = client.post("/api/conversations", json={"title": "Calc Planning"}).json()
    conv_id = created_conv["conversationId"]

    turn_res = client.post(
        f"/api/conversations/{conv_id}/turns",
        json={
            "query": "What are prerequisites for physics?",
            "collectionId": collection_name(db, collection),
            "requestId": str(uuid4()),
            "expectedSequence": 0,
        },
    )
    assert turn_res.status_code == 200

    # Call export-summary endpoint
    if export_mode == "direct":
        export_res = client.post(
            f"/api/conversations/{conv_id}/export-summary", json={"title": "Official Plan"})
        assert export_res.status_code == 200, export_res.text
        record = export_res.json()
    else:
        from dataclasses import replace

        import app.artifacts.advising_summary as summary_module
        from app.db.connection import connect
        from app.flags.service import FeatureFlagService
        from app.generation.artifacts import build_artifact_handlers
        from app.jobs.runtime import RunExecutor
        from app.jobs.store import JobStore
        from app.jobs.worker import run_once

        settings = replace(client.app.state.settings, database_url=migrated_dsn, data_dir=tmp_path)
        client.app.state.settings = settings
        jobs = JobStore(lambda: connect(migrated_dsn))
        client.app.state.job_store = jobs
        queued = client.post("/api/runs", json={"kind": "document", "inputs": {
            "prompt": "Official Plan", "conversationId": conv_id}, "idempotencyKey": export_mode})
        assert queued.status_code == 200, queued.text
        job_id = UUID(queued.json()["runId"])
        assert jobs.get(job_id).payload["inputs"]["throughSequence"] == 2
        _append_turn_pair(ConversationStore(db, "development"), UUID(conv_id), 2,
                          question="Later question", answer="Later answer")
        build = summary_module.build_advising_summary_spec

        def build_then_fence(*args, **kwargs):
            spec = build(*args, **kwargs)
            if export_mode == "queued_cancelled":
                jobs.cancel(job_id)
            elif export_mode == "queued_killed":
                FeatureFlagService.set_kill_switch(db, "advising_summary_export", True, "test stop")
            return spec

        monkeypatch.setattr(summary_module, "build_advising_summary_spec", build_then_fence)
        handlers = build_artifact_handlers(
            settings, client.app.state.config, client.app.state.embedder, provider,
            lambda: connect(migrated_dsn))
        finished = run_once(jobs, "summary-test", RunExecutor(handlers, data_dir=tmp_path))
        if export_mode in {"queued_killed", "queued_cancelled"}:
            assert finished.status.value == ("failed" if export_mode == "queued_killed" else "cancelled")
            assert db.execute("SELECT count(*) AS n FROM artifacts").fetchone()["n"] == 0
            assert provider.calls == 1
            return
        assert finished.status.value == "succeeded", finished.error
        record = client.get(f"/api/artifacts/{finished.result['artifactIds'][0]}").json()
        assert "Later answer" not in str(record["spec"])
    assert record["kind"] == "document"
    assert record["title"] == "Official Plan"
    assert record["spec"]["title"] == "Official Plan"
    assert record["spec"]["provenance"]["type"] == "advising_summary"
    for format_name in ("md", "docx"):
        exported = client.get(f"/api/artifacts/{record['artifactId']}/download", params={
            "revisionId": record["revisionId"], "format": format_name,
        })
        assert exported.status_code == 200, exported.text
        assert exported.headers["X-Artifact-Revision"] == record["revisionId"]
        if format_name == "md":
            text = exported.text
        else:
            from io import BytesIO

            from docx import Document

            document = Document(BytesIO(exported.content))
            text = "\n".join(p.text for p in document.paragraphs)
        assert "Advising conversation summary through turn 2" in text
        assert "guide.txt" in text

