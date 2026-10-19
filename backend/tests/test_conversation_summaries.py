"""Storage invariants for immutable conversation summary checkpoints."""

import json
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from app.conversations.store import ConversationStore
from app.db import queries
from tests.fakes import FakeProvider
from tests.helpers import api_client, collection_name, ingest_text


def _append_turn_pair(store, conversation_id, sequence, *, effective=None, answer="answer"):
    request_id = uuid4()
    snapshot = {"query": f"question {sequence}",
                "request": {"query": f"question {sequence}"},
                "effective": effective or {"collectionId": "course-docs", "sourceIds": []}}
    store.begin(conversation_id, request_id, sequence, snapshot)
    store.finish(conversation_id, request_id, {"type": "answer", "answer": answer,
        "citations": [{"chunkId": "chunk-1", "sourceName": "guide.pdf",
                       "locatorLabel": "page 2"}],
        "trace": {"contextBlock": [{"chunkId": "chunk-1", "sourceName": "guide.pdf",
                                     "locatorLabel": "page 2", "text": "Exact saved excerpt."}]}})
    return sequence + 1


def test_summary_checkpoint_covers_contiguous_old_turns_and_keeps_exact_references(db):
    store = ConversationStore(db, "workspace-a")
    made = store.create("Checkpoint")
    conversation_id = UUID(made["conversationId"])
    _append_turn_pair(store, conversation_id, 0, effective={
        "collectionId": "course-docs", "sourceIds": ["source-id"],
        "artifactReferences": [{"artifactId": "artifact-id", "revisionId": "revision-id",
                                "spec": {"title": "Original revision"}}],
    }, answer="First response.")
    _append_turn_pair(store, conversation_id, 2, answer="Second response.")
    _append_turn_pair(store, conversation_id, 4, answer="Third response.")
    _append_turn_pair(store, conversation_id, 6, answer="Fourth response.")

    summary_id = uuid4()
    begun = store.begin_summary(conversation_id, summary_id, 8, max_bytes=12000)
    assert begun["row"]["state"] == "pending"
    assert begun["row"]["candidate_covered_through"] == 4
    assert [turn["sequence"] for turn in begun["input"]["turns"]] == [1, 2, 3, 4]
    assert begun["references"][0]["effective"]["sourceIds"] == ["source-id"]
    assert begun["references"][0]["effective"]["artifactReferences"][0]["spec"]["title"] == "Original revision"
    assert begun["references"][1]["citations"][0]["chunkId"] == "chunk-1"
    assert begun["references"][1]["contextBlock"][0]["text"] == "Exact saved excerpt."

    with pytest.raises(HTTPException) as pending_turn:
        store.begin(conversation_id, uuid4(), 8, {"query": "cannot race"})
    assert pending_turn.value.status_code == 409
    assert store.summaries(conversation_id)[0]["coveredThrough"] is None

    saved = store.finish_summary(conversation_id, summary_id, state="completed",
        summary_text="A verified checkpoint text.", profile_id=uuid4(), profile_revision=2)
    assert saved["coveredThrough"] == 4
    assert saved["text"] == "A verified checkpoint text."
    assert store.summaries(conversation_id)[0]["referenceMetadata"] == begun["references"]

    replay = store.begin_summary(conversation_id, summary_id, 8, max_bytes=12000)
    assert replay["replay"] == saved
    with pytest.raises(HTTPException) as changed:
        store.begin_summary(conversation_id, summary_id, 10, max_bytes=12000)
    assert changed.value.status_code == 409

    _append_turn_pair(store, conversation_id, 8, answer="Fifth response.")
    next_id = uuid4()
    next_checkpoint = store.begin_summary(conversation_id, next_id, 10, max_bytes=12000)
    assert next_checkpoint["row"]["candidate_covered_through"] == 6
    assert next_checkpoint["input"]["previousSummary"]["summaryId"] == str(summary_id)
    assert next_checkpoint["input"]["previousSummary"]["text"] == "A verified checkpoint text."
    assert [turn["sequence"] for turn in next_checkpoint["input"]["turns"]] == [5, 6]


