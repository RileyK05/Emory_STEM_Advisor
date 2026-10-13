CREATE TABLE provider_batches (
    batch_id uuid PRIMARY KEY,
    workspace_key text NOT NULL CHECK (length(workspace_key) BETWEEN 1 AND 200),
    provider text NOT NULL CHECK (length(provider) BETWEEN 1 AND 80),
    model_id text NOT NULL CHECK (length(model_id) BETWEEN 1 AND 200),
    profile_id uuid,
    profile_revision integer,
    request_fingerprint char(64) NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
    operation_limits jsonb NOT NULL,
    state text NOT NULL CHECK (state IN (
        'prepared', 'submitting', 'pending', 'validating', 'in_progress', 'finalizing',
        'completed', 'failed', 'expired', 'cancel_requested', 'cancelling', 'cancelled', 'unknown'
    )),
    provider_batch_id text,
    input_file_id text,
    submission_token uuid,
    submission_lease_until timestamptz,
    cancel_requested boolean NOT NULL DEFAULT false,
    total_count integer,
    completed_count integer,
    failed_count integer,
    failure_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    CHECK ((profile_id IS NULL) = (profile_revision IS NULL)),
    CHECK (profile_revision IS NULL OR profile_revision > 0),
    CHECK ((state = 'submitting') = (submission_token IS NOT NULL)),
    CHECK ((state = 'submitting') = (submission_lease_until IS NOT NULL)),
    CHECK (total_count IS NULL OR total_count >= 0),
    CHECK (completed_count IS NULL OR completed_count >= 0),
    CHECK (failed_count IS NULL OR failed_count >= 0)
);
CREATE UNIQUE INDEX provider_batches_remote_id_idx
    ON provider_batches (provider, provider_batch_id) WHERE provider_batch_id IS NOT NULL;
CREATE INDEX provider_batches_workspace_idx ON provider_batches (workspace_key, created_at DESC);

CREATE TABLE provider_batch_items (
    batch_id uuid NOT NULL REFERENCES provider_batches ON DELETE CASCADE,
    workspace_key text NOT NULL CHECK (length(workspace_key) BETWEEN 1 AND 200),
    correlation_id text NOT NULL CHECK (length(correlation_id) BETWEEN 1 AND 256),
    operation_id uuid NOT NULL UNIQUE REFERENCES operations,
    request_fingerprint char(64) NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
    request_json jsonb NOT NULL,
    state text NOT NULL CHECK (state IN ('reserved', 'received', 'completed', 'failed', 'cancelled', 'unknown')),
    result_json jsonb,
    error_code text,
    usage_source text NOT NULL DEFAULT 'unknown' CHECK (usage_source IN ('provider', 'estimated', 'unknown')),
    prompt_tokens integer,
    completion_tokens integer,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (batch_id, correlation_id),
    CHECK (prompt_tokens IS NULL OR prompt_tokens >= 0),
    CHECK (completion_tokens IS NULL OR completion_tokens >= 0),
    CHECK ((prompt_tokens IS NULL) = (completion_tokens IS NULL)),
    CHECK (result_json IS NULL OR octet_length(result_json::text) <= 1_000_000)
);
CREATE INDEX provider_batch_items_operation_idx ON provider_batch_items (operation_id);
