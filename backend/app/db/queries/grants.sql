-- name: insert_resource_grant
INSERT INTO resource_grants (
    grant_id, grant_type, actor_user_id, target_workspace_key, resource_type,
    resource_id, allowed_actions, reason, approved_by_user_id, expires_at
) VALUES (
    %(grant_id)s, %(grant_type)s, %(actor_user_id)s, %(target_workspace_key)s, %(resource_type)s,
    %(resource_id)s, %(allowed_actions)s, %(reason)s, %(approved_by_user_id)s, %(expires_at)s
) RETURNING
    grant_id, grant_type, actor_user_id, target_workspace_key, resource_type,
    resource_id, allowed_actions, reason, approved_by_user_id, expires_at,
    created_at, revoked_at, revoked_by_user_id;

-- name: get_resource_grant
SELECT
    g.grant_id, g.grant_type, g.actor_user_id, g.target_workspace_key, g.resource_type,
    g.resource_id, g.allowed_actions, g.reason, g.approved_by_user_id, g.expires_at,
    g.created_at, g.revoked_at, g.revoked_by_user_id,
    u.email AS actor_email, u.display_name AS actor_display_name,
    a.email AS approved_by_email, a.display_name AS approved_by_display_name
FROM resource_grants g
JOIN app_users u ON u.user_id = g.actor_user_id
JOIN app_users a ON a.user_id = g.approved_by_user_id
WHERE g.grant_id = %(grant_id)s;

-- name: list_resource_grants
SELECT
    g.grant_id, g.grant_type, g.actor_user_id, g.target_workspace_key, g.resource_type,
    g.resource_id, g.allowed_actions, g.reason, g.approved_by_user_id, g.expires_at,
    g.created_at, g.revoked_at, g.revoked_by_user_id,
    u.email AS actor_email, u.display_name AS actor_display_name,
    a.email AS approved_by_email, a.display_name AS approved_by_display_name
FROM resource_grants g
JOIN app_users u ON u.user_id = g.actor_user_id
JOIN app_users a ON a.user_id = g.approved_by_user_id
WHERE (%(target_workspace_key)s = '' OR g.target_workspace_key = %(target_workspace_key)s)
  AND (%(actor_user_id)s::uuid IS NULL OR g.actor_user_id = %(actor_user_id)s)
  AND (%(grant_type)s = '' OR g.grant_type = %(grant_type)s)
  AND (%(active_only)s IS FALSE OR (g.revoked_at IS NULL AND g.expires_at > now()))
ORDER BY g.created_at DESC
LIMIT %(limit)s OFFSET %(offset)s;

-- name: revoke_resource_grant
UPDATE resource_grants
SET revoked_at = now(), revoked_by_user_id = %(revoked_by_user_id)s
WHERE grant_id = %(grant_id)s AND revoked_at IS NULL
RETURNING
    grant_id, grant_type, actor_user_id, target_workspace_key, resource_type,
    resource_id, allowed_actions, reason, approved_by_user_id, expires_at,
    created_at, revoked_at, revoked_by_user_id;

-- name: check_active_resource_grant
SELECT grant_id, grant_type, allowed_actions
FROM resource_grants
WHERE actor_user_id = %(actor_user_id)s
  AND target_workspace_key = %(target_workspace_key)s
  AND resource_type = %(resource_type)s
  AND (resource_id IS NULL OR resource_id = %(resource_id)s::text)
  AND %(action)s = ANY(allowed_actions)
  AND (grant_type <> 'support_access' OR %(action)s = 'read')
  AND revoked_at IS NULL
  AND expires_at > now()
  AND EXISTS (
      SELECT 1 FROM app_users recipient WHERE recipient.user_id = actor_user_id
        AND recipient.status = 'active'
        AND ((grant_type = 'advisor_access' AND recipient.role IN ('advisor', 'admin'))
             OR (grant_type = 'support_access' AND recipient.role = 'admin'))
  )
LIMIT 1;

-- name: workspace_execution_user
SELECT user_id, role, status FROM app_users WHERE workspace_key = %(workspace_key)s;

-- name: lock_workspace_execution_user
SELECT user_id, role, status FROM app_users WHERE workspace_key = %(workspace_key)s FOR SHARE;

-- name: insert_support_grant_request
INSERT INTO support_grant_requests (
    request_id, requester_user_id, actor_user_id, target_workspace_key, resource_type,
    resource_id, allowed_actions, reason, expires_at
) VALUES (
    %(request_id)s, %(requester_user_id)s, %(actor_user_id)s, %(target_workspace_key)s,
    %(resource_type)s, %(resource_id)s, %(allowed_actions)s, %(reason)s, %(expires_at)s
) RETURNING request_id;

