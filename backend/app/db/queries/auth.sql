-- name: insert_auth_transaction
INSERT INTO auth_login_transactions (state_hash, nonce, code_verifier, expires_at)
VALUES (%(state_hash)s, %(nonce)s, %(code_verifier)s, %(expires_at)s);

-- name: consume_auth_transaction
DELETE FROM auth_login_transactions
WHERE state_hash = %(state_hash)s AND expires_at > %(now)s
RETURNING nonce, code_verifier;

-- name: insert_auth_session
INSERT INTO auth_sessions (
    session_hash, issuer, subject, workspace_key, display_name, email, expires_at
) VALUES (
    %(session_hash)s, %(issuer)s, %(subject)s, %(workspace_key)s,
    %(display_name)s, %(email)s, %(expires_at)s
);

-- name: get_auth_session
SELECT issuer, subject, workspace_key, display_name, email, expires_at
FROM auth_sessions
WHERE session_hash = %(session_hash)s AND revoked_at IS NULL AND expires_at > %(now)s;

-- name: revoke_auth_session
UPDATE auth_sessions SET revoked_at = %(now)s
WHERE session_hash = %(session_hash)s AND revoked_at IS NULL
RETURNING session_hash;

-- name: purge_auth_records
WITH expired_transactions AS (
    DELETE FROM auth_login_transactions WHERE expires_at <= %(now)s RETURNING 1
), expired_sessions AS (
    DELETE FROM auth_sessions WHERE expires_at <= %(now)s OR revoked_at < %(revoked_before)s RETURNING 1
)
SELECT (SELECT count(*) FROM expired_transactions) AS transactions,
       (SELECT count(*) FROM expired_sessions) AS sessions;
