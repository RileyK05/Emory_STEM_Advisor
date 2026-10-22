"""Admin role boundaries, audited reads, and scoped debug overrides."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
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


def test_prompt_promotion_requires_review_and_a_fixture(db, collection):
    from app.admin.prompt_registry import PromptRegistry

    reviewer = Principal(
        workspace_key="development", issuer=None, subject="development",
        role="admin", mode="development",
    )
    student = Principal(
        workspace_key="development", issuer=None, subject="student", role="student",
    )
    calls = db.execute("SELECT count(*) AS n FROM model_calls").fetchone()["n"]
    with pytest.raises(HTTPException) as denied:
        PromptRegistry.register(db, student, name="answer", template_text="Cite the catalog.")
    assert denied.value.status_code == 403
    first = PromptRegistry.register(db, reviewer, name="answer", template_text="Cite the catalog.")
    same = PromptRegistry.register(db, reviewer, name="answer", template_text="Cite the catalog.")
    assert same["version_id"] == first["version_id"]
    second = PromptRegistry.register(
        db, reviewer, name="answer", template_text="Cite a different line.",
    )
    assert second["version_id"] != first["version_id"]
    with pytest.raises(HTTPException) as reused:
        PromptRegistry.register(
            db, reviewer, name="answer", template_text="Cite a different line.",
            version_id=first["version_id"],
        )
    assert reused.value.status_code == 409
    unchanged = db.execute(
        "SELECT template_text FROM prompt_versions WHERE version_id = %s",
        (first["version_id"],),
    ).fetchone()
    assert unchanged["template_text"] == "Cite the catalog."
    with pytest.raises(HTTPException) as unreviewed:
        PromptRegistry.promote(db, reviewer, version_id=first["version_id"], suite_name="catalog")
    assert unreviewed.value.status_code == 409
    assert PromptRegistry.approve(db, reviewer, first["version_id"])["review_state"] == "approved"
    with pytest.raises(HTTPException) as missing_fixture:
        PromptRegistry.promote(db, reviewer, version_id=first["version_id"], suite_name="catalog")
    assert missing_fixture.value.status_code == 409
    assert db.execute("SELECT count(*) AS n FROM prompt_promotions").fetchone()["n"] == 0
    PromptRegistry.put_fixture(db, reviewer, suite_name="catalog", fixture_text="CS 170 is listed.")
    promoted = PromptRegistry.promote(
        db, reviewer, version_id=first["version_id"], suite_name="catalog",
    )
    assert promoted["status"] == "published"
    again = PromptRegistry.promote(
        db, reviewer, version_id=first["version_id"], suite_name="catalog",
    )
    assert again["promotion_id"] == promoted["promotion_id"]
    assert db.execute("SELECT count(*) AS n FROM model_calls").fetchone()["n"] == calls
    client = api_client(db, collection)
    blocked = client.post(
        f"/api/admin/prompts/{second['version_id']}/promote",
        json={"suiteName": "catalog"},
    )
    assert blocked.status_code == 409, blocked.text


def test_promoted_answer_prompt_drives_grounded_answers(db, collection, tmp_path):
    from app.admin.prompt_registry import PromptRegistry
    from app.generation.answer import ANSWER_PROMPT_NAME, PROMPT_VERSION, SYSTEM_PROMPT

    reviewer = Principal(
        workspace_key="development", issuer=None, subject="development",
        role="admin", mode="development",
    )
    ingest_text(db, collection, tmp_path, "a.txt", "alpha beta gamma")
    provider = FakeProvider(text="The answer is alpha [1].")
    client = api_client(db, collection, provider=provider)
    query = {"query": "alpha", "collectionId": collection_name(db, collection)}

    def answered():
        response = client.post("/api/query", json=query)
        assert response.status_code == 200, response.text
        return db.execute(
            "SELECT prompt_version FROM responses WHERE trace_id = %s",
            (response.json()["trace"]["traceId"],),
        ).fetchone()["prompt_version"], provider.last_messages[0]["content"]

    assert answered() == (PROMPT_VERSION, SYSTEM_PROMPT)
    text = SYSTEM_PROMPT + " Prefer the newest catalog edition."
    version = PromptRegistry.register(db, reviewer, name=ANSWER_PROMPT_NAME, template_text=text)
    PromptRegistry.approve(db, reviewer, version["version_id"])
    PromptRegistry.put_fixture(db, reviewer, suite_name="answers", fixture_text="alpha")
    PromptRegistry.promote(db, reviewer, version_id=version["version_id"], suite_name="answers")
    db.commit()
    recorded, used = answered()
    assert used == text
    assert recorded == "registry-" + version["content_digest"][:16]


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


@pytest.mark.parametrize("role", ["student", "advisor", "auditor"])
def test_non_admin_is_rejected_from_admin_and_debug_surfaces(db, collection, role, monkeypatch):
    client = api_client(db, collection)
    principal = Principal("development", None, role, role=role)
    client.app.dependency_overrides[get_principal] = lambda: principal
    monkeypatch.setattr(client.app.state.auth_service, "resolve", lambda *a, **kw: principal)
    try:
        assert client.get("/api/session").json()["role"] == role
        assert client.get("/api/admin/overview").status_code == 403
        assert client.get("/api/debug/settings").status_code == 403
        if role == "auditor":
            assert client.get("/api/admin/audit").status_code == 200
            assert client.get("/api/admin/telemetry/usage").status_code == 200
            assert client.get("/api/admin/pricing/schedules").status_code == 200
            assert client.get("/api/conversations").status_code == 200
            assert client.post("/api/conversations", json={"title": "Denied"}).status_code == 403
            assert client.post("/api/query", json={"query": "Denied"}).status_code == 403
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


def test_admin_audit_filtering_and_keyset_cursor_pagination(db, collection):
    client = api_client(db, collection)
    actor_id = _app_user(db, email="admin1@emory.edu", role="admin")

    # Insert controlled audit rows
    for i in range(5):
        db.execute(queries.get("admin_insert_audit"), {
            "audit_id": uuid4(),
            "actor_user_id": actor_id,
            "actor_workspace": "development",
            "action": "filter.test.read" if i % 2 == 0 else "filter.test.write",
            "target_type": "artifact" if i < 3 else "user",
            "target_id": f"target-{i}",
            "detail": Jsonb({"index": i}),
        })
    db.commit()

    # Filter by action
    filtered_action = client.get("/api/admin/audit?action=filter.test.read")
    assert filtered_action.status_code == 200
    action_items = filtered_action.json()
    assert len(action_items) >= 3
    assert all(item["action"] == "filter.test.read" for item in action_items)

    # Filter by target_type
    filtered_target = client.get("/api/admin/audit?target_type=artifact")
    assert filtered_target.status_code == 200
    target_items = filtered_target.json()
    assert all(item["targetType"] == "artifact" for item in target_items)

    # Filter by target_id
    filtered_id = client.get("/api/admin/audit?target_id=target-1")
    assert filtered_id.status_code == 200
    assert len(filtered_id.json()) == 1
    assert filtered_id.json()[0]["targetId"] == "target-1"

    # Filter by actor email / workspace
    filtered_actor = client.get("/api/admin/audit?actor=admin1@emory.edu")
    assert filtered_actor.status_code == 200
    assert len(filtered_actor.json()) >= 5

    # Test keyset pagination with cursor
    p1 = client.get("/api/admin/audit?limit=2")
    assert p1.status_code == 200
    assert len(p1.json()) == 2
    cursor = p1.headers.get("X-Next-Cursor")
    assert cursor is not None

    p2 = client.get(f"/api/admin/audit?limit=2&cursor={cursor}")
    assert p2.status_code == 200
    assert len(p2.json()) == 2
    p1_ids = {row["auditId"] for row in p1.json()}
    p2_ids = {row["auditId"] for row in p2.json()}
    assert p1_ids.isdisjoint(p2_ids), "Keyset pagination must not duplicate items across pages"


def test_admin_audit_export_csv_and_json_with_formula_neutralization(db, collection):
    client = api_client(db, collection)
    actor_id = _app_user(db, email="admin2@emory.edu", role="admin")

    # Insert entries with spreadsheet formula attack payloads
    test_audit_id = uuid4()
    db.execute(queries.get("admin_insert_audit"), {
        "audit_id": test_audit_id,
        "actor_user_id": actor_id,
        "actor_workspace": "development",
        "action": "=CMD|' /C calc'!A0",
        "target_type": "exploit_test",
        "target_id": "+123456789",
        "detail": Jsonb({"note": "test-1"}),
    })
    db.execute(queries.get("admin_insert_audit"), {
        "audit_id": uuid4(),
        "actor_user_id": actor_id,
        "actor_workspace": "development",
        "action": "calc.eval",
        "target_type": "-PAYLOAD",
        "target_id": "@SUM(A1:A10)",
        "detail": Jsonb({"note": "test-2"}),
    })
    db.commit()

    # CSV Export
    csv_resp = client.get("/api/admin/audit/export?format=csv&actor=admin2@emory.edu")
    assert csv_resp.status_code == 200
    assert "text/csv" in csv_resp.headers["content-type"]
    assert "attachment; filename=" in csv_resp.headers["content-disposition"]
    assert "X-Snapshot-Until" in csv_resp.headers

    csv_text = csv_resp.text
    # Verify formula prefixes were neutralized with a leading single quote
    assert "'=CMD|' /C calc'!A0" in csv_text
    assert "'+123456789" in csv_text
    assert "'@SUM(A1:A10)" in csv_text
    assert "'-PAYLOAD" in csv_text

    # JSON Export
    json_resp = client.get(f"/api/admin/audit/export?format=json&target_id={test_audit_id}")
    assert json_resp.status_code == 200
    assert json_resp.headers["content-type"] == "application/json"

    # Verify that export logged an audit event without recursive churn
    all_audits = client.get("/api/admin/audit?action=audit.export").json()
    assert len(all_audits) >= 1
    assert all_audits[0]["action"] == "audit.export"


def test_admin_audit_cursor_invalid_fails_safely(db, collection):
    client = api_client(db, collection)
    bad1 = client.get("/api/admin/audit?cursor=invalid-cursor-string")
    assert bad1.status_code == 422
    assert "invalid audit cursor" in bad1.text

    bad2 = client.get("/api/admin/audit?cursor=bm9uLWJhc2U2NA==")
    assert bad2.status_code == 422
    assert "invalid audit cursor" in bad2.text


def test_admin_audit_concurrent_inserts_keyset_stability(db, collection):
    client = api_client(db, collection)
    actor_id = _app_user(db, email="admin3@emory.edu", role="admin")

    # Insert 4 items with distinct action prefix
    prefix = f"stability-{uuid4().hex[:8]}"
    initial_ids = []
    for i in range(4):
        aid = uuid4()
        initial_ids.append(str(aid))
        db.execute(queries.get("admin_insert_audit"), {
            "audit_id": aid,
            "actor_user_id": actor_id,
            "actor_workspace": "development",
            "action": f"{prefix}.action",
            "target_type": "test",
            "target_id": str(i),
            "detail": Jsonb({"i": i}),
        })
    db.commit()


    # Fetch page 1 (limit 2)
    p1 = client.get(f"/api/admin/audit?action={prefix}.action&limit=2")
    assert p1.status_code == 200
    cursor = p1.headers.get("X-Next-Cursor")
    assert cursor is not None
    p1_items = p1.json()
    assert len(p1_items) == 2

    # Simulate concurrent insert happening while user is reading page 1
    db.execute(queries.get("admin_insert_audit"), {
        "audit_id": uuid4(),
        "actor_user_id": actor_id,
        "actor_workspace": "development",
        "action": f"{prefix}.action",
        "target_type": "test",
        "target_id": "concurrent-new",
        "detail": Jsonb({}),
    })
    db.commit()

    # Fetch page 2 using the cursor from page 1
    p2 = client.get(f"/api/admin/audit?action={prefix}.action&limit=2&cursor={cursor}")
    assert p2.status_code == 200
    p2_items = p2.json()
    assert len(p2_items) == 2

    combined_ids = [row["auditId"] for row in p1_items] + [row["auditId"] for row in p2_items]
    assert len(set(combined_ids)) == 4, "No duplicate rows across pages despite concurrent insert"
    assert set(combined_ids) == set(initial_ids), "All 4 initial items were returned across the two pages"
