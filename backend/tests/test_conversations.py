"""Conversation replay, immutable evidence, private access, and context safety."""

import json
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from app.auth.dependencies import get_workspace_key
from app.conversations.store import ConversationStore, history_prompt
from app.db import queries
from app.db.connection import connect
from app.jobs.store import JobStore, SafeJobError
from tests.fakes import FakeProvider
from tests.helpers import api_client, collection_name, ingest_text


def test_history_and_exact_citations_survive_reload_and_request_replay(db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "alpha.txt", "Alpha is a documented course concept.")
    provider = FakeProvider("Alpha is documented [1].")
    client = api_client(db, collection, provider=provider)
    made = client.post("/api/conversations", json={"title": "Alpha questions"}).json()
    path = "/api/conversations/" + made["conversationId"]
    first = {"query": "alpha", "collectionId": collection_name(db, collection),
             "requestId": str(uuid4()), "expectedSequence": 0}
    response = client.post(path + "/turns", json=first)
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["sequence"] == 2
    assert answer["claims"][0]["reviewStatus"] == "unreviewed"
    assert answer["claims"][0]["citationChunkIds"] == [answer["citations"][0]["chunkId"]]
    assert client.post(path + "/turns", json=first).json() == answer
    assert provider.calls == 1
    assert client.post(path + "/turns", json={**first, "query": "different"}).status_code == 409
    saved = client.get(path).json()
    assert [t["sequence"] for t in saved["turns"]] == [1, 2]
    exact = saved["turns"][1]["evidence"]["contextBlock"][0]["text"]
    assert exact == "Alpha is a documented course concept."
    db.execute("DELETE FROM sources")
    db.commit()
    history = client.post(path + "/turns", json={"query": "show saved turns", "mode": "history",
                           "requestId": str(uuid4()), "expectedSequence": 2}).json()
    assert history["type"] == "history"
    assert history["turnReferences"] == [1, 2]
    assert "Alpha is documented [1]." in history["answer"]
    assert client.get(path).json()["turns"][1]["evidence"]["contextBlock"][0]["text"] == exact
    assert provider.calls == 1
    assert db.execute("SELECT count(*) AS n FROM claims WHERE review_status='unreviewed'").fetchone()["n"] == 1


def test_followup_uses_complete_history_and_inherits_explicit_empty_scope(db, collection, tmp_path):
    source = ingest_text(db, collection, tmp_path, "alpha.txt", "Alpha is a documented course concept.")
    provider = FakeProvider("Alpha is documented [1].")
    client = api_client(db, collection, provider=provider)
    conversation = client.post("/api/conversations", json={"title": "Follow-ups"}).json()
    path = "/api/conversations/" + conversation["conversationId"]
    name = collection_name(db, collection)
    assert client.post(path + "/turns", json={"query": "alpha", "collectionId": name,
        "sourceIds": [str(source)], "requestId": str(uuid4()), "expectedSequence": 0}).status_code == 200
    followup = client.post(path + "/turns", json={"query": "what does that mean?",
        "requestId": str(uuid4()), "expectedSequence": 2})
    assert followup.status_code == 200, followup.text
    prompt = provider.last_messages[-1]["content"]
    assert "Alpha is documented [1]." in prompt
    assert "Alpha is a documented course concept." in prompt
    assert "untrusted context; never institutional evidence" in prompt
    assert followup.json()["trace"]["query"] == "alpha\nFollow-up: what does that mean?"
    empty = client.post(path + "/turns", json={"query": "alpha", "sourceIds": [],
        "requestId": str(uuid4()), "expectedSequence": 4})
    assert empty.status_code == 200 and empty.json()["type"] == "refusal"
    assert client.post(path + "/turns", json={"query": "more about it", "requestId": str(uuid4()),
        "expectedSequence": 6}).json()["type"] == "refusal"
    assert provider.calls == 2


