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

-- name: upsert_app_user
INSERT INTO app_users (
    user_id, issuer, subject, workspace_key, display_name, email, role, last_seen_at
) VALUES (
    %(user_id)s, %(issuer)s, %(subject)s, %(workspace_key)s,
    %(display_name)s, %(email)s,
    CASE WHEN %(bootstrap)s THEN 'admin' ELSE 'student' END, now()
)
ON CONFLICT (issuer, subject) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    email = EXCLUDED.email,
    workspace_key = EXCLUDED.workspace_key,
    last_seen_at = now(),
    updated_at = now()
RETURNING user_id, role, status;

-- name: get_auth_session
SELECT s.issuer, s.subject, s.workspace_key, s.display_name, s.email,
       s.expires_at, u.user_id, COALESCE(u.role, 'student') AS role,
       COALESCE(u.status, 'active') AS status
FROM auth_sessions s
LEFT JOIN app_users u ON u.issuer = s.issuer AND u.subject = s.subject
WHERE s.session_hash = %(session_hash)s
  AND s.revoked_at IS NULL AND s.expires_at > %(now)s;

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
