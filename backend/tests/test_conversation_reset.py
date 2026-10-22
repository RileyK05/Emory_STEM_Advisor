"""Tests for conversation memory inspection and epoch boundary reset (Item 6)."""

from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from app.conversations.store import ConversationStore
from tests.helpers import api_client


def _append_turn_pair(store, conversation_id, sequence, *, question="question", answer="answer"):
    request_id = uuid4()
    snapshot = {
        "query": f"{question} {sequence}",
        "request": {"query": f"{question} {sequence}"},
        "effective": {"collectionId": "course-docs", "sourceIds": []},
    }
    store.begin(conversation_id, request_id, sequence, snapshot)
    store.finish(
        conversation_id,
        request_id,
        {
            "type": "answer",
            "answer": f"{answer} {sequence}",
            "citations": [
                {
                    "chunkId": "chunk-1",
                    "sourceName": "guide.pdf",
                    "locatorLabel": "page 2",
                }
            ],
            "trace": {
                "contextBlock": [
                    {
                        "chunkId": "chunk-1",
                        "sourceName": "guide.pdf",
                        "locatorLabel": "page 2",
                        "text": "Exact saved excerpt.",
                    }
                ]
            },
        },
    )


def test_conversation_reset_advances_epoch_and_stamps_boundary(db):
    workspace = "workspace-test"
    store = ConversationStore(db, workspace)
    conv = store.create("Memory Reset Test")
    conv_id = UUID(conv["conversationId"])

    assert conv["contextEpoch"] == 0
    assert conv["contextBoundarySequence"] == 0
    assert conv["contextResetAt"] is None

    # Add 2 turn pairs (turns 1..4)
    _append_turn_pair(store, conv_id, 0)
    _append_turn_pair(store, conv_id, 2)

    # Perform memory reset
    reset_resp = store.reset_memory(conv_id)
    assert reset_resp["contextEpoch"] == 1
    assert reset_resp["contextBoundarySequence"] == 4
    assert reset_resp["contextResetAt"] is not None

    # Inspect memory
    inspection = store.get(conv_id)
    assert inspection["context_epoch"] == 1
    assert inspection["context_boundary_sequence"] == 4
    assert inspection["context_reset_at"] is not None


def test_conversation_reset_excludes_pre_boundary_turns_from_context(db):
    workspace = "workspace-test"
    store = ConversationStore(db, workspace)
    conv = store.create("Context Exclusion Test")
    conv_id = UUID(conv["conversationId"])

    _append_turn_pair(store, conv_id, 0)
    _append_turn_pair(store, conv_id, 2)

    # Before reset, context contains turns 1..4
    ctx_before = store.context(conv_id, max_bytes=10000)
    assert len(ctx_before) == 4

    # Reset boundary to current latest turn (sequence 4)
    store.reset_memory(conv_id)

    # After reset, raw context history excludes turns <= boundary
    ctx_after = store.context(conv_id, max_bytes=10000)
    assert len(ctx_after) == 0

    # Add turn pair 3 (sequences 5 and 6)
    _append_turn_pair(store, conv_id, 4)

    ctx_new = store.context(conv_id, max_bytes=10000)
    assert len(ctx_new) == 2
    assert [t["sequence"] for t in ctx_new] == [5, 6]

    # Full transcript history still retains all 6 turns
    all_turns = store.turns(conv_id)["turns"]
    assert len(all_turns) == 6
    assert [t["sequence"] for t in all_turns] == [1, 2, 3, 4, 5, 6]


def test_conversation_reset_excludes_pre_boundary_turns_from_compaction(db):
    workspace = "workspace-test"
    store = ConversationStore(db, workspace)
    conv = store.create("Compaction Exclusion Test")
    conv_id = UUID(conv["conversationId"])

    # Add turns 1..8
    _append_turn_pair(store, conv_id, 0)
    _append_turn_pair(store, conv_id, 2)
    _append_turn_pair(store, conv_id, 4)
    _append_turn_pair(store, conv_id, 6)

    # Reset memory boundary at turn 8
    store.reset_memory(conv_id)

    # Add new turns after boundary: sequences 8, 10, 12, 14, 16 (turns 9..18)
    _append_turn_pair(store, conv_id, 8)
    _append_turn_pair(store, conv_id, 10)
    _append_turn_pair(store, conv_id, 12)
    _append_turn_pair(store, conv_id, 14)
    _append_turn_pair(store, conv_id, 16)

    # Begin summary checkpoint at sequence 18:
    # keeps recent 4 turns (15, 16, 17, 18), candidate covers through 14
    summary_id = uuid4()
    begun = store.begin_summary(conv_id, summary_id, 18, max_bytes=10000)
    assert begun["row"]["context_epoch"] == 1
    assert begun["row"]["candidate_covered_through"] == 14
    # Candidate turns must all be strictly after boundary (sequence > 8)
    assert all(t["sequence"] > 8 for t in begun["input"]["turns"])


def test_conversation_reset_fences_against_active_turns_and_summaries(db):
    workspace = "workspace-test"
    store = ConversationStore(db, workspace)
    conv = store.create("Fencing Test")
    conv_id = UUID(conv["conversationId"])

    _append_turn_pair(store, conv_id, 0)

    # 1. Active pending turn blocks reset with 409
    pending_req = uuid4()
    store.begin(conv_id, pending_req, 2, {"query": "racing query"})
    with pytest.raises(HTTPException) as turn_conflict:
        store.reset_memory(conv_id)
    assert turn_conflict.value.status_code == 409
    assert "turn request" in turn_conflict.value.detail.lower()

    # Finish the turn so it is no longer pending
    store.finish(conv_id, pending_req, {"type": "answer", "answer": "resolved"})

    # 2. Active pending summary blocks reset with 409
    _append_turn_pair(store, conv_id, 4)
    _append_turn_pair(store, conv_id, 6)
    _append_turn_pair(store, conv_id, 8)
    summary_id = uuid4()
    store.begin_summary(conv_id, summary_id, 10, max_bytes=10000)
    with pytest.raises(HTTPException) as sum_conflict:
        store.reset_memory(conv_id)
    assert sum_conflict.value.status_code == 409
    assert "summary is pending" in sum_conflict.value.detail.lower()


def test_conversation_memory_api_endpoints(db, collection):
    store = ConversationStore(db, "development")
    conv = store.create("API Test")
    conv_id = UUID(conv["conversationId"])
    _append_turn_pair(store, conv_id, 0)

    client = api_client(db, collection)

    # GET /api/conversations/{id}/memory
    inspect_res = client.get(f"/api/conversations/{conv_id}/memory")
    assert inspect_res.status_code == 200
    data = inspect_res.json()
    assert data["conversationId"] == str(conv_id)
    assert data["contextEpoch"] == 0
    assert data["contextBoundarySequence"] == 0
    assert data["contextResetAt"] is None

    # POST /api/conversations/{id}/memory/reset
    reset_res = client.post(f"/api/conversations/{conv_id}/memory/reset")
    assert reset_res.status_code == 200
    reset_data = reset_res.json()
    assert reset_data["conversationId"] == str(conv_id)
    assert reset_data["contextEpoch"] == 1
    assert reset_data["contextBoundarySequence"] == 2
    assert reset_data["contextResetAt"] is not None

    # Inspect again to verify persistence
    inspect_after = client.get(f"/api/conversations/{conv_id}/memory")
    assert inspect_after.status_code == 200
    data_after = inspect_after.json()
    assert data_after["contextEpoch"] == 1
    assert data_after["contextBoundarySequence"] == 2
    assert data_after["contextResetAt"] is not None
