CREATE TABLE tool_executions (
    operation_id uuid NOT NULL REFERENCES operations ON DELETE CASCADE,
    workspace_key text NOT NULL CHECK (length(workspace_key) BETWEEN 1 AND 200),
    call_id text NOT NULL CHECK (length(call_id) BETWEEN 1 AND 256),
    tool_id text NOT NULL CHECK (length(tool_id) BETWEEN 1 AND 128),
    arguments_sha256 char(64) NOT NULL CHECK (arguments_sha256 ~ '^[0-9a-f]{64}$'),
    status text NOT NULL CHECK (status IN ('executing', 'completed', 'unknown')),
    result_json text,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    PRIMARY KEY (operation_id, call_id),
    CHECK ((status = 'completed') = (result_json IS NOT NULL)),
    CHECK ((status = 'completed') = (completed_at IS NOT NULL)),
    CHECK (result_json IS NULL OR octet_length(result_json) <= 64000)
);
CREATE INDEX tool_executions_workspace_idx ON tool_executions (workspace_key, operation_id);