-- name: get_support_grant_request
SELECT
    r.request_id, r.requester_user_id, r.actor_user_id, r.target_workspace_key,
    r.resource_type, r.resource_id, r.allowed_actions, r.reason, r.expires_at,
    r.status, r.revision, r.decision_note, r.decided_by_user_id, r.decided_at,
    r.grant_id, r.created_at, r.updated_at,
    u.email AS actor_email, u.display_name AS actor_display_name
FROM support_grant_requests r
JOIN app_users u ON u.user_id = r.actor_user_id
WHERE r.request_id = %(request_id)s;

-- name: get_support_grant_request_for_update
SELECT request_id, requester_user_id, actor_user_id, target_workspace_key, resource_type,
       resource_id, allowed_actions, reason, expires_at, status, revision
FROM support_grant_requests
WHERE request_id = %(request_id)s
FOR UPDATE;

-- name: list_support_grant_requests
SELECT
    r.request_id, r.requester_user_id, r.actor_user_id, r.target_workspace_key,
    r.resource_type, r.resource_id, r.allowed_actions, r.reason, r.expires_at,
    r.status, r.revision, r.decision_note, r.decided_by_user_id, r.decided_at,
    r.grant_id, r.created_at, r.updated_at,
    u.email AS actor_email, u.display_name AS actor_display_name
FROM support_grant_requests r
JOIN app_users u ON u.user_id = r.actor_user_id
WHERE (%(status)s = '' OR r.status = %(status)s)
ORDER BY r.created_at DESC
LIMIT %(limit)s OFFSET %(offset)s;

-- name: update_support_grant_request
UPDATE support_grant_requests
SET reason = %(reason)s,
    expires_at = %(expires_at)s,
    target_workspace_key = %(target_workspace_key)s,
    resource_type = %(resource_type)s,
    resource_id = %(resource_id)s,
    revision = revision + 1,
    updated_at = now()
WHERE request_id = %(request_id)s
  AND status = 'pending'
  AND revision = %(expected_revision)s
RETURNING request_id;

-- name: decide_support_grant_request
UPDATE support_grant_requests
SET status = %(status)s,
    decision_note = %(decision_note)s,
    decided_by_user_id = %(decided_by_user_id)s,
    decided_at = now(),
    grant_id = %(grant_id)s,
    revision = revision + 1,
    updated_at = now()
WHERE request_id = %(request_id)s
  AND status = 'pending'
  AND revision = %(expected_revision)s
RETURNING request_id;

-- name: find_active_support_grant
SELECT grant_id
FROM resource_grants
WHERE actor_user_id = %(actor_user_id)s
  AND target_workspace_key = %(target_workspace_key)s
  AND resource_type = %(resource_type)s
  AND (resource_id IS NULL OR resource_id = %(resource_id)s)
  AND 'read' = ANY(allowed_actions)
  AND grant_type = 'support_access'
  AND revoked_at IS NULL
  AND expires_at > now()
ORDER BY expires_at DESC
LIMIT 1;

-- name: lock_support_grant_for_publication
SELECT g.grant_id
FROM resource_grants g
JOIN app_users recipient ON recipient.user_id = g.actor_user_id
WHERE g.grant_id = %(grant_id)s
  AND g.revoked_at IS NULL
  AND g.expires_at > now()
  AND 'read' = ANY(g.allowed_actions)
  AND recipient.status = 'active'
  AND (
      (g.grant_type = 'advisor_access' AND recipient.role IN ('advisor', 'admin'))
      OR (g.grant_type = 'support_access' AND recipient.role = 'admin')
  )
LIMIT 1
FOR SHARE;

-- name: insert_granted_result_staging
INSERT INTO granted_result_staging (
    staging_id, grant_id, actor_user_id, target_workspace_key, resource_type, resource_id
) VALUES (
    %(staging_id)s, %(grant_id)s, %(actor_user_id)s, %(target_workspace_key)s,
    %(resource_type)s, %(resource_id)s
) RETURNING staging_id, grant_id, status, resource_id;

-- name: get_granted_result_staging_for_update
SELECT staging_id, grant_id, actor_user_id, target_workspace_key, resource_type, resource_id, status
FROM granted_result_staging
WHERE staging_id = %(staging_id)s
FOR UPDATE;

-- name: mark_granted_result_staging
UPDATE granted_result_staging
SET status = %(status)s,
    published_at = CASE WHEN %(status)s = 'published' THEN now() ELSE published_at END
WHERE staging_id = %(staging_id)s
RETURNING staging_id, grant_id, status, resource_id;

