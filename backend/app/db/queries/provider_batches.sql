-- name: list_provider_batches_for_workspace
SELECT b.batch_id, b.state, b.cancel_requested, count(i.correlation_id)::integer AS item_count,
       b.total_count, b.completed_count, b.failed_count, b.created_at, b.updated_at
FROM provider_batches AS b
LEFT JOIN provider_batch_items AS i ON i.batch_id = b.batch_id
WHERE b.workspace_key = %(workspace_key)s
GROUP BY b.batch_id
ORDER BY b.created_at DESC, b.batch_id DESC
LIMIT 100;

-- name: insert_provider_batch
INSERT INTO provider_batches (
    batch_id, workspace_key, provider, model_id, profile_id, profile_revision,
    request_fingerprint, operation_limits, state
) VALUES (
    %(batch_id)s, %(workspace_key)s, %(provider)s, %(model_id)s, %(profile_id)s,
    %(profile_revision)s, %(request_fingerprint)s, %(operation_limits)s, 'prepared'
)
ON CONFLICT (batch_id) DO NOTHING
RETURNING *;

-- name: get_provider_batch
SELECT * FROM provider_batches WHERE batch_id = %(batch_id)s;

-- name: get_provider_batch_for_update
SELECT * FROM provider_batches WHERE batch_id = %(batch_id)s FOR UPDATE;

-- name: insert_provider_batch_item
INSERT INTO provider_batch_items (
    batch_id, workspace_key, correlation_id, operation_id, request_fingerprint,
    request_json, state
) VALUES (
    %(batch_id)s, %(workspace_key)s, %(correlation_id)s, %(operation_id)s,
    %(request_fingerprint)s, %(request_json)s, 'reserved'
)
ON CONFLICT (batch_id, correlation_id) DO NOTHING
RETURNING *;

-- name: list_provider_batch_items
SELECT * FROM provider_batch_items
WHERE batch_id = %(batch_id)s ORDER BY correlation_id;

-- name: get_provider_batch_item
SELECT * FROM provider_batch_items
WHERE batch_id = %(batch_id)s AND correlation_id = %(correlation_id)s;

-- name: list_provider_batch_items_for_update
SELECT * FROM provider_batch_items
WHERE batch_id = %(batch_id)s ORDER BY correlation_id FOR UPDATE;

-- name: lock_provider_batch_operation_budget
SELECT operation_id FROM operations WHERE operation_id = %(operation_id)s FOR UPDATE;

-- name: set_provider_batch_item_consumer_data
UPDATE provider_batch_items
SET consumer_data = %(consumer_data)s, updated_at = clock_timestamp()
WHERE batch_id = %(batch_id)s AND correlation_id = %(correlation_id)s
  AND workspace_key = %(workspace_key)s AND state = 'reserved'
  AND publication_state = 'pending' AND consumer_data = '{}'::jsonb
RETURNING *;

-- name: set_provider_batch_item_published
UPDATE provider_batch_items
SET publication_state = 'published', published_artifact_id = %(artifact_id)s,
    published_revision_id = %(revision_id)s, updated_at = clock_timestamp()
WHERE batch_id = %(batch_id)s AND correlation_id = %(correlation_id)s
  AND workspace_key = %(workspace_key)s AND state = 'completed'
  AND publication_state = 'pending'
RETURNING *;

-- name: set_provider_batch_item_withheld
UPDATE provider_batch_items
SET publication_state = 'withheld', updated_at = clock_timestamp()
WHERE batch_id = %(batch_id)s AND correlation_id = %(correlation_id)s
  AND workspace_key = %(workspace_key)s AND publication_state = 'pending'
RETURNING *;

-- name: set_provider_batch_item_result
UPDATE provider_batch_items
SET state = %(state)s, result_json = %(result_json)s, error_code = %(error_code)s,
    usage_source = %(usage_source)s, prompt_tokens = %(prompt_tokens)s,
    completion_tokens = %(completion_tokens)s, updated_at = clock_timestamp()
WHERE batch_id = %(batch_id)s AND correlation_id = %(correlation_id)s
  AND workspace_key = %(workspace_key)s AND state = 'received'
RETURNING *;