def test_failed_or_refused_summary_does_not_advance_coverage_and_retries_need_new_id(db):
    store = ConversationStore(db, "workspace-a")
    made = store.create("Failed checkpoint")
    conversation_id = UUID(made["conversationId"])
    for sequence in range(0, 8, 2):
        _append_turn_pair(store, conversation_id, sequence)

    failed_id = uuid4()
    store.begin_summary(conversation_id, failed_id, 8, max_bytes=12000)
    failed = store.finish_summary(conversation_id, failed_id, state="failed",
                                  failure_code="invalid_output")
    assert failed["state"] == "failed" and failed["coveredThrough"] is None
    replay = store.begin_summary(conversation_id, failed_id, 8, max_bytes=12000)
    assert replay["replay"]["state"] == "failed"

    retry = store.begin_summary(conversation_id, uuid4(), 8, max_bytes=12000)
    assert retry["row"]["candidate_covered_through"] == 4
    refused = store.finish_summary(conversation_id, retry["row"]["summary_id"],
                                   state="refused", failure_code="provider_refusal")
    assert refused["state"] == "refused" and refused["coveredThrough"] is None


def test_pending_turn_and_owner_scope_fence_summary_snapshot_reads(db):
    owner = ConversationStore(db, "workspace-a")
    made = owner.create("Private checkpoint")
    conversation_id = UUID(made["conversationId"])
    request_id = uuid4()
    owner.begin(conversation_id, request_id, 0, {"query": "pending question"})
    with pytest.raises(HTTPException) as pending:
        owner.begin_summary(conversation_id, uuid4(), 1, max_bytes=12000)
    assert pending.value.status_code == 409
    owner.abandon(conversation_id, request_id)
    with pytest.raises(HTTPException) as not_found:
        ConversationStore(db, "workspace-b").summaries(conversation_id)
    assert not_found.value.status_code == 404


def _append_referenced_pair(store, conversation_id, sequence, *, answer):
    request_id = uuid4()
    store.begin(conversation_id, request_id, sequence, {
        "query": f"question {sequence}",
        "request": {"query": f"question {sequence}"},
        "effective": {
            "collectionId": "course-docs",
            "sourceIds": None,
            "artifactReferences": [{
                "artifactId": "artifact-id", "revisionId": "revision-id",
                "spec": {"title": "Degree plan", "kind": "document"},
            }],
        },
    })
    store.finish(conversation_id, request_id, {
        "type": "answer",
        "answer": answer,
        "citations": [{"chunkId": "chunk-1", "sourceId": "source-1",
                       "sourceName": "guide.pdf", "locatorLabel": "page 2"}],
        "trace": {"contextBlock": [{
            "chunkId": "chunk-1", "sourceId": "source-1",
            "sourceName": "guide.pdf", "locatorLabel": "page 2",
            "text": "Exact saved excerpt.",
        }]},
    })
    return sequence + 1


def test_auto_compaction_replaces_old_turns_with_exact_pointer_memory(db, collection):
    provider = FakeProvider("Compacted memory narrative.")
    client = api_client(db, collection, provider=provider)
    client.app.state.settings = replace(
        client.app.state.settings,
        conversation_compaction_enabled=True,
        llm_context_window_tokens=1000,
        conversation_compaction_threshold=0.5,
        conversation_token_estimate_bytes=4,
        conversation_context_max_bytes=20_000,
        conversation_compaction_keep_turns=2,
        conversation_compaction_max_output_tokens=512,
    )
    store = ConversationStore(db, "development")
    conversation_id = UUID(store.create("Auto compact")["conversationId"])
    sequence = 0
    for index in range(4):
        _append_referenced_pair(
            store, conversation_id, sequence,
            answer=f"Answer {index}. " + "detail " * 120,
        )
        sequence += 2

    request_id = uuid4()
    response = client.post(
        f"/api/conversations/{conversation_id}/turns/stream",
        json={"query": "what next?", "collectionId": collection_name(db, collection),
              "requestId": str(request_id), "expectedSequence": sequence},
    )
    assert response.status_code == 202, response.text

    # One provider call: the checkpoint. The queued turn has not run.
    assert provider.calls == 1
    checkpoints = client.get(
        f"/api/conversations/{conversation_id}/summaries").json()
    assert [item["state"] for item in checkpoints] == ["completed"]
    text = checkpoints[0]["text"]
    assert "Compacted conversation memory" in text
    assert "guide.pdf" in text and "page 2" in text and "chunk-1" in text
    assert "source-1" in text
    assert "artifact-id" in text and "revision-id" in text and "Degree plan" in text
    assert checkpoints[0]["coveredThrough"] == 6

    row = db.execute(queries.get("get_conversation_request"), {
        "conversation_id": conversation_id, "request_id": request_id,
    }).fetchone()
    context = json.loads(row["input_snapshot"]["context"]["historyContext"])
    assert context["kind"] == "compacted_conversation_memory"
    assert context["coveredThrough"] == 6
    assert [turn["sequence"] for turn in context["recentTurns"]] == [7, 8]
    assert context["summaryId"] == checkpoints[0]["summaryId"]


