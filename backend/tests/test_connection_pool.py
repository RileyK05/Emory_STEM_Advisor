import logging
from dataclasses import replace
from uuid import uuid4

import psycopg
import pytest
from psycopg_pool import PoolTimeout

from app.config import load_settings
from app.db import connection, queries
from app.db.connection import PooledConnection, close_pools, connector, pooled_connect
from app.llm.base import GenerationResult, ToolCall, Usage
from tests.helpers import collection_name, ingest_text, lenient_config


@pytest.fixture
def dsn(migrated_dsn, db):
    # `db` cleans committed rows; pools are closed so no idle connection outlives the test.
    yield migrated_dsn
    close_pools()


def _add_collection(conn, name):
    conn.execute(queries.get("insert_collection"), {"collection_id": uuid4(), "name": name})


def _count(conn, name):
    return conn.execute("SELECT count(*) AS n FROM collections WHERE name = %s", (name,)).fetchone()["n"]


def _borrow(dsn, size=3, timeout=2.0):
    return pooled_connect(dsn, max_size=size, timeout=timeout)


def test_borrowed_connection_returns_dict_rows_and_vectors(dsn):
    conn = _borrow(dsn)
    row = conn.execute("SELECT 1 AS one, '[1,2,3]'::vector AS v").fetchone()
    assert row["one"] == 1
    assert type(row["v"]).__name__ == "Vector" and row["v"].to_list() == [1.0, 2.0, 3.0]
    conn.close()


def test_close_returns_the_connection_to_the_pool_and_is_idempotent(dsn):
    pool = connection._pool_for(dsn, 3, 2.0)
    first = _borrow(dsn)
    underlying = first._conn
    first.close()
    first.close()  # second close must not double-return
    assert not underlying.closed  # returned to the pool, not closed
    assert pool.get_stats()["pool_available"] >= 1
    second = _borrow(dsn)
    assert second.execute("SELECT 1 AS one").fetchone()["one"] == 1
    second.close()
    with pytest.raises(psycopg.OperationalError):
        first.execute("SELECT 1")


def test_with_block_commits_on_success_and_rolls_back_on_error(dsn):
    with _borrow(dsn) as conn:
        _add_collection(conn, 'pool-commit')
    with _borrow(dsn) as conn:
        assert _count(conn, 'pool-commit') == 1

    with pytest.raises(RuntimeError), _borrow(dsn) as conn:
        _add_collection(conn, 'pool-rollback')
        raise RuntimeError("boom")
    with _borrow(dsn) as conn:
        assert _count(conn, 'pool-rollback') == 0


def test_an_unfinished_transaction_never_leaks_to_the_next_borrower(dsn):
    leaked = _borrow(dsn, size=1)
    _add_collection(leaked, 'pool-leak')
    leaked.close()  # no commit: the pool must roll this back
    nxt = _borrow(dsn, size=1)
    assert _count(nxt, 'pool-leak') == 0
    nxt.close()


def test_exhausted_pool_times_out_then_recovers_when_a_connection_is_returned(dsn):
    held = _borrow(dsn, size=1, timeout=0.3)
    with pytest.raises(PoolTimeout):
        _borrow(dsn, size=1, timeout=0.3)
    held.close()
    again = _borrow(dsn, size=1, timeout=0.3)
    assert again.execute("SELECT 1 AS one").fetchone()["one"] == 1
    again.close()


def test_transaction_helper_works_through_the_wrapper(dsn):
    conn = _borrow(dsn)
    with conn.transaction():
        _add_collection(conn, 'pool-transaction')
    conn.close()
    with _borrow(dsn) as check:
        assert _count(check, 'pool-transaction') == 1


def test_connector_pools_by_default_and_falls_back_to_plain_connections_when_disabled(dsn):
    settings = replace(load_settings(), database_url=dsn, db_pool_max_size=2, db_pool_timeout_seconds=2.0)
    pooled = connector(settings)()
    assert isinstance(pooled, PooledConnection)
    pooled.close()

    plain = connector(replace(settings, db_pool_max_size=0))()
    try:
        assert isinstance(plain, psycopg.Connection)
    finally:
        plain.close()


def test_pool_settings_are_validated():
    with pytest.raises(ValueError, match="DB_POOL_MAX_SIZE"):
        replace(load_settings(), db_pool_max_size=-1)
    with pytest.raises(ValueError, match="DB_POOL_TIMEOUT_SECONDS"):
        replace(load_settings(), db_pool_timeout_seconds=0)


def test_returning_a_connection_after_a_read_only_query_does_not_log_a_pool_warning(dsn, caplog):
    conn = _borrow(dsn)
    conn.execute("SELECT 1")  # leaves the implicit transaction open, like most request handlers
    with caplog.at_level(logging.WARNING):
        conn.close()
    assert "rolling back returned connection" not in caplog.text


def test_api_requests_share_a_small_pool_and_return_every_connection(dsn):
    from fastapi.testclient import TestClient

    from app.api import create_app
    from tests.fakes import FakeEmbedder, FakeProvider

    settings = replace(load_settings(), database_url=dsn, db_pool_max_size=2, db_pool_timeout_seconds=5.0)
    app = create_app(settings=settings, allow_test_client=True,
                     embedder=FakeEmbedder(dim=8), provider=FakeProvider())
    client = TestClient(app)
    for _ in range(25):
        assert client.get("/api/collections").status_code == 200

    pool = connection._pool_for(dsn, 2, 5.0)  # the pool the requests actually used
    stats = pool.get_stats()
    assert stats["pool_size"] <= 2
    assert stats["requests_num"] >= 25            # every request borrowed from the pool
    assert stats["pool_available"] == stats["pool_size"]  # nothing leaked


