from datetime import UTC, datetime, timedelta

from app.auth.housekeeping import purge_expired_auth

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


def _session(db, tag: bytes, *, expires: datetime, revoked: datetime | None = None):
    db.execute(
        "INSERT INTO auth_sessions (session_hash, issuer, subject, workspace_key, expires_at, revoked_at) "
        "VALUES (%s, 'iss', %s, %s, %s, %s)",
        (tag, tag.decode(), "ws:" + tag.decode(), expires, revoked),
    )


def _transaction(db, tag: bytes, *, expires: datetime):
    db.execute(
        "INSERT INTO auth_login_transactions (state_hash, nonce, code_verifier, expires_at) "
        "VALUES (%s, 'n', 'v', %s)", (tag, expires),
    )


def test_purge_removes_only_expired_and_long_revoked_rows(db):
    _session(db, b"active", expires=NOW + timedelta(days=1))
    _session(db, b"expired", expires=NOW - timedelta(minutes=1))
    _session(db, b"revoked-recent", expires=NOW + timedelta(days=1), revoked=NOW - timedelta(days=1))
    _session(db, b"revoked-old", expires=NOW + timedelta(days=1), revoked=NOW - timedelta(days=45))
    _transaction(db, b"tx-live", expires=NOW + timedelta(minutes=5))
    _transaction(db, b"tx-expired", expires=NOW - timedelta(minutes=5))
    db.commit()

    removed = purge_expired_auth(db, now=NOW)

    assert removed == {"transactions": 1, "sessions": 2}
    sessions = {bytes(r["session_hash"]) for r in db.execute("SELECT session_hash FROM auth_sessions")}
    transactions = {bytes(r["state_hash"]) for r in db.execute("SELECT state_hash FROM auth_login_transactions")}
    assert sessions == {b"active", b"revoked-recent"}
    assert transactions == {b"tx-live"}
    assert purge_expired_auth(db, now=NOW) == {"transactions": 0, "sessions": 0}  # idempotent

