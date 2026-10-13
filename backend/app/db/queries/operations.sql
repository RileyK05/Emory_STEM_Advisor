-- name: create_operation_budget
INSERT INTO operations (
    operation_id, workspace_key, started_at, deadline_at, max_calls,
    total_token_budget, per_call_output_cap, per_call_timeout_seconds
) VALUES (
    %(operation_id)s, %(workspace_key)s, clock_timestamp(),
    clock_timestamp() + (%(active_budget_seconds)s * interval '1 second'),
    %(max_calls)s, %(total_token_budget)s, %(per_call_output_cap)s,
    %(per_call_timeout_seconds)s
)
ON CONFLICT (operation_id) DO NOTHING
RETURNING operation_id;

-- name: get_operation_budget_for_update
SELECT operation_id, workspace_key, started_at, deadline_at, max_calls, call_count,
       total_token_budget, reserved_tokens, accounted_tokens, per_call_output_cap,
       per_call_timeout_seconds, clock_timestamp() AS observed_at
FROM operations
WHERE operation_id = %(operation_id)s
FOR UPDATE;

-- name: get_operation_budget
SELECT operation_id, workspace_key, started_at, deadline_at, max_calls, call_count,
       total_token_budget, reserved_tokens, accounted_tokens, per_call_output_cap,
       per_call_timeout_seconds, clock_timestamp() AS observed_at
FROM operations
WHERE operation_id = %(operation_id)s;

-- name: reserve_operation_budget_call
UPDATE operations
SET call_count = call_count + 1,
    reserved_tokens = reserved_tokens + %(reservation_tokens)s
WHERE operation_id = %(operation_id)s
  AND call_count = %(expected_call_count)s
  AND reserved_tokens = %(expected_reserved_tokens)s
RETURNING call_count, reserved_tokens;

-- name: insert_operation_budget_call
INSERT INTO operation_calls (
    operation_id, attempt_no, prompt_reserved_tokens, output_reserved_tokens, status
) VALUES (
    %(operation_id)s, %(attempt_no)s, %(prompt_reserved_tokens)s,
    %(output_reserved_tokens)s, 'reserved'
);

-- name: get_operation_call_for_update
SELECT c.operation_id, c.attempt_no, c.prompt_reserved_tokens, c.output_reserved_tokens,
       c.measured_prompt_tokens, c.measured_completion_tokens, c.status,
       o.reserved_tokens, o.accounted_tokens
FROM operation_calls AS c
JOIN operations AS o USING (operation_id)
WHERE c.operation_id = %(operation_id)s AND c.attempt_no = %(attempt_no)s
FOR UPDATE OF c, o;

-- name: settle_operation_budget_call
UPDATE operations
SET reserved_tokens = reserved_tokens - %(reservation_tokens)s,
    accounted_tokens = accounted_tokens + %(measured_tokens)s
WHERE operation_id = %(operation_id)s
  AND reserved_tokens >= %(reservation_tokens)s
RETURNING operation_id;

-- name: settle_operation_call
UPDATE operation_calls
SET measured_prompt_tokens = %(prompt_tokens)s,
    measured_completion_tokens = %(completion_tokens)s,
    status = %(status)s,
    accounted_at = clock_timestamp()
WHERE operation_id = %(operation_id)s AND attempt_no = %(attempt_no)s
  AND status IN ('reserved', 'unknown', 'failed', 'cancelled')
  AND measured_prompt_tokens IS NULL AND measured_completion_tokens IS NULL
RETURNING operation_id;

-- name: keep_operation_call_reservation
UPDATE operation_calls
SET status = %(status)s, accounted_at = clock_timestamp()
WHERE operation_id = %(operation_id)s AND attempt_no = %(attempt_no)s
  AND status = 'reserved'
RETURNING operation_id;
