-- name: admin_overview
SELECT
    (SELECT count(*) FROM app_users) AS users,
    (SELECT count(*) FROM app_users WHERE role = 'admin') AS admins,
    (SELECT count(*) FROM app_users WHERE status = 'disabled') AS disabled_users,
    (SELECT count(*) FROM auth_sessions
      WHERE revoked_at IS NULL AND expires_at > now()) AS active_sessions,
    (SELECT count(*) FROM conversations) AS conversations,
    (SELECT count(*) FROM conversation_turns) AS turns,
    (SELECT count(*) FROM conversation_summaries) AS summaries,
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

-- name: admin_list_audit
SELECT a.audit_id, a.actor_user_id, a.actor_workspace, a.action, a.target_type,
       a.target_id, a.detail, a.created_at, u.email AS actor_email,
       u.display_name AS actor_display_name
FROM admin_audit a
LEFT JOIN app_users u ON u.user_id = a.actor_user_id
ORDER BY a.created_at DESC
LIMIT %(limit)s;

-- name: admin_list_conversations
SELECT c.conversation_id, c.workspace_key, c.title, c.last_sequence,
       c.pending_request_id, c.created_at, c.updated_at,
       (SELECT count(*) FROM conversation_turns t
         WHERE t.conversation_id = c.conversation_id) AS turn_count,
       (SELECT count(*) FROM conversation_summaries s
         WHERE s.conversation_id = c.conversation_id) AS summary_count
FROM conversations c
WHERE (%(workspace)s = '' OR c.workspace_key = %(workspace)s)
  AND (%(query_like)s = '' OR lower(c.title) LIKE %(query_like)s)
ORDER BY c.updated_at DESC
LIMIT %(limit)s OFFSET %(offset)s;

-- name: admin_get_conversation
SELECT conversation_id, workspace_key, title, last_sequence, pending_request_id,
       created_at, updated_at
FROM conversations WHERE conversation_id = %(conversation_id)s;

-- name: admin_list_conversation_turns
SELECT sequence, request_id, role, text, status, evidence, created_at
FROM conversation_turns
WHERE conversation_id = %(conversation_id)s
ORDER BY sequence
LIMIT 1000;

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

-- name: admin_delete_conversation_turns
DELETE FROM conversation_turns WHERE conversation_id = %(conversation_id)s;

-- name: admin_delete_conversation_summaries
DELETE FROM conversation_summaries WHERE conversation_id = %(conversation_id)s;

-- name: admin_delete_conversation_requests
DELETE FROM conversation_requests
WHERE conversation_id = %(conversation_id)s
RETURNING job_id;

-- name: admin_delete_jobs
DELETE FROM jobs WHERE job_id = ANY(%(job_ids)s);

-- name: admin_delete_conversation
DELETE FROM conversations
WHERE conversation_id = %(conversation_id)s
RETURNING conversation_id;

-- name: admin_list_artifacts
SELECT a.artifact_id, a.workspace_key, a.kind, a.head_revision_id,
       a.original_filename, a.created_at,
       (SELECT count(*) FROM artifact_revisions r
         WHERE r.artifact_id = a.artifact_id) AS revision_count
FROM artifacts a
ORDER BY a.created_at DESC
LIMIT %(limit)s OFFSET %(offset)s;

-- name: admin_list_collections
SELECT c.collection_id, c.name, c.created_at,
       (SELECT count(*) FROM sources s
         WHERE s.collection_id = c.collection_id) AS source_count
FROM collections c
ORDER BY c.name
LIMIT %(limit)s OFFSET %(offset)s;

-- name: admin_get_collection
SELECT c.collection_id, c.name, c.created_at,
       (SELECT count(*) FROM sources s
         WHERE s.collection_id = c.collection_id) AS source_count
FROM collections c WHERE c.collection_id = %(collection_id)s;

-- name: admin_create_collection
INSERT INTO collections (collection_id, name)
VALUES (%(collection_id)s, %(name)s)
RETURNING collection_id, name, created_at;

-- name: admin_rename_collection
UPDATE collections SET name = %(name)s
WHERE collection_id = %(collection_id)s
RETURNING collection_id, name, created_at;

-- name: admin_collection_source_count
SELECT count(*) AS sources FROM sources WHERE collection_id = %(collection_id)s;

-- name: admin_collection_plan_count
SELECT count(*) AS plans FROM artifact_run_plans WHERE collection_id = %(collection_id)s;

-- name: admin_delete_collection
DELETE FROM collections
WHERE collection_id = %(collection_id)s
RETURNING collection_id;
