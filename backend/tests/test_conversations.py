"""Conversation replay, immutable evidence, private access, and context safety."""

import json
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from app.auth.dependencies import get_workspace_key
from app.auth.models import Principal
from app.conversations.experiments import ExperimentService
from app.conversations.store import (
    ConversationStore,
    history_prompt,
    research_history_prompt,
    warm_citations,
)
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
     "prereqs for CS 350?\nCourses discussed: CS 350, CS 253, CS 255, CS_OX 171\nFollow-up: what do those require?"),
    ("No courses here [1].", "prereqs for CS 350?\nFollow-up: what do those require?"),
])
def test_followup_retrieval_carries_the_courses_the_last_answer_named(answer, expected):
    from app.api.routes.conversations import ConversationQuery, _effective_scope

    rows = [{"sequence": 1, "role": "user", "text": "prereqs for CS 350?", "evidence": {}},
            {"sequence": 2, "role": "assistant", "text": answer, "evidence": {}}]
    body = ConversationQuery(query="what do those require?", requestId=uuid4(), expectedSequence=2)
    assert _effective_scope(rows, body)[2] == expected


@pytest.mark.parametrize(("query", "rows", "expected"), [
    ("What about the major?",
     [{"sequence": 1, "role": "user",
       "text": "What are the requirements for the computer science minor?", "evidence": {}},
      {"sequence": 2, "role": "assistant",
       "text": "The minor requires MATH 111 and CS 170 [1].", "evidence": {}}],
     ("What are the requirements for the computer science minor?\n"
      "Courses discussed: MATH 111, CS 170\nFollow-up: What about the major?")),
    ("Which of those require CS 253 first?",
     [{"sequence": 1, "role": "user",
       "text": "What are the requirements for the computer science minor?", "evidence": {}},
      {"sequence": 2, "role": "assistant",
       "text": "The minor requires MATH 111 and CS 170 [1].", "evidence": {}},
      {"sequence": 3, "role": "user", "text": "What about the major?", "evidence": {}},
      {"sequence": 4, "role": "assistant", "text": "The CS BS requires CS 253 [1].", "evidence": {}}],
     ("What are the requirements for the computer science minor?\n"
      "Courses discussed: CS 253\nFollow-up: Which of those require CS 253 first?")),
])
def test_followup_without_pronoun_folds_in_the_earlier_topic(query, rows, expected):
    from app.api.routes.conversations import ConversationQuery, _effective_scope

    body = ConversationQuery(query=query, requestId=uuid4(), expectedSequence=99)
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


def test_history_carries_citation_labels_not_retrieved_passage_bodies(db, collection):
    store = ConversationStore(db, "development")
    conversation = UUID(store.create("Planned context")["conversationId"])
    request_id = uuid4()
    store.begin(conversation, request_id, 0, {"query": "data engineering?"})
    context = [{"chunkId": f"c{i}", "sourceId": "s", "sourceName": "atlas",
                "locatorLabel": f"CS {i}", "text": "passage " * 200} for i in range(20)]
    store.finish(conversation, request_id, {"type": "answer",
        "answer": "Take CS 1 [2]. " + "y" * 3000,
        "citations": [{"chunkId": "c1", "sourceId": "s", "sourceName": "atlas",
                       "locatorLabel": "CS 1"}], "trace": {"contextBlock": context}})
    # Twenty retrieved passages (~32 KB) were saved for display, but history
    # re-sends only the answer text and compact citation labels (EMO-017).
    rows = store.context(conversation, max_bytes=8192)
    assert len(rows[-1]["evidence"]["contextBlock"]) == 20
    history = json.loads(history_prompt(rows))
    assert "passage" not in json.dumps(history)
    assert history[-1]["evidence"]["citations"] == [
        {"chunkId": "c1", "sourceName": "atlas", "locatorLabel": "CS 1"}]
    assert history[0]["evidence"] == {}
    with pytest.raises(HTTPException):
        store.context(conversation, max_bytes=1024)
    archived = store.context(conversation, max_bytes=1024, archive=True)
    selected = research_history_prompt(archived, max_bytes=1024)
    assert len(selected.encode("utf-8")) <= 1024
    assert json.loads(selected)["omitted_turns"] == 1
    assert [turn["text"] for turn in json.loads(selected)["turns"]] == ["data engineering?"]
    assert archived[-1]["text"] == rows[-1]["text"]
    # A newer long reply cannot hide a student correction. Keep exact messages,
    # including the superseded report, so the model can reconcile chronology.
    correction = {"sequence": 3, "role": "user", "text": "CS 253 is now completed.",
                  "evidence": {}}
    replies = {"sequence": 4, "role": "assistant", "text": "No CS 253. " * 400,
               "evidence": {}}
    selected = json.loads(research_history_prompt([*archived, correction, replies], max_bytes=1024))
    assert [turn["sequence"] for turn in selected["turns"]] == [1, 3]
    assert selected["turns"][-1]["text"] == correction["text"]
    assert selected["omitted_turns"] == 2


