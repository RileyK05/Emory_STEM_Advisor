-- Feature flags and cohort rollout queries (Item 17).

-- name: feature_flag_workspace_user
SELECT user_id, role, status FROM app_users WHERE workspace_key = %(workspace_key)s;

-- name: lock_feature_flag
SELECT * FROM feature_flags WHERE flag_name = %(flag_name)s FOR UPDATE;

-- name: share_feature_flag
SELECT flag_name FROM feature_flags WHERE flag_name = %(flag_name)s FOR SHARE;

-- name: list_cohort_members
SELECT u.user_id, u.display_name, u.email, u.role, c.created_at
FROM user_cohorts c JOIN app_users u ON u.user_id = c.user_id
WHERE c.cohort_name = %(cohort_name)s
ORDER BY c.created_at, u.user_id LIMIT %(limit)s OFFSET %(offset)s;

-- name: list_feature_flags
SELECT
    f.flag_name,
    f.description,
    f.env_ceiling_key,
    f.default_enabled,
    f.kill_switch_active,
    f.kill_reason,
    f.created_at,
    f.updated_at,
    COUNT(o.override_id) AS overrides_count
FROM feature_flags f
LEFT JOIN feature_flag_overrides o ON f.flag_name = o.flag_name
GROUP BY f.flag_name, f.description, f.env_ceiling_key, f.default_enabled, f.kill_switch_active, f.kill_reason, f.created_at, f.updated_at
ORDER BY f.flag_name;

-- name: get_feature_flag
SELECT
    flag_name,
    description,
    env_ceiling_key,
    default_enabled,
    kill_switch_active,
    kill_reason,
    created_at,
    updated_at
FROM feature_flags
WHERE flag_name = %(flag_name)s;

-- name: seed_feature_flags
INSERT INTO feature_flags (flag_name, description, env_ceiling_key, default_enabled)
VALUES (%(flag_name)s, %(description)s, %(env_ceiling_key)s, %(default_enabled)s)
ON CONFLICT (flag_name) DO NOTHING;

-- name: set_feature_flag_kill_switch
UPDATE feature_flags
SET
    kill_switch_active = %(kill_switch_active)s,
    kill_reason = %(kill_reason)s,
    updated_at = clock_timestamp()
WHERE flag_name = %(flag_name)s
RETURNING flag_name, description, env_ceiling_key, default_enabled, kill_switch_active, kill_reason, updated_at;

-- name: list_feature_flag_overrides
SELECT
    override_id,
    flag_name,
    scope_type,
    scope_id,
    enabled,
    reason,
    actor_user_id,
    updated_at
FROM feature_flag_overrides
WHERE flag_name = %(flag_name)s
ORDER BY scope_type, scope_id;

-- name: upsert_feature_flag_override
INSERT INTO feature_flag_overrides (
    override_id,
    flag_name,
    scope_type,
    scope_id,
    enabled,
    reason,
    actor_user_id,
    updated_at
) VALUES (
    %(override_id)s,
    %(flag_name)s,
    %(scope_type)s,
    %(scope_id)s,
    %(enabled)s,
    %(reason)s,
    %(actor_user_id)s,
    clock_timestamp()
)
ON CONFLICT (flag_name, scope_type, scope_id)
DO UPDATE SET
    enabled = EXCLUDED.enabled,
    reason = EXCLUDED.reason,
    actor_user_id = EXCLUDED.actor_user_id,
    updated_at = clock_timestamp()
RETURNING override_id, flag_name, scope_type, scope_id, enabled, reason, actor_user_id, updated_at;

-- name: delete_feature_flag_override
DELETE FROM feature_flag_overrides
WHERE flag_name = %(flag_name)s AND scope_type = %(scope_type)s AND scope_id = %(scope_id)s;

-- name: get_user_cohorts
SELECT cohort_name
FROM user_cohorts
WHERE user_id = %(user_id)s
ORDER BY cohort_name;

-- name: list_cohorts
SELECT
    cohort_name,
    COUNT(DISTINCT user_id) AS member_count
FROM user_cohorts
GROUP BY cohort_name
ORDER BY cohort_name;

-- name: add_user_cohort
INSERT INTO user_cohorts (user_id, cohort_name, assigned_by)
VALUES (%(user_id)s, %(cohort_name)s, %(assigned_by)s)
ON CONFLICT (user_id, cohort_name) DO NOTHING;

-- name: remove_user_cohort
DELETE FROM user_cohorts
WHERE user_id = %(user_id)s AND cohort_name = %(cohort_name)s;
