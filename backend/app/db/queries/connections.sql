-- name: insert_connection_profile
INSERT INTO connection_profiles (
    profile_id, name, provider, model_id, base_url, account_id, secret_ciphertext,
    provider_options, roles, capabilities, data_scopes, enabled, priority, revision
) VALUES (
    %(profile_id)s, %(name)s, %(provider)s, %(model_id)s, %(base_url)s, %(account_id)s,
    %(secret_ciphertext)s, %(provider_options)s, %(roles)s, %(capabilities)s,
    %(data_scopes)s, %(enabled)s, %(priority)s, 1
)
RETURNING *;

-- name: get_connection_profile
SELECT * FROM connection_profiles WHERE profile_id = %(profile_id)s;

-- name: get_connection_profile_revision_for_share
SELECT enabled, revision FROM connection_profiles
WHERE profile_id = %(profile_id)s
FOR SHARE;

-- name: get_connection_profile_revision
SELECT enabled, revision FROM connection_profiles
WHERE profile_id = %(profile_id)s;

-- name: list_connection_profiles
SELECT * FROM connection_profiles ORDER BY priority, lower(name), profile_id;

-- name: update_connection_profile
UPDATE connection_profiles
SET name = %(name)s,
    provider = %(provider)s,
    model_id = %(model_id)s,
    base_url = %(base_url)s,
    account_id = %(account_id)s,
    provider_options = %(provider_options)s,
    roles = %(roles)s,
    capabilities = %(capabilities)s,
    data_scopes = %(data_scopes)s,
    enabled = %(enabled)s,
    priority = %(priority)s,
    secret_ciphertext = CASE WHEN provider <> %(provider)s THEN NULL ELSE secret_ciphertext END,
    revision = revision + 1,
    updated_at = %(now)s
WHERE profile_id = %(profile_id)s AND revision = %(expected_revision)s
RETURNING *;

-- name: replace_connection_profile_secret
UPDATE connection_profiles
SET secret_ciphertext = %(secret_ciphertext)s,
    revision = revision + 1,
    updated_at = %(now)s
WHERE profile_id = %(profile_id)s AND revision = %(expected_revision)s
RETURNING *;

-- name: clear_connection_profile_secret
UPDATE connection_profiles
SET secret_ciphertext = NULL,
    revision = revision + 1,
    updated_at = %(now)s
WHERE profile_id = %(profile_id)s AND revision = %(expected_revision)s
RETURNING *;

-- name: delete_connection_profile
DELETE FROM connection_profiles
WHERE profile_id = %(profile_id)s AND revision = %(expected_revision)s
RETURNING profile_id;

-- name: read_connection_profile_secrets
SELECT profile_id, revision, provider, secret_ciphertext FROM connection_profiles
WHERE secret_ciphertext IS NOT NULL ORDER BY profile_id;

-- name: rotate_connection_profile_secret
-- Re-encrypts the same credential under a new key. Not a configuration change, so
-- the revision (which evaluation approval and in-flight fences bind to) is kept.
UPDATE connection_profiles
SET secret_ciphertext = %(secret_ciphertext)s,
    updated_at = %(now)s
WHERE profile_id = %(profile_id)s AND revision = %(expected_revision)s
RETURNING *;

-- name: insert_connection_profile_evaluation
INSERT INTO connection_profile_evaluations (
    evaluation_id, profile_id, profile_revision, provider, model_id, suite_hash, status, metrics
)
SELECT %(evaluation_id)s, p.profile_id, p.revision, p.provider, p.model_id,
       %(suite_hash)s, %(status)s, %(metrics)s
FROM connection_profiles AS p
WHERE p.profile_id = %(profile_id)s
  AND p.revision = %(profile_revision)s
  AND p.provider = %(provider)s
  AND p.model_id = %(model_id)s
RETURNING *;

-- name: list_route_profiles
SELECT p.*,
       EXISTS (
           SELECT 1 FROM connection_profile_evaluations AS e
           WHERE e.profile_id = p.profile_id
             AND e.profile_revision = p.revision
             AND e.provider = p.provider
             AND e.model_id = p.model_id
             AND e.suite_hash = %(suite_hash)s
             AND e.status = 'passed'
       ) AS eval_passed
FROM connection_profiles AS p
WHERE p.enabled = true
ORDER BY p.priority, lower(p.name), p.profile_id;