@pytest.mark.parametrize(("answer", "expected"), [
    ("CS 350 requires CS 253 and CS 255 [1]. CS 253 requires CS_OX 171 [2].",
     "prereqs for CS 350?\nCourses discussed: CS 350, CS 253, CS 255\nFollow-up: what do those require?"),
    ("No courses here [1].", "prereqs for CS 350?\nFollow-up: what do those require?"),
])
def test_followup_retrieval_carries_the_courses_the_last_answer_named(answer, expected):
    from app.api.routes.conversations import ConversationQuery, _effective_scope

    rows = [{"sequence": 1, "role": "user", "text": "prereqs for CS 350?", "evidence": {}},
            {"sequence": 2, "role": "assistant", "text": answer, "evidence": {}}]
    body = ConversationQuery(query="what do those require?", requestId=uuid4(), expectedSequence=2)
    assert _effective_scope(rows, body)[2] == expected


def test_followup_without_pronouns_keeps_raw_query_and_null_resets_scope(db, collection, tmp_path):
    source = ingest_text(db, collection, tmp_path, "alpha.txt", "Alpha is a documented course concept.")
    provider = FakeProvider("Alpha is documented [1].")
    client = api_client(db, collection, provider=provider)
    conversation = client.post("/api/conversations", json={"title": "Scope resets"}).json()
    path = "/api/conversations/" + conversation["conversationId"]
    name = collection_name(db, collection)
    assert client.post(path + "/turns", json={"query": "alpha", "collectionId": name,
        "sourceIds": [str(source)], "requestId": str(uuid4()), "expectedSequence": 0}).status_code == 200
    plain = client.post(path + "/turns", json={"query": "alpha requirements", "sourceIds": [str(source)],
        "requestId": str(uuid4()), "expectedSequence": 2})
    assert plain.status_code == 200, plain.text
    # A non-pronoun follow-up is not rewritten with prior question text.
    assert plain.json()["trace"]["query"] == "alpha requirements"
    # An explicit null resets to the whole collection rather than inheriting [source].
    reset = client.post(path + "/turns", json={"query": "alpha", "sourceIds": None,
        "requestId": str(uuid4()), "expectedSequence": 4})
    assert reset.status_code == 200, reset.text
    assert reset.json()["type"] == "answer"


def test_private_conversation_cannot_be_listed_read_written_or_abandoned_by_other_owner(db, collection):
    client = api_client(db, collection)
    owner = ["alice"]
    client.app.dependency_overrides[get_workspace_key] = lambda: owner[0]
    made = client.post("/api/conversations", json={"title": "Private"}).json()
    path = "/api/conversations/" + made["conversationId"]
    owner[0] = "bob"
    assert client.get("/api/conversations").json() == []
    assert client.get(path).status_code == 404
    assert client.post(path + "/turns", json={"query": "history", "mode": "history",
        "requestId": str(uuid4()), "expectedSequence": 0}).status_code == 404
    assert client.post(path + "/turns/" + str(uuid4()) + "/abandon").status_code == 404


def test_pending_unknown_request_blocks_resubmit_and_abandon_fences_late_completion(db, migrated_dsn):
    store = ConversationStore(db, "alice")
    made = store.create("Concurrent")
    conversation = UUID(made["conversationId"])
    request_id = uuid4()
    snapshot = {"query": "original inputs", "request": {"query": "original inputs"}}
    store.begin(conversation, request_id, 0, snapshot)
    other = connect(migrated_dsn)
    try:
        duplicate = ConversationStore(other, "alice")
        with pytest.raises(HTTPException) as pending:
            duplicate.begin(conversation, request_id, 0, snapshot)
        assert pending.value.status_code == 409
        with pytest.raises(HTTPException):
            duplicate.begin(conversation, uuid4(), 0, snapshot)
        result = duplicate.abandon(conversation, request_id)
        assert result["sequence"] == 2
        assert duplicate.abandon(conversation, request_id) == result
        with pytest.raises(HTTPException) as stale:
            store.finish(conversation, request_id, {"type": "answer", "answer": "late"})
        assert stale.value.status_code == 409
        assert store.turns(conversation)["turns"][-1]["status"] == "abandoned"
    finally:
        other.close()


