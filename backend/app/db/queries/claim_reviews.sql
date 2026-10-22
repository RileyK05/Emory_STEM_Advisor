-- name: load_claim_review_input
SELECT r.response_id, r.trace_id, r.status AS response_status, r.text AS answer_text,
       r.context_snapshot, o.workspace_key, o.operation_id, rd.decision AS research_decision
FROM responses AS r
JOIN model_calls AS mc ON mc.trace_id = r.trace_id
JOIN operations AS o ON o.operation_id = mc.operation_id
LEFT JOIN research_decisions rd ON rd.response_id = r.response_id
WHERE r.response_id = %(response_id)s AND o.operation_id = %(operation_id)s
  AND o.workspace_key = %(workspace_key)s AND r.status = 'answer'
ORDER BY mc.created_at DESC, mc.model_call_id DESC
LIMIT 1;

-- name: get_response_for_trace
SELECT response_id, text, context_snapshot FROM responses
WHERE trace_id = %(trace_id)s AND status = 'answer'
ORDER BY created_at DESC, response_id DESC LIMIT 1;

-- name: get_operation_for_trace
SELECT o.operation_id FROM operations AS o
JOIN model_calls AS mc ON mc.operation_id = o.operation_id
WHERE mc.trace_id = %(trace_id)s
ORDER BY mc.created_at DESC, mc.model_call_id DESC LIMIT 1;

-- name: get_claim_review
SELECT * FROM claim_reviews
WHERE review_id = %(review_id)s AND workspace_key = %(workspace_key)s;

-- name: get_claim_review_owner
SELECT workspace_key FROM claim_reviews WHERE review_id = %(review_id)s;

-- name: get_claim_review_for_operation
SELECT review_id, workspace_key FROM claim_reviews
WHERE operation_id = %(operation_id)s;

-- name: get_claim_review_for_update
SELECT * FROM claim_reviews
WHERE review_id = %(review_id)s AND workspace_key = %(workspace_key)s
FOR UPDATE;

-- name: insert_claim_review_intent
INSERT INTO claim_reviews (
    review_id, response_id, workspace_key, operation_id, profile_id, profile_revision,
    provider, model_id, review_version, request_fingerprint, binding_fingerprint,
    input_snapshot, state
) VALUES (
    %(review_id)s, %(response_id)s, %(workspace_key)s, %(operation_id)s,
    %(profile_id)s, %(profile_revision)s, %(provider)s, %(model_id)s,
    %(review_version)s, %(request_fingerprint)s, %(binding_fingerprint)s,
    %(input_snapshot)s, 'pending'
) ON CONFLICT DO NOTHING RETURNING *;

-- name: lock_claim_review_operation
SELECT workspace_key, deadline_at, clock_timestamp() AS observed_at,
       total_token_budget, reserved_tokens, accounted_tokens
FROM operations WHERE operation_id = %(operation_id)s FOR UPDATE;

-- name: finish_claim_review
UPDATE claim_reviews SET state = %(state)s, usage_source = %(usage_source)s,
    prompt_tokens = %(prompt_tokens)s, completion_tokens = %(completion_tokens)s,
    failure_code = %(failure_code)s, updated_at = clock_timestamp()
WHERE review_id = %(review_id)s AND workspace_key = %(workspace_key)s
  AND state = 'pending' RETURNING *;

-- name: withhold_claim_review
UPDATE claim_reviews SET state = 'failed', failure_code = %(failure_code)s,
    updated_at = clock_timestamp()
WHERE review_id = %(review_id)s AND workspace_key = %(workspace_key)s
  AND state IN ('completed','refused','invalid') RETURNING *;

-- name: abandon_claim_review
UPDATE claim_reviews SET state = 'abandoned', failure_code = 'abandoned',
    updated_at = clock_timestamp()
WHERE review_id = %(review_id)s AND workspace_key = %(workspace_key)s
  AND state IN ('pending','unknown') RETURNING *;

-- name: update_abandoned_claim_review_usage
UPDATE claim_reviews SET usage_source = %(usage_source)s,
    prompt_tokens = %(prompt_tokens)s, completion_tokens = %(completion_tokens)s,
    updated_at = clock_timestamp()
WHERE review_id = %(review_id)s AND workspace_key = %(workspace_key)s
  AND state = 'abandoned' RETURNING *;

-- name: insert_claim_review_unit
INSERT INTO claim_review_units (
    review_id, ordinal, answer_start, answer_end, answer_text, coverage_kind,
    model_assessment, confidence, evidence_spans
) VALUES (
    %(review_id)s, %(ordinal)s, %(answer_start)s, %(answer_end)s,
    %(answer_text)s, %(coverage_kind)s, %(model_assessment)s,
    %(confidence)s, %(evidence_spans)s
);

-- name: insert_claim_review_unit_idempotent
INSERT INTO claim_review_units (
    review_id, ordinal, answer_start, answer_end, answer_text, coverage_kind,
    model_assessment, confidence, evidence_spans
) VALUES (
    %(review_id)s, %(ordinal)s, %(answer_start)s, %(answer_end)s,
    %(answer_text)s, %(coverage_kind)s, %(model_assessment)s,
    %(confidence)s, %(evidence_spans)s
) ON CONFLICT (review_id, ordinal) DO NOTHING;

-- name: list_claim_review_units
SELECT ordinal, answer_start, answer_end, answer_text, coverage_kind,
       model_assessment, confidence, evidence_spans
FROM claim_review_units WHERE review_id = %(review_id)s ORDER BY ordinal;

-- name: delete_claim_review_units
DELETE FROM claim_review_units WHERE review_id = %(review_id)s;

-- name: insert_unreviewed_claim_unit
INSERT INTO claim_review_units (
    review_id, ordinal, answer_start, answer_end, answer_text,
    coverage_kind, model_assessment, confidence, evidence_spans
) VALUES (
    %(review_id)s, 0, 0, %(answer_end)s, %(answer_text)s,
    'unreviewed_gap', 'unreviewed', NULL, '[]'::jsonb
) ON CONFLICT (review_id, ordinal) DO NOTHING;

-- name: insert_claim_review_model_call
INSERT INTO model_calls (
    model_call_id, trace_id, provider, model_id, status, finish_reason, request_id,
    prompt_tokens, completion_tokens, usage_source, latency_ms, error_type,
    operation_id, sampling_policy, reasoning_effort, requested_output_tokens,
    requested_timeout_seconds, requested_temperature, profile_id, profile_revision,
    claim_review_id, cached_prompt_tokens, schedule_id, estimated_cost, cost_status
) VALUES (
    %(model_call_id)s, %(trace_id)s, %(provider)s, %(model_id)s, %(status)s,
    %(finish_reason)s, %(request_id)s, %(prompt_tokens)s, %(completion_tokens)s,
    %(usage_source)s, %(latency_ms)s, %(error_type)s, %(operation_id)s,
    %(sampling_policy)s, %(reasoning_effort)s, %(requested_output_tokens)s,
    %(requested_timeout_seconds)s, %(requested_temperature)s, %(profile_id)s,
    %(profile_revision)s, %(claim_review_id)s, %(cached_prompt_tokens)s,
    %(schedule_id)s, %(estimated_cost)s, coalesce(%(cost_status)s, 'unknown')
);

-- name: count_claim_review_audits
SELECT count(*) FROM model_calls WHERE claim_review_id = %(review_id)s;
