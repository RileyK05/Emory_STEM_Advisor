"""Storage invariants for immutable conversation summary checkpoints."""

from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from app.conversations.store import ConversationStore


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

