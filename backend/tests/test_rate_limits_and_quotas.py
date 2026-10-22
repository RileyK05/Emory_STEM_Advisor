"""Tests for per-student rate limits, daily quotas, concurrency limits, and 429 Retry-After handling (Item 27)."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.db import queries
from app.quotas.service import QuotaService
from tests.fakes import FakeProvider
from tests.helpers import api_client, collection_name


def test_minute_rate_limit_and_retry_after(db):
    ws = "ws-rate-minute"
    # Set workspace policy: 2 queries per minute
    QuotaService.upsert_policy(db, "workspace", ws, queries_per_minute=2, queries_per_day=100)

    # 1. First two queries succeed
    QuotaService.check_and_consume(db, workspace_key=ws)
    QuotaService.check_and_consume(db, workspace_key=ws)

    # 2. Third query fails with 429 and Retry-After header
    with pytest.raises(HTTPException) as exc:
        QuotaService.check_and_consume(db, workspace_key=ws)
    assert exc.value.status_code == 429
    assert "queries per minute" in exc.value.detail
    assert "Retry-After" in exc.value.headers
    retry_after = int(exc.value.headers["Retry-After"])
    assert 1 <= retry_after <= 60


def test_daily_quota_limit(db):
    ws = "ws-rate-daily"
    # Set workspace policy: 3 queries per day
    QuotaService.upsert_policy(db, "workspace", ws, queries_per_minute=100, queries_per_day=3)

    # 1. First three queries succeed
    for _ in range(3):
        QuotaService.check_and_consume(db, workspace_key=ws)

    # 2. Fourth query fails with 429
    with pytest.raises(HTTPException) as exc:
        QuotaService.check_and_consume(db, workspace_key=ws)
    assert exc.value.status_code == 429
    assert "Daily quota exceeded" in exc.value.detail
    assert "Retry-After" in exc.value.headers


def test_token_and_cost_buckets_use_their_own_daily_limits(db):
    ws = "ws-rate-token-cost"
    QuotaService.upsert_policy(
        db, "workspace", ws,
        queries_per_minute=1, queries_per_day=100,
        tokens_per_day=100, spend_usd_per_day=1,
    )
    # A token settlement is not compared with queries_per_minute.
    QuotaService.check_and_consume(
        db, workspace_key=ws, bucket_type="tokens", delta_count=60
    )
    with pytest.raises(HTTPException) as token_error:
        QuotaService.check_and_consume(
            db, workspace_key=ws, bucket_type="tokens", delta_count=60
        )
    assert "maximum 100 tokens" in token_error.value.detail

    QuotaService.check_and_consume(
        db, workspace_key=ws, bucket_type="cost_usd", delta_count=0, delta_amount=0.6
    )
    with pytest.raises(HTTPException) as cost_error:
        QuotaService.check_and_consume(
            db, workspace_key=ws, bucket_type="cost_usd", delta_count=0, delta_amount=0.6
        )
    assert "maximum 1 USD" in cost_error.value.detail


def test_partial_policy_update_preserves_unmentioned_limits(db):
    ws = "ws-policy-merge"
    QuotaService.upsert_policy(
        db, "workspace", ws, queries_per_minute=5, spend_usd_per_day=100
    )
    QuotaService.upsert_policy(db, "workspace", ws, queries_per_minute=6)
    policy = QuotaService.resolve_policy(db, workspace_key=ws, user_id=uuid4())
    assert policy.queries_per_minute == 6
    assert policy.spend_usd_per_day == 100


def test_query_endpoint_enforces_the_query_quota(db, collection):
    ws = "development"
    QuotaService.upsert_policy(
        db, "workspace", ws, queries_per_minute=100, queries_per_day=0
    )
    client = api_client(db, collection)
    response = client.post("/api/query", json={"query": "What are my requirements?"})
    assert response.status_code == 429
    assert "Daily quota exceeded" in response.json()["detail"]


def test_concurrent_requests_cannot_both_consume_the_last_query(db, migrated_dsn):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from types import SimpleNamespace

    from app.db.connection import connect

    workspace = "quota-last-query"
    QuotaService.upsert_policy(db, "workspace", workspace,
                               queries_per_minute=1, queries_per_day=1)
    first_incremented, second_checked = Event(), Event()

    class CoordinatedConnection:
        def __init__(self, conn, first):
            self.conn, self.first = conn, first

        def execute(self, query, params=None):
            # On the fixed path the second request waits for the first quota
            # transaction. On the old path it could read both empty buckets.
            if not self.first and query == queries.get("lock_workspace_quota"):
                second_checked.set()
            cursor = self.conn.execute(query, params)
            if self.first and query == queries.get("increment_quota_bucket"):
                first_incremented.set()
                assert second_checked.wait(10), "second request did not reach the quota check"
            if (not self.first and query == queries.get("get_quota_bucket")
                    and params["window_unit"] == "day"):
                row = cursor.fetchone()
                second_checked.set()
                return SimpleNamespace(fetchone=lambda: row)
            return cursor

        def commit(self):
            self.conn.commit()

    def consume(first):
        if not first:
            assert first_incremented.wait(10), "first request did not start its charge"
        with connect(migrated_dsn) as conn:
            try:
                QuotaService.check_and_consume(
                    CoordinatedConnection(conn, first), workspace_key=workspace)
                return 200
            except HTTPException as error:
                conn.rollback()
                return error.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(consume, True)
        second = executor.submit(consume, False)
        assert [first.result(timeout=20), second.result(timeout=20)] == [200, 429]
    assert _daily_query_count(db, workspace) == 1


def test_active_jobs_concurrency_limit(db):
    ws = "ws-rate-concurrency"
    # Set workspace policy: max 2 active jobs
    QuotaService.upsert_policy(db, "workspace", ws, max_active_jobs=2)

    # Simulate 2 queued/running jobs
    from datetime import UTC, datetime

    from psycopg.types.json import Jsonb

    for i in range(2):
        db.execute(
            queries.get("insert_job"),
            {
                "job_id": uuid4(),
                "kind": "artifact_generate",
                "scope_key": ws,
                "idempotency_key": str(uuid4()),
                "payload": Jsonb({"prompt": f"test {i}"}),
                "max_attempts": 3,
                "available_at": datetime.now(UTC),
            },
        )
    db.commit()

    # Attempting work with check_concurrency=True raises 429
    with pytest.raises(HTTPException) as exc:
        QuotaService.check_and_consume(db, workspace_key=ws, check_concurrency=True)
    assert exc.value.status_code == 429
    assert "Concurrent job capacity limit reached" in exc.value.detail
    assert exc.value.headers.get("Retry-After") == "5"


def test_effective_quota_endpoint(db, collection):
    client = api_client(db, collection)
    res = client.get("/api/quotas/effective")
    assert res.status_code == 200
    data = res.json()
    assert "remainingQueriesMinute" in data
    assert "remainingQueriesDay" in data
    assert "minuteQueryLimit" in data
    assert "dailyQueryLimit" in data
    assert "activeJobsLimit" in data
    assert "resetDaySeconds" in data
    assert data["dailyQueryLimit"] >= 100


def test_admin_quota_policy_crud_and_audit(db, collection):
    client = api_client(db, collection)

    # 1. List policies
    res = client.get("/api/admin/quotas")
    assert res.status_code == 200
    policies = res.json()
    assert len(policies) >= 1
    assert any(p["scopeType"] == "global" and p["scopeId"] == "default" for p in policies)

    # 2. Upsert policy for student cohort
    post_res = client.post(
        "/api/admin/quotas",
        json={
            "scopeType": "cohort",
            "scopeId": "power_students",
            "queriesPerMinute": 50,
            "queriesPerDay": 500,
            "tokensPerDay": 2000000,
            "spendUsdPerDay": 25.0,
            "maxActiveJobs": 5,
        },
    )
    assert post_res.status_code == 200
    p = post_res.json()
    assert p["scopeType"] == "cohort"
    assert p["scopeId"] == "power_students"
    assert p["queriesPerDay"] == 500

    # 3. Verify audit log
    audits = db.execute(
        queries.get("admin_list_audit"),
        {"limit": 10},
    ).fetchall()
    actions = {a["action"] for a in audits}
    assert "admin.quota_policy_upserted" in actions

    # Merely opening the list must preserve the administrator's global limits.
    changed = client.post("/api/admin/quotas", json={
        "scopeType": "global", "scopeId": "default",
        "queriesPerMinute": 7, "queriesPerDay": 17,
    })
    assert changed.status_code == 200, changed.text
    listed = client.get("/api/admin/quotas")
    assert listed.status_code == 200, listed.text
    global_policy = next(p for p in listed.json() if p["scopeType"] == "global")
    assert global_policy["queriesPerDay"] == 17
    assert client.get("/api/quotas/effective").json()["dailyQueryLimit"] == 17


@pytest.mark.parametrize("scope", ["user", "cohort"])
def test_stream_admission_honors_the_displayed_user_policy(db, collection, scope):
    from app.auth.dependencies import get_principal
    from app.auth.models import Principal

    user_id = uuid4()
    workspace = f"ws-quota-{user_id}"
    db.execute(queries.get("upsert_app_user"), {
        "user_id": user_id, "issuer": "https://auth.emory.edu", "subject": str(user_id),
        "workspace_key": workspace, "display_name": "Quota Student",
        "email": f"{user_id}@emory.edu", "bootstrap": False,
    })
    if scope == "cohort":
        db.execute(queries.get("add_user_cohort"), {
            "user_id": user_id, "cohort_name": "limited", "assigned_by": None,
        })
    QuotaService.upsert_policy(db, scope, str(user_id) if scope == "user" else "limited",
                               queries_per_minute=1, queries_per_day=1)
    client = api_client(db, collection)
    client.app.dependency_overrides[get_principal] = lambda: Principal(
        workspace_key=workspace, user_id=user_id, role="student", status="active",
        issuer="https://auth.emory.edu", subject=str(user_id),
    )
    assert client.get("/api/quotas/effective").json()["dailyQueryLimit"] == 1
    for expected_status in (202, 429):
        conversation = client.post("/api/conversations", json={"title": "quota"}).json()
        response = client.post(
            f"/api/conversations/{conversation['conversationId']}/turns/stream",
            json={"query": "alpha", "collectionId": collection_name(db, collection),
                  "requestId": str(uuid4()), "expectedSequence": 0},
        )
        assert response.status_code == expected_status, response.text
    assert _daily_query_count(db, workspace) == 1


def test_zero_quota_is_not_replaced_by_the_default(db, collection):
    ws = "ws-zero-quota"
    QuotaService.upsert_policy(
        db,
        "workspace",
        ws,
        queries_per_minute=0,
        queries_per_day=0,
        spend_usd_per_day=0,
    )
    response = api_client(db, collection).post("/api/admin/quotas", json={
        "scopeType": "workspace", "scopeId": ws,
        "queriesPerMinute": 0, "queriesPerDay": 0,
        "tokensPerDay": 0, "spendUsdPerDay": 0, "maxActiveJobs": 0,
    })
    assert response.status_code == 200, response.text
    policy = QuotaService.resolve_policy(db, workspace_key=ws)
    assert policy.queries_per_minute == 0
    assert policy.queries_per_day == 0
    assert policy.spend_usd_per_day == 0
    with pytest.raises(HTTPException) as exc:
        QuotaService.check_and_consume(db, workspace_key=ws)
    assert exc.value.status_code == 429


def _daily_query_count(db, workspace_key: str) -> int:
    day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    charged = db.execute(
        queries.get("get_quota_bucket"),
        {
            "scope_type": "workspace",
            "scope_id": workspace_key,
            "bucket_type": "queries",
            "window_unit": "day",
            "window_start": day,
        },
    ).fetchone()
    return 0 if charged is None else charged["count"]


def test_generation_retry_settles_quota_once(db, collection):
    ws = "development"
    QuotaService.upsert_policy(db, "workspace", ws, queries_per_minute=10, queries_per_day=10)
    client = api_client(db, collection, provider=FakeProvider("Alpha [1]."))
    made = client.post("/api/conversations", json={"title": "quota"}).json()
    path = "/api/conversations/" + made["conversationId"]
    request_id = str(uuid4())
    body = {
        "query": "alpha",
        "collectionId": collection_name(db, collection),
        "requestId": request_id,
        "expectedSequence": 0,
    }
    missing = client.post(
        path + "/turns/stream",
        json={**body, "requestId": str(uuid4()), "sourceIds": [str(uuid4())]},
    )
    assert missing.status_code == 404, missing.text
    rejected = client.post(
        path + "/turns/stream",
        json={**body, "expectedSequence": 1},
    )
    assert rejected.status_code == 409, rejected.text
    assert _daily_query_count(db, ws) == 0
    first = client.post(path + "/turns/stream", json=body)
    assert first.status_code == 202, first.text
    replay = client.post(path + "/turns/stream", json=body)
    assert replay.status_code == 202, replay.text
    assert replay.json()["jobId"] == first.json()["jobId"]
    assert _daily_query_count(db, ws) == 1
    second_made = client.post("/api/conversations", json={"title": "quota again"}).json()
    second_path = "/api/conversations/" + second_made["conversationId"]
    second = client.post(
        second_path + "/turns/stream",
        json={**body, "requestId": str(uuid4())},
    )
    assert second.status_code == 202, second.text
    assert _daily_query_count(db, ws) == 2