def test_warm_citations_keep_cited_bodies_newest_first():
    rows = [
        {"role": "assistant", "evidence": {
            "citations": [{"chunkId": "c-old", "sourceName": "Handbook", "locatorLabel": "pay"}],
            "contextBlock": [
                {"chunkId": "c-old", "sourceId": "s-old", "sourceName": "Handbook",
                 "locatorLabel": "pay", "text": "WARMTOKEN handbook body"},
                {"chunkId": "c-unused", "sourceId": "s-old", "sourceName": "Handbook",
                 "locatorLabel": "other", "text": "retrieved but never cited"},
            ],
        }},
        {"role": "assistant", "evidence": {
            "citations": [
                {"chunkId": "c-new", "sourceName": "Atlas", "locatorLabel": "CS 170"},
                {"chunkId": "c-old", "sourceName": "Handbook", "locatorLabel": "pay"},
            ],
            "contextBlock": [
                {"chunkId": "c-new", "sourceId": "s-new", "sourceName": "Atlas",
                 "locatorLabel": "CS 170", "text": "four credit hours"},
                {"chunkId": "c-old", "sourceId": "s-old", "sourceName": "Handbook",
                 "locatorLabel": "pay", "text": "WARMTOKEN handbook body"},
            ],
        }},
    ]
    warm = warm_citations(rows)
    assert [item["chunkId"] for item in warm] == ["c-new", "c-old"]
    assert "retrieved but never cited" not in json.dumps(warm)
    assert [item["chunkId"] for item in warm_citations(rows, allowed_source_ids={"s-new"})] == ["c-new"]
    huge = [{"role": "assistant", "evidence": {
        "citations": [{"chunkId": "c-big"}],
        "contextBlock": [{"chunkId": "c-big", "sourceId": "s", "sourceName": "s",
                          "locatorLabel": "s", "text": "x" * 20_000}],
    }}]
    assert warm_citations(huge) == []


def test_followup_keeps_a_cited_passage_when_retrieval_moves_on(db, collection, tmp_path, monkeypatch):
    ingest_text(db, collection, tmp_path, "handbook.txt",
                "WARMTOKEN handbook body about software developer pay.")
    ingest_text(db, collection, tmp_path, "course.txt",
                "CS 170 is four credit hours of programming.")
    provider = FakeProvider("Noted [1].")
    client = api_client(db, collection, provider=provider)
    made = client.post("/api/conversations", json={"title": "Warm citations"}).json()
    path = "/api/conversations/" + made["conversationId"]
    name = collection_name(db, collection)
    first = client.post(path + "/turns", json={
        "query": "WARMTOKEN handbook", "collectionId": name,
        "requestId": str(uuid4()), "expectedSequence": 0})
    assert first.status_code == 200, first.text
    assert "WARMTOKEN" in first.json()["trace"]["contextBlock"][0]["text"]

    from app.api import query_service
    real_retrieve = query_service.retrieve

    def without_handbook(*args, **kwargs):
        outcome = real_retrieve(*args, **kwargs)
        chunks = getattr(outcome, "chunks", None)
        if not chunks:
            return outcome
        kept = [chunk for chunk in chunks if "WARMTOKEN" not in chunk.text]
        return replace(outcome, chunks=kept)

    monkeypatch.setattr(query_service, "retrieve", without_handbook)
    second = client.post(path + "/turns", json={
        "query": "how many credit hours is CS 170", "collectionId": name,
        "requestId": str(uuid4()), "expectedSequence": 2})
    assert second.status_code == 200, second.text
    prompt = provider.last_messages[-1]["content"]
    assert "WARMTOKEN handbook body" in prompt
    assert "four credit hours" in prompt
    texts = [item["text"] for item in second.json()["trace"]["contextBlock"]]
    assert any("WARMTOKEN" in text for text in texts)
    assert any("four credit hours" in text for text in texts)
    history = json.loads(history_prompt(client.get(path).json()["turns"]))
    assert "WARMTOKEN handbook body" not in json.dumps(history)


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


