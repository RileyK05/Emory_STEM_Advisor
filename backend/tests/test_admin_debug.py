"""Admin role boundaries, audited reads, and scoped debug overrides."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from app.auth.dependencies import get_principal
from app.auth.models import Principal
from app.conversations.store import ConversationStore
from app.db import queries
from tests.fakes import FakeProvider
from tests.helpers import api_client, collection_name, ingest_text


def _app_user(db, *, email="target@emory.edu", role="student", status="active") -> UUID:
    user_id = uuid4()
    workspace_key = "oidc:" + user_id.hex
    db.execute(queries.get("upsert_app_user"), {
        "user_id": user_id, "issuer": "https://idp.example.test",
        "subject": "subject-" + user_id.hex, "workspace_key": workspace_key,
        "display_name": "Target User", "email": email, "bootstrap": False,
    })
    if role != "student" or status != "active":
        db.execute(queries.get("admin_update_user"), {
            "user_id": user_id, "role": role, "status": status,
        })
    db.commit()
    return user_id


def test_admin_lists_and_updates_users_with_an_audit_trail(db, collection):
    client = api_client(db, collection)
    user_id = _app_user(db, email="student@emory.edu")

    listed = client.get("/api/admin/users")
    assert listed.status_code == 200, listed.text
    assert [item["email"] for item in listed.json()] == ["student@emory.edu"]

    promoted = client.patch(f"/api/admin/users/{user_id}", json={
        "role": "admin", "status": "active",
    })
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["role"] == "admin"

    audit = client.get("/api/admin/audit").json()
    assert audit[0]["action"] == "user.role_status"
    assert audit[0]["targetId"] == str(user_id)
    assert audit[0]["detail"]["after"]["role"] == "admin"

    revoked = client.post(f"/api/admin/users/{user_id}/revoke-sessions")
    assert revoked.status_code == 200
    assert revoked.json() == {"revoked": 0}


def test_admin_cannot_remove_the_last_active_admin(db, collection):
    client = api_client(db, collection)
    user_id = _app_user(db, role="admin")
    response = client.patch(f"/api/admin/users/{user_id}", json={
        "role": "student", "status": "active",
    })
    assert response.status_code == 409
    assert "last active administrator" in response.text


def test_non_admin_is_rejected_from_admin_and_debug_surfaces(db, collection):
    client = api_client(db, collection)
    client.app.dependency_overrides[get_principal] = lambda: Principal(
        "development", None, "student", role="student")
    try:
        assert client.get("/api/admin/overview").status_code == 403
        assert client.get("/api/debug/settings").status_code == 403
    finally:
        client.app.dependency_overrides.pop(get_principal, None)


def test_admin_transcript_read_is_audited(db, collection):
    client = api_client(db, collection)
    store = ConversationStore(db, "development")
    conversation_id = UUID(store.create("Private chat")["conversationId"])
    request_id = uuid4()
    store.begin(conversation_id, request_id, 0, {
        "query": "hello", "request": {"query": "hello"},
        "effective": {"collectionId": "course-docs", "sourceIds": None},
    })
    store.finish(conversation_id, request_id, {"type": "answer", "answer": "Hi."})

    value = client.get(f"/api/admin/conversations/{conversation_id}/transcript")
    assert value.status_code == 200, value.text
    assert value.json()["conversation"]["title"] == "Private chat"
    assert value.json()["turns"][0]["text"] == "hello"

    audit = client.get("/api/admin/audit").json()
    assert audit[0]["action"] == "conversation.read"
    assert audit[0]["targetId"] == str(conversation_id)


def test_debug_session_applies_prompt_and_settings_only_with_its_token(db, collection, tmp_path):
    ingest_text(db, collection, tmp_path, "alpha.txt", "Alpha is documented.")
    provider = FakeProvider("Alpha is documented [1].")
    client = api_client(db, collection, provider=provider)

    registry = client.get("/api/debug/settings")
    assert registry.status_code == 200, registry.text
    names = {item["name"] for item in registry.json()["settings"]}
    assert "conversation_compaction_threshold" in names
    assert "llm_context_window_tokens" in names

    created = client.post("/api/debug/sessions", json={
        "overrides": {
            "settings": {"conversation_compaction_threshold": 0.25},
            "retrieval": {"fusion.cited_set_size": 3},
        },
        "systemPrompt": "Debug prompt for this session.",
    })
    assert created.status_code == 200, created.text
    token = created.json()["token"]
    headers = {"X-Debug-Session": token}

    current = client.get("/api/debug/session", headers=headers)
    assert current.status_code == 200, current.text
    assert current.json()["systemPrompt"] == "Debug prompt for this session."
    assert current.json()["overrides"]["settings"]["conversation_compaction_threshold"] == 0.25

    changed = client.patch("/api/debug/session", headers=headers, json={
        "overrides": {"settings": {"conversation_compaction_threshold": 0.3}},
        "systemPrompt": "Changed prompt for this session.",
    })
    assert changed.status_code == 200, changed.text
    assert changed.json()["systemPrompt"] == "Changed prompt for this session."

    answer = client.post("/api/query", json={
        "query": "alpha", "collectionId": collection_name(db, collection),
    }, headers=headers)
    assert answer.status_code == 200, answer.text
    assert provider.last_messages[0] == {
        "role": "system", "content": "Changed prompt for this session.",
    }

    made = client.post("/api/conversations", json={"title": "Debug chat"}).json()
    started = client.post(
        f"/api/conversations/{made['conversationId']}/turns/stream",
        json={"query": "alpha", "collectionId": collection_name(db, collection),
              "requestId": str(uuid4()), "expectedSequence": 0},
        headers=headers,
    )
    assert started.status_code == 202, started.text
    row = db.execute(queries.get("get_conversation_request"), {
        "conversation_id": UUID(made["conversationId"]),
        "request_id": UUID(started.json()["requestId"]),
    }).fetchone()
    snapshot = row["input_snapshot"]
    assert snapshot["debug"]["settings"]["conversation_compaction_threshold"] == 0.3
    assert snapshot["debug"]["systemPrompt"] == "Changed prompt for this session."

    assert client.delete("/api/debug/session", headers=headers).status_code == 204
    assert client.get("/api/debug/session", headers=headers).status_code == 401


def test_debug_rejects_unknown_or_out_of_bounds_overrides(db, collection):
    client = api_client(db, collection)
    unknown = client.post("/api/debug/sessions", json={
        "overrides": {"settings": {"not_a_setting": 1}},
    })
    assert unknown.status_code == 422
    out_of_bounds = client.post("/api/debug/sessions", json={
        "overrides": {"settings": {"conversation_compaction_threshold": 2.0}},
    })
    assert out_of_bounds.status_code == 422


def test_admin_bootstrap_promotes_only_on_first_insert(db):
    values = {
        "issuer": "https://idp.example.test", "subject": "bootstrap-subject",
        "workspace_key": "oidc:bootstrap", "display_name": "Bootstrap",
        "email": "admin@emory.edu", "bootstrap": True,
    }
    first = db.execute(queries.get("upsert_app_user"), {"user_id": uuid4(), **values}).fetchone()
    assert first["role"] == "admin"
    second = db.execute(queries.get("upsert_app_user"), {
        "user_id": uuid4(), **values, "bootstrap": False,
    }).fetchone()
    assert second["role"] == "admin"
    db.execute(queries.get("admin_update_user"), {
        "user_id": first["user_id"], "role": "student", "status": "active",
    })
    third = db.execute(queries.get("upsert_app_user"), {
        "user_id": uuid4(), **values, "bootstrap": True,
    }).fetchone()
    assert third["role"] == "student"
    db.commit()


def test_admin_user_sessions_and_conversation_delete_are_audited(db, collection):
    client = api_client(db, collection)
    user_id = _app_user(db, email="sessions@emory.edu")
    workspace = db.execute(queries.get("admin_get_user"), {"user_id": user_id}).fetchone()[
        "workspace_key"]
    db.execute(
        "INSERT INTO auth_sessions (session_hash, issuer, subject, workspace_key, expires_at) "
        "VALUES (%(hash)s, 'issuer', 'subject', %(workspace)s, now() + interval '1 hour')",
        {"hash": b"session-one", "workspace": workspace},
    )
    db.commit()

    sessions = client.get(f"/api/admin/users/{user_id}/sessions")
    assert sessions.status_code == 200, sessions.text
    assert len(sessions.json()) == 1
    assert sessions.json()[0]["revokedAt"] is None

    store = ConversationStore(db, "development")
    conversation_id = UUID(store.create("Delete me")["conversationId"])
    request_id = uuid4()
    store.begin(conversation_id, request_id, 0, {
        "query": "hello", "request": {"query": "hello"},
        "effective": {"collectionId": "course-docs", "sourceIds": None},
    })
    store.finish(conversation_id, request_id, {"type": "answer", "answer": "Hi."})
    assert client.get(f"/api/admin/conversations/{conversation_id}/transcript").status_code == 200

    deleted = client.delete(f"/api/admin/conversations/{conversation_id}")
    assert deleted.status_code == 204, deleted.text
    assert client.get(f"/api/admin/conversations/{conversation_id}/transcript").status_code == 404
    audit = client.get("/api/admin/audit").json()
    assert any(item["action"] == "conversation.deleted" for item in audit)
    assert any(item["action"] == "conversation.read" for item in audit)

    stream_conversation = UUID(store.create("Stream delete")["conversationId"])
    stream_request = uuid4()
    started = store.begin_stream(stream_conversation, stream_request, 0, {
        "request": {"query": "hello", "requestId": str(stream_request)},
        "query": "hello",
        "effective": {"collectionId": "course-docs", "sourceIds": None},
    })
    assert client.delete(f"/api/admin/conversations/{stream_conversation}").status_code == 204
    assert db.execute(queries.get("get_job"), {"job_id": started["job_id"]}).fetchone() is None


def test_admin_cancels_a_queued_job(db, collection):
    client = api_client(db, collection)
    job_id = uuid4()
    db.execute(queries.get("insert_job"), {
        "job_id": job_id, "kind": "conversation.turn", "scope_key": "development",
        "idempotency_key": "admin-cancel-test", "payload": Jsonb({}),
        "max_attempts": 1, "available_at": datetime.now(UTC),
    })
    db.commit()

    listed = client.get("/api/admin/jobs")
    assert listed.status_code == 200, listed.text
    assert listed.json()[0]["cancelRequested"] is False

    cancelled = client.post(f"/api/admin/jobs/{job_id}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["cancelRequested"] is False
    assert any(item["action"] == "job.cancel" for item in client.get("/api/admin/audit").json())


def test_admin_collection_crud(db, collection):
    client = api_client(db, collection)
    created = client.post("/api/admin/collections", json={"name": "advisor-test-collection"})
    assert created.status_code == 201, created.text
    collection_id = created.json()["collectionId"]
    assert created.json()["sourceCount"] == 0

    renamed = client.patch(f"/api/admin/collections/{collection_id}",
                           json={"name": "advisor-test-renamed"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "advisor-test-renamed"

    deleted = client.delete(f"/api/admin/collections/{collection_id}")
    assert deleted.status_code == 204, deleted.text
    assert collection_id not in [item["collectionId"] for item in
                                 client.get("/api/admin/collections").json()]
    default_name = client.app.state.settings.default_collection
    default = next((item for item in client.get("/api/admin/collections").json()
                    if item["name"] == default_name), None)
    if default is None:
        created_default = client.post("/api/admin/collections", json={"name": default_name})
        assert created_default.status_code == 201, created_default.text
        default = created_default.json()
    assert client.delete(f"/api/admin/collections/{default['collectionId']}").status_code == 409
    assert client.get("/api/admin/artifacts").status_code == 200