def test_context_overflow_keeps_every_saved_turn_and_never_calls_provider(db, collection):
    provider = FakeProvider()
    client = api_client(db, collection, provider=provider)
    client.app.state.settings = replace(client.app.state.settings, conversation_context_max_bytes=1024)
    made = client.post("/api/conversations", json={"title": "Large history"}).json()
    conversation = UUID(made["conversationId"])
    store = ConversationStore(db, "development")
    request_id = uuid4()
    store.begin(conversation, request_id, 0, {"query": "x" * 2000})
    store.finish(conversation, request_id, {"type": "history", "answer": "saved"})
    path = "/api/conversations/" + str(conversation)
    response = client.post(path + "/turns", json={"query": "next", "expectedSequence": 2,
                                                  "requestId": str(uuid4())})
    assert response.status_code == 422
    saved = client.get(path).json()
    assert saved["lastSequence"] == 2 and saved["pendingRequestId"] is None
    assert saved["turns"][0]["text"] == "x" * 2000
    assert provider.calls == 0


def test_history_and_its_budget_carry_cited_passages_not_every_retrieved_one(db, collection):
    store = ConversationStore(db, "development")
    conversation = UUID(store.create("Planned context")["conversationId"])
    request_id = uuid4()
    store.begin(conversation, request_id, 0, {"query": "data engineering?"})
    context = [{"chunkId": f"c{i}", "sourceId": "s", "sourceName": "atlas",
                "locatorLabel": f"CS {i}", "text": "passage " * 200} for i in range(20)]
    store.finish(conversation, request_id, {"type": "answer", "answer": "Take CS 1 [2].",
        "citations": [{"chunkId": "c1", "sourceId": "s", "sourceName": "atlas",
                       "locatorLabel": "CS 1"}], "trace": {"contextBlock": context}})
    # Twenty retrieved passages (~32 KB) were saved, but history needs only the cited one.
    rows = store.context(conversation, max_bytes=4096)
    assert len(rows[-1]["evidence"]["contextBlock"]) == 20
    history = json.loads(history_prompt(rows))
    assert [item["chunkId"] for item in history[-1]["evidence"]["contextBlock"]] == ["c1"]
    with pytest.raises(HTTPException):
        store.context(conversation, max_bytes=1024)


def test_artifact_context_binds_exact_revision_not_current_title_only(db, collection):
    client = api_client(db, collection)
    spec = {"kind": "document", "title": "Exact artifact", "blocks": [
        {"id": "p1", "type": "paragraph", "text": "Immutable original paragraph"}]}
    original = client.post("/api/artifacts", json={"kind": "document", "spec": spec}).json()
    revised = {**spec, "blocks": [{"id": "p1", "type": "paragraph", "text": "New paragraph"}]}
    client.post("/api/artifacts/" + original["artifactId"] + "/revisions", json={
        "expectedParentRevisionId": original["revisionId"], "spec": revised})
    made = client.post("/api/conversations", json={"title": "Artifact reference"}).json()
    path = "/api/conversations/" + made["conversationId"]
    response = client.post(path + "/turns", json={"query": "show history", "mode": "history",
        "requestId": str(uuid4()), "expectedSequence": 0,
        "artifactReferences": [{"artifactId": original["artifactId"], "revisionId": original["revisionId"]}]})
    assert response.status_code == 200, response.text
    reference = client.get(path).json()["turns"][0]["evidence"]["effective"]["artifactReferences"][0]
    assert reference["spec"]["blocks"][0]["text"] == "Immutable original paragraph"


