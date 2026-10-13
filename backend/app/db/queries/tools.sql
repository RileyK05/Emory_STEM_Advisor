-- name: ensure_tool_execution_operation
INSERT INTO tool_execution_operations (operation_id, workspace_key, scope_fingerprint,
                                       max_tool_calls, claimed_calls)
VALUES (%(operation_id)s, %(workspace_key)s, %(scope_fingerprint)s,
        %(max_tool_calls)s, 0)
ON CONFLICT (operation_id) DO NOTHING;

-- name: lock_tool_execution_operation
SELECT workspace_key, scope_fingerprint, max_tool_calls, claimed_calls
FROM tool_execution_operations
WHERE operation_id = %(operation_id)s
FOR UPDATE;

-- name: increment_tool_execution_claims
UPDATE tool_execution_operations
SET claimed_calls = claimed_calls + 1
WHERE operation_id = %(operation_id)s AND claimed_calls < max_tool_calls
RETURNING claimed_calls;

-- name: insert_tool_execution_claim
INSERT INTO tool_executions (operation_id, workspace_key, call_id, tool_id, arguments_sha256,
                             scope_fingerprint, status)
VALUES (%(operation_id)s, %(workspace_key)s, %(call_id)s, %(tool_id)s, %(arguments_sha256)s,
        %(scope_fingerprint)s, 'executing')
ON CONFLICT (operation_id, call_id) DO NOTHING
RETURNING operation_id;

-- name: get_tool_execution
SELECT operation_id, workspace_key, call_id, tool_id, arguments_sha256, scope_fingerprint,
       status, result_json
FROM tool_executions
WHERE operation_id = %(operation_id)s AND call_id = %(call_id)s;

-- name: complete_tool_execution
UPDATE tool_executions
SET status = 'completed', result_json = %(result_json)s, completed_at = clock_timestamp()
WHERE operation_id = %(operation_id)s AND call_id = %(call_id)s
  AND workspace_key = %(workspace_key)s AND tool_id = %(tool_id)s
  AND arguments_sha256 = %(arguments_sha256)s AND scope_fingerprint = %(scope_fingerprint)s
  AND status = 'executing'
RETURNING operation_id;

-- name: mark_tool_execution_unknown
UPDATE tool_executions
SET status = 'unknown'
WHERE operation_id = %(operation_id)s AND call_id = %(call_id)s
  AND workspace_key = %(workspace_key)s AND status = 'executing'
RETURNING operation_id;