def _admission_client(db, collection, provider, *, database_url=None, **overrides):
    from fastapi.testclient import TestClient

    from app.api import create_app
    from app.config import load_settings
    from tests.fakes import FakeEmbedder

    settings = replace(load_settings(), **overrides)
    if database_url is not None:
        settings = replace(settings, database_url=database_url)
    app = create_app(settings=settings, allow_test_client=True,
                     embedder=FakeEmbedder(dim=8), provider=provider)
    app.state.override_conn = db
    return TestClient(app)


def _start(client, collection_name_value, query="alpha"):
    made = client.post("/api/conversations", json={"title": query}).json()
    path = "/api/conversations/" + made["conversationId"]
    body = {"query": query, "collectionId": collection_name_value,
            "requestId": str(uuid4()), "expectedSequence": 0}
    return path, body, client.post(path + "/turns/stream", json=body)


def test_stream_start_enforces_workspace_admission_cap(db, migrated_dsn, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "alpha.txt", "Alpha is documented.")
    client = _admission_client(db, collection, FakeProvider("Alpha [1]."),
                               database_url=migrated_dsn,
                               admission_max_pending_workspace=1)
    name = collection_name(db, collection)
    first_path, first_body, first = _start(client, name)
    assert first.status_code == 202, first.text

    second_path, second_body, second = _start(client, name, "beta")
    assert second.status_code == 429
    assert second.headers["retry-after"] == "5"

    # A terminal request releases its slot: abandoning the first admits the second.
    client.post(f"{first_path}/turns/{first_body['requestId']}/abandon")
    assert client.post(second_path + "/turns/stream", json=second_body).status_code == 202


def test_stream_start_enforces_global_admission_cap(db, migrated_dsn, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "alpha.txt", "Alpha is documented.")
    client = _admission_client(db, collection, FakeProvider("Alpha [1]."),
                               database_url=migrated_dsn,
                               admission_max_pending_global=1)
    name = collection_name(db, collection)
    _, _, first = _start(client, name)
    assert first.status_code == 202, first.text
    _, _, second = _start(client, name, "beta")
    assert second.status_code == 429


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


def test_conversation_failure_hook_settles_the_pending_request(db, migrated_dsn):
    from app.conversations.worker import ConversationTurnExecutor
    from app.db.connection import connect
    from app.jobs.store import JobStore

    store = ConversationStore(db, "development")
    conversation_id = UUID(store.create("Failure hook")["conversationId"])
    request_id = uuid4()
    accepted = store.begin_stream(
        conversation_id, request_id, 0,
        {"request": {"query": "will fail"}, "query": "will fail", "effective": {}},
    )
    job = JobStore(lambda: connect(migrated_dsn)).get(accepted["job_id"])
    ConversationTurnExecutor.on_failure(db, job, RuntimeError("provider failed"))

    transcript = store.get(conversation_id)
    assert transcript["pending_request_id"] is None
    request = db.execute(
        "SELECT state FROM conversation_requests WHERE conversation_id=%s AND request_id=%s",
        (conversation_id, request_id),
    ).fetchone()
    assert request["state"] == "failed"


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


