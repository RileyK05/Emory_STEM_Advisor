-- name: get_conversation_summary
SELECT * FROM conversation_summaries
WHERE summary_id = %(summary_id)s AND conversation_id = %(conversation_id)s
  AND workspace_key = %(workspace_key)s;

-- name: get_conversation_summary_identity
SELECT conversation_id, workspace_key FROM conversation_summaries
WHERE summary_id = %(summary_id)s;

-- name: lock_conversation_summary
SELECT * FROM conversation_summaries
WHERE summary_id = %(summary_id)s AND conversation_id = %(conversation_id)s
  AND workspace_key = %(workspace_key)s
FOR UPDATE;

-- name: list_conversation_summaries
SELECT * FROM conversation_summaries
WHERE conversation_id = %(conversation_id)s AND workspace_key = %(workspace_key)s
ORDER BY created_at, summary_id;

-- name: latest_conversation_summary
SELECT * FROM conversation_summaries
WHERE conversation_id = %(conversation_id)s AND workspace_key = %(workspace_key)s
  AND state = 'completed'
ORDER BY covered_through DESC, created_at DESC, summary_id
LIMIT 1;

-- name: pending_conversation_summary
SELECT summary_id FROM conversation_summaries
WHERE conversation_id = %(conversation_id)s AND workspace_key = %(workspace_key)s
  AND state = 'pending'
LIMIT 1;

-- name: expire_stale_pending_summary
UPDATE conversation_summaries
SET state = 'failed', failure_code = 'stale_pending', updated_at = now()
WHERE conversation_id = %(conversation_id)s AND workspace_key = %(workspace_key)s
  AND state = 'pending'
  AND created_at < now() - make_interval(secs => %(seconds)s)
RETURNING summary_id;

-- name: insert_conversation_summary
INSERT INTO conversation_summaries (
    summary_id, conversation_id, workspace_key, expected_sequence,
    candidate_covered_through, request_fingerprint, input_fingerprint,
    input_snapshot, reference_metadata, state, operation_id
) VALUES (
    %(summary_id)s, %(conversation_id)s, %(workspace_key)s, %(expected_sequence)s,
    %(candidate_covered_through)s, %(request_fingerprint)s, %(input_fingerprint)s,
    %(input_snapshot)s, %(reference_metadata)s, 'pending', %(operation_id)s
)
RETURNING *;

-- name: finish_conversation_summary
UPDATE conversation_summaries SET
    state = %(state)s,
    summary_text = %(summary_text)s,
    covered_through = CASE WHEN %(state)s = 'completed' THEN candidate_covered_through ELSE NULL END,
    failure_code = %(failure_code)s,
    profile_id = %(profile_id)s,
    profile_revision = %(profile_revision)s,
    updated_at = now()
WHERE summary_id = %(summary_id)s AND conversation_id = %(conversation_id)s
  AND workspace_key = %(workspace_key)s AND state = 'pending'
RETURNING *;