def test_sync_turn_compacts_before_answering(db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "alpha.txt", "Alpha is documented.")
    provider = FakeProvider("Answer about alpha [1].")
    client = api_client(db, collection, provider=provider)
    client.app.state.settings = replace(
        client.app.state.settings,
        conversation_compaction_enabled=True,
        llm_context_window_tokens=1000,
        conversation_compaction_threshold=0.5,
        conversation_token_estimate_bytes=4,
        conversation_context_max_bytes=20_000,
        conversation_compaction_keep_turns=2,
        conversation_compaction_max_output_tokens=512,
    )
    store = ConversationStore(db, "development")
    conversation_id = UUID(store.create("Sync compact")["conversationId"])
    sequence = 0
    for index in range(4):
        _append_referenced_pair(
            store, conversation_id, sequence,
            answer=f"Answer {index}. " + "detail " * 120,
        )
        sequence += 2

    response = client.post(
        f"/api/conversations/{conversation_id}/turns",
        json={"query": "alpha", "collectionId": collection_name(db, collection),
              "requestId": str(uuid4()), "expectedSequence": sequence},
    )
    assert response.status_code == 200, response.text
    # One checkpoint call plus the actual answer call.
    assert provider.calls == 2
    checkpoints = client.get(
        f"/api/conversations/{conversation_id}/summaries").json()
    assert [item["state"] for item in checkpoints] == ["completed"]


def test_stale_pending_summary_is_recovered_before_a_new_turn(db):
    store = ConversationStore(db, "workspace-a")
    conversation_id = UUID(store.create("Crash recovery")["conversationId"])
    sequence = 0
    for _ in range(4):
        _append_turn_pair(store, conversation_id, sequence)
        sequence += 2

    summary_id = uuid4()
    store.begin_summary(conversation_id, summary_id, sequence, max_bytes=12000)
    db.execute(
        "UPDATE conversation_summaries SET created_at = now() - interval '10 minutes' "
        "WHERE summary_id = %(summary_id)s",
        {"summary_id": summary_id},
    )
    db.commit()

    request_id = uuid4()
    store.begin(conversation_id, request_id, sequence,
                {"query": "next", "request": {"query": "next"}})
    summary = db.execute(queries.get("get_conversation_summary"), {
        "summary_id": summary_id, "conversation_id": conversation_id,
        "workspace_key": "workspace-a",
    }).fetchone()
    assert summary["state"] == "failed"
    assert summary["failure_code"] == "stale_pending"
    store.abandon(conversation_id, request_id)


def test_completed_summary_that_lost_its_sequence_becomes_stale(db):
    store = ConversationStore(db, "workspace-a")
    made = store.create("Stale checkpoint")
    conversation_id = UUID(made["conversationId"])
    for sequence in range(0, 8, 2):
        _append_turn_pair(store, conversation_id, sequence)
    summary_id = uuid4()
    store.begin_summary(conversation_id, summary_id, 8, max_bytes=12000)
    # Simulate a newer write racing the frozen checkpoint (a pending summary
    # fences normal writes, so force the sequence directly).
    db.execute(
        "UPDATE conversations SET last_sequence = 10 WHERE conversation_id = %(c)s",
        {"c": conversation_id},
    )
    db.commit()
    stale = store.finish_summary(conversation_id, summary_id, state="completed",
                                 summary_text="Late checkpoint text.")
    assert stale["state"] == "stale"
    assert stale["coveredThrough"] is None
    assert stale["failureCode"] == "stale_conversation"
    assert stale["text"] is None

