-- name: insert_model_call
INSERT INTO model_calls (
    model_call_id, trace_id, provider, model_id, status, finish_reason, request_id,
    prompt_tokens, completion_tokens, usage_source, latency_ms, error_type, operation_id,
    sampling_policy, reasoning_effort, requested_output_tokens, requested_timeout_seconds,
    requested_temperature, profile_id, profile_revision, cached_prompt_tokens,
    schedule_id, estimated_cost, cost_status
) VALUES (
    %(model_call_id)s, %(trace_id)s, %(provider)s, %(model_id)s, %(status)s,
    %(finish_reason)s, %(request_id)s, %(prompt_tokens)s, %(completion_tokens)s,
    %(usage_source)s, %(latency_ms)s, %(error_type)s, %(operation_id)s,
    %(sampling_policy)s, %(reasoning_effort)s, %(requested_output_tokens)s,
    %(requested_timeout_seconds)s, %(requested_temperature)s, %(profile_id)s, %(profile_revision)s,
    %(cached_prompt_tokens)s,
    %(schedule_id)s, %(estimated_cost)s, coalesce(%(cost_status)s, 'unknown')
);


-- name: insert_response
INSERT INTO responses (
    response_id, trace_id, status, text, reason, prompt_version, context_snapshot
) VALUES (
    %(response_id)s, %(trace_id)s, %(status)s, %(text)s, %(reason)s,
    %(prompt_version)s, %(context_snapshot)s
);

-- name: insert_response_citation
INSERT INTO citations (
    response_id, ordinal, chunk_id, source_id, source_name, locator_label, text_snapshot
) VALUES (
    %(response_id)s, %(ordinal)s, %(chunk_id)s, %(source_id)s, %(source_name)s,
    %(locator_label)s, %(text_snapshot)s
);

-- name: attach_operation_model_calls_to_trace
-- A planning call runs before its retrieval trace exists; link it once written.
UPDATE model_calls
SET trace_id = %(trace_id)s
WHERE operation_id = %(operation_id)s
  AND trace_id IS NULL;
