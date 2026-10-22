-- name: insert_decision_intent
INSERT INTO decisions (
    decision_id, workspace_key, operation_id, profile_id, profile_revision,
    provider, model_id, request_fingerprint, request_json, state
) VALUES (
    %(decision_id)s, %(workspace_key)s, %(operation_id)s, %(profile_id)s,
    %(profile_revision)s, %(provider)s, %(model_id)s, %(request_fingerprint)s,
    %(request_json)s, 'pending'
) ON CONFLICT (decision_id) DO NOTHING RETURNING *;

-- name: get_decision_for_workspace
SELECT * FROM decisions
WHERE decision_id = %(decision_id)s AND workspace_key = %(workspace_key)s;

-- name: get_decision_owner
SELECT workspace_key FROM decisions WHERE decision_id = %(decision_id)s;

-- name: lock_decision_operation_fence
SELECT workspace_key, deadline_at, clock_timestamp() AS observed_at,
       total_token_budget, reserved_tokens, accounted_tokens
FROM operations WHERE operation_id = %(operation_id)s FOR UPDATE;

-- name: get_decision_for_workspace_update
SELECT * FROM decisions
WHERE decision_id = %(decision_id)s AND workspace_key = %(workspace_key)s
FOR UPDATE;

-- name: complete_decision
UPDATE decisions SET state = %(state)s, prediction = %(prediction)s,
    probability = %(probability)s, confidence = %(confidence)s,
    refused = %(refused)s, usage_source = %(usage_source)s,
    prompt_tokens = %(prompt_tokens)s, completion_tokens = %(completion_tokens)s,
    error_code = %(error_code)s, updated_at = clock_timestamp()
WHERE decision_id = %(decision_id)s AND workspace_key = %(workspace_key)s
  AND state = 'pending' RETURNING *;

-- name: abandon_decision
UPDATE decisions SET state = 'abandoned', prediction = NULL, probability = NULL,
    confidence = NULL, refused = false, updated_at = clock_timestamp()
WHERE decision_id = %(decision_id)s AND workspace_key = %(workspace_key)s
  AND state IN ('pending','unknown') RETURNING *;

-- name: update_abandoned_decision_usage
UPDATE decisions SET usage_source = %(usage_source)s, prompt_tokens = %(prompt_tokens)s,
    completion_tokens = %(completion_tokens)s, updated_at = clock_timestamp()
WHERE decision_id = %(decision_id)s AND workspace_key = %(workspace_key)s
  AND state = 'abandoned' RETURNING *;

-- name: withhold_decision_result
UPDATE decisions SET state = 'failed', prediction = NULL, probability = NULL,
    confidence = NULL, refused = false, error_code = %(error_code)s,
    updated_at = clock_timestamp()
WHERE decision_id = %(decision_id)s AND workspace_key = %(workspace_key)s
  AND state IN ('completed','refused') RETURNING *;
