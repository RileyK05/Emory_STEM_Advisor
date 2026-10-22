-- Per-student rate limits, daily quotas, concurrency limits, and policies (Item 27).

CREATE TABLE IF NOT EXISTS quota_buckets (
    scope_type text NOT NULL CHECK (scope_type IN ('global', 'user', 'workspace')),
    scope_id text NOT NULL,
    bucket_type text NOT NULL CHECK (bucket_type IN ('queries', 'tokens', 'cost_usd', 'active_jobs')),
    window_unit text NOT NULL CHECK (window_unit IN ('second', 'minute', 'hour', 'day')),
    window_start timestamptz NOT NULL,
    count bigint NOT NULL DEFAULT 0,
    amount double precision NOT NULL DEFAULT 0.0,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (scope_type, scope_id, bucket_type, window_unit, window_start)
);

CREATE INDEX IF NOT EXISTS idx_quota_buckets_lookup
    ON quota_buckets(scope_type, scope_id, bucket_type, window_unit, window_start);

CREATE TABLE IF NOT EXISTS quota_policies (
    policy_id uuid PRIMARY KEY,
    scope_type text NOT NULL CHECK (scope_type IN ('global', 'cohort', 'workspace', 'user')),
    scope_id text NOT NULL,
    queries_per_minute int,
    queries_per_day int,
    tokens_per_day int,
    spend_usd_per_day double precision,
    max_active_jobs int,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (scope_type, scope_id)
);

-- Prepopulate global default policy
INSERT INTO quota_policies (
    policy_id,
    scope_type,
    scope_id,
    queries_per_minute,
    queries_per_day,
    tokens_per_day,
    spend_usd_per_day,
    max_active_jobs
) VALUES (
    '00000000-0000-0000-0000-000000000027',
    'global',
    'default',
    30,
    300,
    1000000,
    10.00,
    3
) ON CONFLICT (scope_type, scope_id) DO NOTHING;