def test_experiment_identity_is_distinct_from_conversation_replay(db, collection):
    actor = Principal(
        workspace_key="development", issuer=None, subject="development",
        display_name="Development", role="admin", mode="development",
    )
    student = Principal(
        workspace_key="development", issuer=None, subject="student", role="student",
    )
    other = Principal(
        workspace_key="other-student", issuer=None, subject="other", role="admin",
    )
    store = ConversationStore(db, "development")
    conversation = store.create("Synthetic experiment")
    conversation_id = UUID(conversation["conversationId"])
    request_id = uuid4()
    snapshot = {
        "query": "what is CS 170?",
        "collectionId": str(collection),
        "apiKey": "secret-token",
    }
    assert store.begin(conversation_id, request_id, 0, snapshot) is None
    store.finish(conversation_id, request_id, {"type": "answer", "answer": "See the catalog."})
    turns_before = db.execute(
        "SELECT count(*) AS n FROM conversation_turns WHERE conversation_id = %s",
        (conversation_id,),
    ).fetchone()["n"]
    replayed = store.replay(conversation_id, request_id, snapshot)
    assert replayed["answer"] == "See the catalog."
    assert db.execute("SELECT count(*) AS n FROM debug_experiments").fetchone()["n"] == 0

    with pytest.raises(HTTPException) as student_denied:
        ExperimentService.start(db, student, conversation_id, request_id, snapshot)
    assert student_denied.value.status_code == 403
    with pytest.raises(HTTPException) as other_denied:
        ExperimentService.start(db, other, conversation_id, request_id, snapshot)
    assert other_denied.value.status_code == 404

    experiment = ExperimentService.start(db, actor, conversation_id, request_id, snapshot)
    assert experiment["experiment_id"] != conversation_id
    assert experiment["state"] == "recorded"
    stored = db.execute(
        "SELECT snapshot FROM debug_experiments WHERE experiment_id = %s",
        (experiment["experiment_id"],),
    ).fetchone()["snapshot"]
    assert "apiKey" not in stored
    assert "secret-token" not in json.dumps(stored)
    assert db.execute(
        "SELECT count(*) AS n FROM conversation_turns WHERE conversation_id = %s",
        (conversation_id,),
    ).fetchone()["n"] == turns_before

    client = api_client(db, collection)
    recorded = client.post("/api/debug/experiments", json={
        "conversationId": str(conversation_id),
        "requestId": str(request_id),
        "snapshot": snapshot,
    })
    assert recorded.status_code == 200, recorded.text
    body = recorded.json()
    assert body["experimentId"] != body["sourceConversationId"]
    assert body["state"] == "recorded"
    assert db.execute(
        "SELECT count(*) AS n FROM conversation_turns WHERE conversation_id = %s",
        (conversation_id,),
    ).fetchone()["n"] == turns_before

    bare_id = uuid4()
    bare = {"query": "   ", "collectionId": str(collection)}
    assert store.begin(conversation_id, bare_id, 2, bare) is None
    store.finish(conversation_id, bare_id, {"type": "answer", "answer": "Saved."})
    experiments = db.execute("SELECT count(*) AS n FROM debug_experiments").fetchone()["n"]
    with pytest.raises(HTTPException) as missing:
        ExperimentService.start(db, actor, conversation_id, bare_id, bare)
    assert missing.value.status_code == 409
    assert "not replayable" in missing.value.detail

    pending_id = uuid4()
    assert store.begin(conversation_id, pending_id, 4, {"query": "later"}) is None
    with pytest.raises(HTTPException) as pending:
        ExperimentService.start(db, actor, conversation_id, pending_id, {"query": "later"})
    assert pending.value.status_code == 409
    assert "not replayed" in pending.value.detail
    with pytest.raises(HTTPException):
        store.replay(conversation_id, pending_id, {"query": "later"})
    assert db.execute("SELECT count(*) AS n FROM debug_experiments").fetchone()["n"] == experiments
