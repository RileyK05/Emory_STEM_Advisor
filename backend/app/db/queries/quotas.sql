-- Quota buckets and policy management queries (Item 27).

-- name: seed_quota_policy
INSERT INTO quota_policies (
    policy_id, scope_type, scope_id, queries_per_minute, queries_per_day,
    tokens_per_day, spend_usd_per_day, max_active_jobs
) VALUES (
    %(policy_id)s, %(scope_type)s, %(scope_id)s, %(queries_per_minute)s, %(queries_per_day)s,
    %(tokens_per_day)s, %(spend_usd_per_day)s, %(max_active_jobs)s
)
ON CONFLICT (scope_type, scope_id) DO NOTHING;

-- name: quota_workspace_user
SELECT user_id FROM app_users WHERE workspace_key = %(workspace_key)s;

-- name: lock_workspace_quota
SELECT pg_advisory_xact_lock(hashtextextended('quota:' || %(workspace_key)s, 0));

-- name: get_quota_policy
SELECT policy_id, scope_type, scope_id, queries_per_minute, queries_per_day,
       tokens_per_day, spend_usd_per_day, max_active_jobs, created_at, updated_at
FROM quota_policies
WHERE scope_type = %(scope_type)s AND scope_id = %(scope_id)s;

-- name: list_quota_policies
SELECT policy_id, scope_type, scope_id, queries_per_minute, queries_per_day,
       tokens_per_day, spend_usd_per_day, max_active_jobs, created_at, updated_at
FROM quota_policies
ORDER BY scope_type, scope_id;

-- name: upsert_quota_policy
INSERT INTO quota_policies (
    policy_id, scope_type, scope_id, queries_per_minute, queries_per_day,
    tokens_per_day, spend_usd_per_day, max_active_jobs, updated_at
) VALUES (
    %(policy_id)s, %(scope_type)s, %(scope_id)s, %(queries_per_minute)s, %(queries_per_day)s,
    %(tokens_per_day)s, %(spend_usd_per_day)s, %(max_active_jobs)s, CURRENT_TIMESTAMP
)
ON CONFLICT (scope_type, scope_id)
DO UPDATE SET
    queries_per_minute = COALESCE(EXCLUDED.queries_per_minute, quota_policies.queries_per_minute),
    queries_per_day = COALESCE(EXCLUDED.queries_per_day, quota_policies.queries_per_day),
    tokens_per_day = COALESCE(EXCLUDED.tokens_per_day, quota_policies.tokens_per_day),
    spend_usd_per_day = COALESCE(EXCLUDED.spend_usd_per_day, quota_policies.spend_usd_per_day),
    max_active_jobs = COALESCE(EXCLUDED.max_active_jobs, quota_policies.max_active_jobs),
    updated_at = CURRENT_TIMESTAMP
RETURNING policy_id, scope_type, scope_id, queries_per_minute, queries_per_day,
          tokens_per_day, spend_usd_per_day, max_active_jobs, created_at, updated_at;

-- name: get_quota_bucket
SELECT count, amount
FROM quota_buckets
WHERE scope_type = %(scope_type)s
  AND scope_id = %(scope_id)s
  AND bucket_type = %(bucket_type)s
  AND window_unit = %(window_unit)s
  AND window_start = %(window_start)s;

-- name: increment_quota_bucket
INSERT INTO quota_buckets (
    scope_type, scope_id, bucket_type, window_unit, window_start, count, amount, updated_at
) VALUES (
    %(scope_type)s, %(scope_id)s, %(bucket_type)s, %(window_unit)s, %(window_start)s, %(delta_count)s, %(delta_amount)s, CURRENT_TIMESTAMP
)
ON CONFLICT (scope_type, scope_id, bucket_type, window_unit, window_start)
DO UPDATE SET
    count = quota_buckets.count + EXCLUDED.count,
    amount = quota_buckets.amount + EXCLUDED.amount,
    updated_at = CURRENT_TIMESTAMP
RETURNING count, amount;

-- name: count_active_workspace_jobs
SELECT COUNT(*) AS active_jobs
FROM jobs
WHERE scope_key = %(workspace_key)s
  AND status IN ('queued', 'running');

-- name: prune_expired_quota_buckets
DELETE FROM quota_buckets
WHERE window_start < %(cutoff)s;

-- name: insert_quota_settlement
INSERT INTO quota_settlements (
    workspace_key, request_id, bucket_type, delta_count, delta_amount
) VALUES (
    %(workspace_key)s, %(request_id)s, %(bucket_type)s, %(delta_count)s, %(delta_amount)s
)
ON CONFLICT (workspace_key, request_id, bucket_type) DO NOTHING
RETURNING workspace_key, request_id, bucket_type;