-- name: set_provider_batch_item_received
UPDATE provider_batch_items
SET state = 'received', result_json = %(result_json)s, error_code = %(error_code)s,
    usage_source = %(usage_source)s, prompt_tokens = %(prompt_tokens)s,
    completion_tokens = %(completion_tokens)s, updated_at = clock_timestamp()
WHERE batch_id = %(batch_id)s AND correlation_id = %(correlation_id)s
  AND workspace_key = %(workspace_key)s AND state = 'reserved'
RETURNING *;

-- name: start_provider_batch_submission
UPDATE provider_batches
SET state = 'submitting', submission_token = %(submission_token)s,
    submission_lease_until = %(submission_lease_until)s, updated_at = clock_timestamp()
WHERE batch_id = %(batch_id)s AND workspace_key = %(workspace_key)s AND state = 'prepared' AND cancel_requested = false
RETURNING *;

-- name: mark_provider_batch_submitted
UPDATE provider_batches
SET state = %(state)s, provider_batch_id = %(provider_batch_id)s,
    input_file_id = %(input_file_id)s, total_count = %(total_count)s,
    completed_count = %(completed_count)s, failed_count = %(failed_count)s,
    submission_token = NULL, submission_lease_until = NULL,
    finished_at = CASE WHEN %(terminal)s THEN clock_timestamp() ELSE NULL END,
    updated_at = clock_timestamp()
WHERE batch_id = %(batch_id)s AND workspace_key = %(workspace_key)s
  AND state = 'submitting' AND submission_token = %(submission_token)s
RETURNING *;

-- name: mark_provider_batch_unknown
UPDATE provider_batches
SET state = 'unknown', failure_code = %(failure_code)s,
    submission_token = NULL, submission_lease_until = NULL,
    finished_at = clock_timestamp(), updated_at = clock_timestamp()
WHERE batch_id = %(batch_id)s AND workspace_key = %(workspace_key)s
  AND state IN ('submitting', 'prepared', 'pending', 'validating', 'in_progress', 'finalizing',
                'completed', 'failed', 'expired', 'cancel_requested', 'cancelling', 'cancelled')
RETURNING *;

-- name: mark_provider_batch_submission_unknown
UPDATE provider_batches
SET state = 'unknown', failure_code = %(failure_code)s,
    submission_token = NULL, submission_lease_until = NULL,
    finished_at = clock_timestamp(), updated_at = clock_timestamp()
WHERE batch_id = %(batch_id)s AND workspace_key = %(workspace_key)s
  AND state = 'submitting' AND submission_token = %(submission_token)s
RETURNING *;

-- name: update_provider_batch_progress
UPDATE provider_batches
SET state = %(state)s, total_count = %(total_count)s,
    completed_count = %(completed_count)s, failed_count = %(failed_count)s,
    failure_code = %(failure_code)s,
    finished_at = CASE WHEN %(terminal)s THEN clock_timestamp() ELSE NULL END,
    updated_at = clock_timestamp()
WHERE batch_id = %(batch_id)s AND workspace_key = %(workspace_key)s
RETURNING *;

-- name: request_provider_batch_cancel
UPDATE provider_batches
SET cancel_requested = true, updated_at = clock_timestamp()
WHERE batch_id = %(batch_id)s AND workspace_key = %(workspace_key)s
RETURNING *;

-- name: get_provider_batch_profile_for_share
SELECT enabled, revision, provider, model_id
FROM connection_profiles WHERE profile_id = %(profile_id)s FOR SHARE;

-- name: get_provider_batch_operation_call
SELECT attempt_no, prompt_reserved_tokens, output_reserved_tokens,
       measured_prompt_tokens, measured_completion_tokens, status
FROM operation_calls
WHERE operation_id = %(operation_id)s
ORDER BY attempt_no DESC LIMIT 1;

-- name: mark_provider_batch_reserved_items_unknown
UPDATE provider_batch_items
SET state = 'unknown', publication_state = 'withheld', error_code = %(failure_code)s,
    updated_at = clock_timestamp()
WHERE batch_id = %(batch_id)s AND workspace_key = %(workspace_key)s
  AND state = 'reserved';