def test_stream_request_replay_inspects_job_before_reloading_context(db, collection):
    provider = FakeProvider("This provider must not be called by stream submission.")
    client = api_client(db, collection, provider=provider)
    made = client.post("/api/conversations", json={"title": "Stream replay"}).json()
    path = "/api/conversations/" + made["conversationId"]
    body = {"query": "alpha " + "x" * 1500, "collectionId": collection_name(db, collection),
            "requestId": str(uuid4()), "expectedSequence": 0}

    first = client.post(path + "/turns/stream", json=body)
    assert first.status_code == 202, first.text
    job_id = first.json()["jobId"]
    assert first.json()["state"] == "queued"

    # A replay must be a storage-only inspection even when current transcript
    # context could no longer be prepared within the configured budget.
    client.app.state.settings = replace(client.app.state.settings,
                                         conversation_context_max_bytes=1024)
    replay = client.post(path + "/turns/stream", json=body)
    assert replay.status_code == 202, replay.text
    assert replay.json()["jobId"] == job_id
    assert replay.json()["state"] == "queued"
    assert provider.calls == 0

    changed = client.post(path + "/turns/stream", json={**body, "query": "different"})
    assert changed.status_code == 409
    assert provider.calls == 0


def test_stream_request_with_sync_identity_never_creates_another_job(db, collection):
    client = api_client(db, collection, provider=FakeProvider())
    made = client.post("/api/conversations", json={"title": "Sync identity"}).json()
    conversation_id = UUID(made["conversationId"])
    path = "/api/conversations/" + str(conversation_id)
    body = {"query": "alpha", "collectionId": collection_name(db, collection),
            "requestId": str(uuid4()), "expectedSequence": 0}
    store = ConversationStore(db, "development")
    store.begin(conversation_id, UUID(body["requestId"]), 0,
                {"request": body, "query": body["query"]})

    response = client.post(path + "/turns/stream", json=body)
    assert response.status_code == 409
    assert db.execute("SELECT count(*) AS n FROM jobs WHERE kind = 'conversation.turn'").fetchone()["n"] == 0
    events = client.get(path + "/turns/" + body["requestId"] + "/events")
    assert events.status_code == 200
    assert events.json()["jobState"] == "unknown" and events.json()["done"] is True


def test_same_revision_deindex_withholds_stream_answer(db, migrated_dsn, collection, tmp_path):
    from app.conversations.worker import ConversationTurnExecutor, ConversationTurnResult

    source_id = ingest_text(db, collection, tmp_path, "fenced.txt", "A source that can be deindexed.")
    client = api_client(db, collection, provider=FakeProvider())
    made = client.post("/api/conversations", json={"title": "Source fence"}).json()
    path = "/api/conversations/" + made["conversationId"]
    accepted = client.post(path + "/turns/stream", json={
        "query": "fenced", "collectionId": collection_name(db, collection),
        "sourceIds": [str(source_id)], "requestId": str(uuid4()), "expectedSequence": 0,
    })
    assert accepted.status_code == 202, accepted.text
    job_id = UUID(accepted.json()["jobId"])
    revision = db.execute(
        "SELECT index_revision FROM sources WHERE source_id=%s", (source_id,)
    ).fetchone()["index_revision"]
    store = JobStore(lambda: connect(migrated_dsn))
    claimed = store.claim("source-fence-worker")
    assert claimed is not None and claimed.job_id == job_id

    # Deindex without changing the last-good revision. Revision-only checks
    # would accept this source even though it is no longer citable.
    db.execute("UPDATE sources SET status='failed' WHERE source_id=%s", (source_id,))
    db.commit()
    result = ConversationTurnResult(
        response={"type": "answer", "answer": "This answer must not publish."},
        source_snapshot={str(source_id): revision}, collection_id=collection,
        operation_id=None, profile_selections=(),
    )
    with pytest.raises(SafeJobError, match="source changed"):
        store._finish_success(claimed, claimed.lease_token, result,
                              publisher=ConversationTurnExecutor.publish_success)
    transcript = client.get(path).json()
    assert transcript["pendingRequestId"] == accepted.json()["requestId"]
    assert len(transcript["turns"]) == 1 and transcript["turns"][0]["role"] == "user"


