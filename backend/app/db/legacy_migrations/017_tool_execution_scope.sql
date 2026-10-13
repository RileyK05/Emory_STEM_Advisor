ALTER TABLE tool_executions
    ADD COLUMN scope_fingerprint char(64) NOT NULL
        DEFAULT '0000000000000000000000000000000000000000000000000000000000000000'
        CHECK (scope_fingerprint ~ '^[0-9a-f]{64}$');

CREATE TABLE tool_execution_operations (
    operation_id uuid PRIMARY KEY REFERENCES operations ON DELETE CASCADE,
    workspace_key text NOT NULL CHECK (length(workspace_key) BETWEEN 1 AND 200),
    scope_fingerprint char(64) NOT NULL CHECK (scope_fingerprint ~ '^[0-9a-f]{64}$'),
    max_tool_calls integer NOT NULL CHECK (max_tool_calls BETWEEN 0 AND 100),
    claimed_calls integer NOT NULL DEFAULT 0 CHECK (claimed_calls >= 0),
    CHECK (claimed_calls <= max_tool_calls)
);

INSERT INTO tool_execution_operations (operation_id, workspace_key, scope_fingerprint,
                                       max_tool_calls, claimed_calls)
SELECT operation_id, min(workspace_key),
       '0000000000000000000000000000000000000000000000000000000000000000',
       100, count(*)::integer
FROM tool_executions
GROUP BY operation_id;
