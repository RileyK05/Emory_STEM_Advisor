-- name: admin_overview
SELECT
    (SELECT count(*) FROM app_users) AS users,
    (SELECT count(*) FROM app_users WHERE role = 'admin') AS admins,
    (SELECT count(*) FROM app_users WHERE status = 'disabled') AS disabled_users,
    (SELECT count(*) FROM auth_sessions
      WHERE revoked_at IS NULL AND expires_at > now()) AS active_sessions,
    (SELECT count(*) FROM conversations) AS conversations,
    (SELECT count(*) FROM conversation_turns) AS turns,
    (SELECT count(*) FROM jobs WHERE status = 'queued') AS queued_jobs,
    (SELECT count(*) FROM jobs WHERE status = 'running') AS running_jobs,
    (SELECT count(*) FROM jobs WHERE status = 'failed') AS failed_jobs,
    (SELECT count(*) FROM sources) AS sources,
    (SELECT count(*) FROM artifacts) AS artifacts,
    (SELECT count(*) FROM retrieval_traces) AS traces;

-- name: admin_list_users
SELECT u.user_id, u.issuer, u.subject, u.workspace_key, u.display_name, u.email,
       u.role, u.status, u.created_at, u.updated_at, u.last_seen_at,
       (SELECT count(*) FROM conversations c
         WHERE c.workspace_key = u.workspace_key) AS conversation_count,
       (SELECT count(*) FROM auth_sessions s
         WHERE s.workspace_key = u.workspace_key AND s.revoked_at IS NULL
           AND s.expires_at > now()) AS active_sessions
FROM app_users u
WHERE (%(query_like)s = ''
       OR lower(coalesce(u.email, '')) LIKE %(query_like)s
       OR lower(coalesce(u.display_name, '')) LIKE %(query_like)s
       OR u.subject = %(query_exact)s
       OR u.workspace_key = %(query_exact)s)
  AND (%(role)s = '' OR u.role = %(role)s)
  AND (%(status)s = '' OR u.status = %(status)s)
ORDER BY u.created_at DESC
LIMIT %(limit)s OFFSET %(offset)s;

-- name: admin_get_user
SELECT user_id, issuer, subject, workspace_key, display_name, email, role, status,
       created_at, updated_at, last_seen_at
FROM app_users WHERE user_id = %(user_id)s;

-- name: admin_lock_user
SELECT user_id, issuer, subject, workspace_key, display_name, email, role, status,
       created_at, updated_at, last_seen_at
FROM app_users WHERE user_id = %(user_id)s FOR UPDATE;

-- name: admin_update_user
UPDATE app_users
SET role = %(role)s, status = %(status)s, updated_at = now()
WHERE user_id = %(user_id)s
RETURNING user_id, issuer, subject, workspace_key, display_name, email, role, status,
          created_at, updated_at, last_seen_at;

-- name: admin_lock_user_table
-- Serialize the last-active-admin invariant across concurrent updates.
LOCK TABLE app_users IN SHARE ROW EXCLUSIVE MODE;

-- name: admin_count_active_admins
SELECT count(*) AS admins FROM app_users
WHERE role = 'admin' AND status = 'active';

-- name: admin_revoke_workspace_sessions
UPDATE auth_sessions SET revoked_at = now()
WHERE workspace_key = %(workspace_key)s AND revoked_at IS NULL
RETURNING session_hash;

-- name: admin_insert_audit
INSERT INTO admin_audit (
    audit_id, actor_user_id, actor_workspace, action, target_type, target_id, detail
) VALUES (
    %(audit_id)s, %(actor_user_id)s, %(actor_workspace)s, %(action)s,
    %(target_type)s, %(target_id)s, %(detail)s
);

-- name: admin_list_audit_filtered
SELECT a.audit_id, a.actor_user_id, a.actor_workspace, a.action, a.target_type,
       a.target_id, a.detail, a.created_at, u.email AS actor_email,
       u.display_name AS actor_display_name
FROM admin_audit a
LEFT JOIN app_users u ON u.user_id = a.actor_user_id
WHERE (%(action)s = '' OR a.action = %(action)s)
  AND (%(target_type)s = '' OR a.target_type = %(target_type)s)
  AND (%(target_id)s = '' OR a.target_id = %(target_id)s)
  AND (%(actor)s = ''
       OR a.actor_workspace = %(actor)s
       OR (%(actor_user_id)s::uuid IS NOT NULL AND a.actor_user_id = %(actor_user_id)s::uuid)
       OR lower(coalesce(u.email, '')) LIKE %(actor_like)s
       OR lower(coalesce(u.display_name, '')) LIKE %(actor_like)s)
  AND (%(since)s::timestamptz IS NULL OR a.created_at >= %(since)s::timestamptz)
  AND (%(until)s::timestamptz IS NULL OR a.created_at <= %(until)s::timestamptz)
  AND (%(cursor_created_at)s::timestamptz IS NULL
       OR (a.created_at, a.audit_id) < (%(cursor_created_at)s::timestamptz, %(cursor_audit_id)s::uuid))
ORDER BY a.created_at DESC, a.audit_id DESC
LIMIT %(limit)s;

-- name: admin_list_jobs
SELECT job_id, kind, scope_key, status, attempt_count, max_attempts,
       cancel_requested, error, created_at, updated_at, finished_at
FROM jobs
WHERE (%(status)s = '' OR status = %(status)s)
ORDER BY created_at DESC
LIMIT %(limit)s OFFSET %(offset)s;

-- name: admin_list_sources
SELECT source_id, collection_id, filename, content_type, status, workspace_key,
       index_revision, is_current, created_at, updated_at
FROM sources
WHERE (%(workspace)s = '' OR workspace_key = %(workspace)s)
ORDER BY created_at DESC
LIMIT %(limit)s OFFSET %(offset)s;

-- name: admin_list_user_sessions
SELECT created_at, expires_at, revoked_at
FROM auth_sessions
WHERE workspace_key = %(workspace_key)s
ORDER BY created_at DESC
LIMIT 100;
