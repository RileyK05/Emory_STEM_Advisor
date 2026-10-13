CREATE TABLE jobs (
    job_id uuid PRIMARY KEY,
    kind text NOT NULL CHECK (length(kind) BETWEEN 1 AND 100),
    scope_key text NOT NULL CHECK (length(scope_key) BETWEEN 1 AND 200),
    idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 200),
    payload jsonb NOT NULL,
    result jsonb,
    error jsonb,
    status text NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    max_attempts integer NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 20),
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_owner text,
    lease_token uuid UNIQUE,
    lease_expires_at timestamptz,
    heartbeat_at timestamptz,
    cancel_requested boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    UNIQUE (scope_key, idempotency_key),
    CHECK (attempt_count <= max_attempts),
    CHECK ((status = 'running') = (lease_token IS NOT NULL)),
    CHECK ((status = 'running') = (lease_expires_at IS NOT NULL)),
    CHECK ((status IN ('succeeded', 'failed', 'cancelled')) = (finished_at IS NOT NULL))
);
CREATE INDEX jobs_ready_idx ON jobs (available_at, created_at, job_id) WHERE status = 'queued';
CREATE INDEX jobs_expired_lease_idx ON jobs (lease_expires_at, job_id) WHERE status = 'running';

CREATE TABLE job_events (
    event_id bigserial PRIMARY KEY,
    job_id uuid NOT NULL REFERENCES jobs ON DELETE CASCADE,
    event_type text NOT NULL CHECK (length(event_type) BETWEEN 1 AND 80),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX job_events_order_idx ON job_events (job_id, event_id);
