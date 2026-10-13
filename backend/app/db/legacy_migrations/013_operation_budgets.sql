CREATE TABLE operations (
    operation_id uuid PRIMARY KEY,
    workspace_key text NOT NULL CHECK (length(workspace_key) BETWEEN 1 AND 200),
    started_at timestamptz NOT NULL,
    deadline_at timestamptz NOT NULL,
    max_calls integer NOT NULL CHECK (max_calls BETWEEN 1 AND 100),
    call_count integer NOT NULL DEFAULT 0 CHECK (call_count >= 0),
    total_token_budget integer NOT NULL CHECK (total_token_budget > 0),
    reserved_tokens integer NOT NULL DEFAULT 0 CHECK (reserved_tokens >= 0),
    accounted_tokens integer NOT NULL DEFAULT 0 CHECK (accounted_tokens >= 0),
    per_call_output_cap integer NOT NULL CHECK (per_call_output_cap > 0),
    per_call_timeout_seconds double precision NOT NULL CHECK (per_call_timeout_seconds > 0),
    CHECK (deadline_at > started_at),
    CHECK (call_count <= max_calls)
);
CREATE INDEX operations_workspace_idx ON operations (workspace_key, started_at);

CREATE TABLE operation_calls (
    operation_id uuid NOT NULL REFERENCES operations ON DELETE CASCADE,
    attempt_no integer NOT NULL CHECK (attempt_no > 0),
    prompt_reserved_tokens integer NOT NULL CHECK (prompt_reserved_tokens >= 0),
    output_reserved_tokens integer NOT NULL CHECK (output_reserved_tokens > 0),
    measured_prompt_tokens integer,
    measured_completion_tokens integer,
    status text NOT NULL CHECK (status IN ('reserved', 'completed', 'failed', 'unknown', 'cancelled')),
    created_at timestamptz NOT NULL DEFAULT now(),
    accounted_at timestamptz,
    PRIMARY KEY (operation_id, attempt_no),
    CHECK (measured_prompt_tokens IS NULL OR measured_prompt_tokens >= 0),
    CHECK (measured_completion_tokens IS NULL OR measured_completion_tokens >= 0),
    CHECK ((measured_prompt_tokens IS NULL) = (measured_completion_tokens IS NULL))
);
CREATE INDEX operation_calls_status_idx ON operation_calls (operation_id, status, attempt_no);

ALTER TABLE model_calls
    ADD COLUMN operation_id uuid REFERENCES operations ON DELETE SET NULL;
CREATE INDEX model_calls_operation_idx ON model_calls (operation_id) WHERE operation_id IS NOT NULL;
