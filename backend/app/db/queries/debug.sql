-- name: debug_insert_session
INSERT INTO debug_sessions (
    debug_session_id, owner_user_id, owner_workspace, token_hash, overrides,
    system_prompt, expires_at
) VALUES (
    %(debug_session_id)s, %(owner_user_id)s, %(owner_workspace)s, %(token_hash)s,
    %(overrides)s, %(system_prompt)s, %(expires_at)s
);

-- name: debug_get_session
SELECT debug_session_id, owner_user_id, owner_workspace, overrides, system_prompt,
       expires_at, created_at, updated_at, revoked_at
FROM debug_sessions
WHERE token_hash = %(token_hash)s AND revoked_at IS NULL AND expires_at > now();

-- name: debug_get_session_by_id
SELECT debug_session_id, owner_user_id, owner_workspace, overrides, system_prompt,
       expires_at, created_at, updated_at, revoked_at
FROM debug_sessions
WHERE debug_session_id = %(debug_session_id)s
  AND owner_workspace = %(owner_workspace)s
  AND owner_user_id IS NOT DISTINCT FROM %(owner_user_id)s
  AND revoked_at IS NULL AND expires_at > now();

-- name: debug_update_session
UPDATE debug_sessions
SET overrides = %(overrides)s, system_prompt = %(system_prompt)s, updated_at = now()
WHERE debug_session_id = %(debug_session_id)s
  AND owner_workspace = %(owner_workspace)s
  AND revoked_at IS NULL AND expires_at > now()
RETURNING debug_session_id, owner_user_id, owner_workspace, overrides, system_prompt,
          expires_at, created_at, updated_at, revoked_at;

-- name: debug_revoke_session
UPDATE debug_sessions SET revoked_at = now(), updated_at = now()
WHERE debug_session_id = %(debug_session_id)s
  AND owner_workspace = %(owner_workspace)s
  AND revoked_at IS NULL
RETURNING debug_session_id;

-- name: debug_purge_expired
DELETE FROM debug_sessions
WHERE expires_at <= now() - interval '1 day'
   OR revoked_at <= now() - interval '1 day'
RETURNING 1;