def test_run_routes_do_not_hold_bootstrap_connection_while_job_store_borrows(dsn):
    from fastapi.testclient import TestClient

    from app.api import create_app
    from app.config import load_settings
    from tests.fakes import FakeEmbedder, FakeProvider

    settings = replace(load_settings(), database_url=dsn, db_pool_max_size=1,
                       db_pool_timeout_seconds=1.0)
    app = create_app(settings=settings, allow_test_client=True,
                     embedder=FakeEmbedder(dim=8), provider=FakeProvider())
    client = TestClient(app)
    made = client.post("/api/runs", json={
        "kind": "document", "inputs": {}, "idempotencyKey": "single-pool-run",
    })
    assert made.status_code == 200, made.text
    run_id = made.json()["runId"]
    assert client.get(f"/api/runs/{run_id}").json()["state"] == "queued"
    assert client.get(f"/api/runs/{run_id}/events").status_code == 200
    assert client.post(f"/api/runs/{run_id}/cancel").json()["state"] == "cancelled"
    pool = connection._pool_for(dsn, 1, 1.0)
    stats = pool.get_stats()
    assert stats["pool_size"] <= 1
    assert stats["pool_available"] == stats["pool_size"]


def test_conversation_execution_lease_does_not_block_job_control_pool(dsn):
    from app.jobs.store import JobStore
    from app.jobs.worker import JobContext

    settings = replace(load_settings(), database_url=dsn, db_pool_max_size=1,
                       db_pool_timeout_seconds=0.5)
    control = connector(settings)
    store = JobStore(control)
    job = store.create(kind="conversation.turn", scope_key="single-pool-worker",
                       idempotency_key="single-pool-control", payload={})
    claimed = store.claim("single-pool-worker")
    assert claimed is not None and claimed.job_id == job.job_id
    context = JobContext(store, claimed, 30)
    execution_conn = connector(settings, pool_name="conversation_execution")()
    try:
        assert context.heartbeat().lease_token == claimed.lease_token
        context.progress({"kind": "stage", "stage": "generating"})
        context.check_cancelled()
    finally:
        execution_conn.close()
    control_pool = connection._pool_for(dsn, 1, 0.5)
    execution_pool = connection._pool_for(dsn, 1, 0.5, "conversation_execution")
    assert control_pool.get_stats()["pool_size"] <= 1
    assert execution_pool.get_stats()["pool_size"] <= 1


def _pooled_client(dsn, *, provider=None, **overrides):
    from fastapi.testclient import TestClient

    from app.api import create_app
    from tests.fakes import FakeEmbedder

    settings = replace(load_settings(), database_url=dsn, db_pool_max_size=1,
                       db_pool_timeout_seconds=1.0, **overrides)
    app = create_app(settings=settings, allow_test_client=True, config=lenient_config(),
                     embedder=FakeEmbedder(dim=8), provider=provider)
    return TestClient(app)


def test_conversation_abandon_releases_the_request_borrow_before_job_cancel(dsn, db, collection):
    # RT-01: with a one-slot pool the abandon handler used to hold the request
    # connection while JobStore.cancel borrowed the same pool and self-waited.
    from tests.fakes import FakeProvider

    client = _pooled_client(dsn, provider=FakeProvider("unused"))
    made = client.post("/api/conversations", json={"title": "Abandon"}).json()
    conversation_id = made["conversationId"]
    request_id = str(uuid4())
    started = client.post(f"/api/conversations/{conversation_id}/turns/stream", json={
        "query": "alpha", "collectionId": collection_name(db, collection),
        "requestId": request_id, "expectedSequence": 0,
    })
    assert started.status_code == 202, started.text
    job_id = started.json()["jobId"]
    assert job_id is not None
    abandoned = client.post(f"/api/conversations/{conversation_id}/turns/{request_id}/abandon")
    assert abandoned.status_code == 200, abandoned.text
    assert abandoned.json()["type"] == "abandoned"
    job = db.execute(queries.get("get_job"), {"job_id": job_id}).fetchone()
    assert job is not None and job["status"] == "cancelled"
    stats = connection._pool_for(dsn, 1, 1.0).get_stats()
    assert stats["pool_available"] == stats["pool_size"]


class _OneToolProvider:
    """One calculator tool call, then a cited final answer."""

    def __init__(self):
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        if self.calls == 1:
            return GenerationResult(tool_calls=(ToolCall(
                "pool-calc", "advisor_calculate", {"expression": "1+1"}),),
                finish_reason="tool_calls", usage=Usage(20, 5, source="provider"))
        return GenerationResult(text="The result is 2 [1].", finish_reason="stop",
                                usage=Usage(30, 8, source="provider"))


def test_tool_query_does_not_self_starve_a_one_slot_pool(dsn, db, collection, tmp_path):
    # RT-02: the tool ledger used to borrow the same one-slot pool the request
    # held. A dedicated ledger role lets the tool call complete.
    ingest_text(db, collection, tmp_path, "alpha.txt", "Alpha has value 2.\n")
    db.commit()
    provider = _OneToolProvider()
    client = _pooled_client(db.info.dsn, tools_enabled=True, llm_provider="openai",
                            llm_model_id="mock-tools", provider=provider)
    response = client.post("/api/query", json={
        "query": "alpha", "collectionId": collection_name(db, collection),
    })
    assert response.status_code == 200, response.text
    assert response.json()["answer"] == "The result is 2 [1]."
    assert provider.calls == 2
    tool = db.execute("SELECT status FROM tool_executions").fetchone()
    assert tool["status"] == "completed"