@pytest.mark.parametrize("fence", ["operation_deadline", "profile_revision"])
def test_final_publication_fences_keep_measured_usage_and_withhold_assistant(
    db, migrated_dsn, collection, fence,
):
    from cryptography.fernet import Fernet

    from app.config import load_settings
    from app.connections.models import ProfileCreate
    from app.connections.repository import ConnectionProfiles
    from app.conversations.worker import ConversationTurnExecutor, ConversationTurnResult
    from app.generation.evidence import record_attempt
    from app.generation.operation import GenerationOperation
    from app.jobs.store import JobStore
    from app.jobs.worker import run_once
    from app.llm.base import GenerationRequest, GenerationResult, TextMessage, Usage

    workspace = "publication-fence-" + fence
    conversation_store = ConversationStore(db, workspace)
    conversation_id = UUID(conversation_store.create("Publication fence")["conversationId"])
    request_id = uuid4()
    snapshot = {"query": "one measured generation", "request": {"query": "one measured generation"},
                "effective": {"sourceSnapshot": {}}, "context": {"historyContext": "[]"}}
    accepted = conversation_store.begin_stream(conversation_id, request_id, 0, snapshot)
    job_id = accepted["job_id"]

    profiles = ConnectionProfiles(Fernet.generate_key())
    selected_profile = None
    if fence == "profile_revision":
        selected_profile = profiles.create(db, ProfileCreate(
            name="Publication fence", provider="openai", model_id="offline-test",
            roles=frozenset({"standard"}), capabilities=frozenset({"structured_output"}),
            data_scopes=frozenset({"institutional_corpus"}), secret={"api_key": "test-secret"},
        ))
        db.commit()

    class GeneratedOnce:
        publish_success = ConversationTurnExecutor.publish_success

        def __call__(self, job, _context):
            conn = connect(migrated_dsn)
            try:
                settings = load_settings()
                operation = GenerationOperation(conn, settings, job.job_id, workspace)
                request = operation.prepare(GenerationRequest(
                    messages=[TextMessage("user", "fenced generation")], max_output_tokens=8,
                ))
                measured = GenerationResult(
                    text="A measured answer.", finish_reason="stop",
                    usage=Usage(9, 5, source="provider"), model="offline-test",
                )
                record_attempt(conn, None, settings, measured, None, 5,
                               operation_id=job.job_id, request=request)
                conn.commit()
                operation.account(measured, None)
                conn.commit()
                selected = ()
                if fence == "operation_deadline":
                    conn.execute(
                        "UPDATE operations SET started_at=clock_timestamp()-interval '2 minutes', "
                        "deadline_at=clock_timestamp()-interval '1 minute' WHERE operation_id=%s",
                        (job.job_id,),
                    )
                else:
                    profiles.clear_secret(conn, selected_profile.profile_id,
                                          expected_revision=selected_profile.revision)
                    selected = ((selected_profile.profile_id, selected_profile.revision),)
                conn.commit()
                return ConversationTurnResult(
                    response={"type": "answer", "answer": "A measured answer."},
                    source_snapshot={}, collection_id=collection, operation_id=job.job_id,
                    profile_selections=selected,
                )
            finally:
                conn.close()

    job_store = JobStore(lambda: connect(migrated_dsn))
    failed = run_once(job_store, "publication-fence-worker", GeneratedOnce())
    assert failed is not None and failed.status.value == "failed"
    assert failed.result is None
    assert failed.error["type"] == ("operation_fence" if fence == "operation_deadline"
                                    else "stale_connection")
    status = db.execute(queries.get("get_operation_budget_for_update"), {
        "operation_id": job_id,
    }).fetchone()
    assert status["accounted_tokens"] == 14 and status["reserved_tokens"] == 0
    calls = db.execute("SELECT status, prompt_tokens, completion_tokens, usage_source "
                       "FROM model_calls WHERE operation_id=%s", (job_id,)).fetchall()
    assert len(calls) == 1
    assert (calls[0]["status"], calls[0]["prompt_tokens"], calls[0]["completion_tokens"],
            calls[0]["usage_source"]) == ("completed", 9, 5, "provider")
    request_row = conversation_store.request_for_owner(conversation_id, request_id)
    assert request_row["state"] == "pending"
    turns = conversation_store.turns(conversation_id)["turns"]
    assert len(turns) == 1 and turns[0]["role"] == "user"
