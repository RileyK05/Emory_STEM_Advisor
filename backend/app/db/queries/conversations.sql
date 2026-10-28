-- name: create_conversation
INSERT INTO conversations (conversation_id, workspace_key, title)
VALUES (%(conversation_id)s, %(workspace_key)s, %(title)s)
RETURNING *;

-- name: list_conversations
SELECT * FROM conversations
WHERE workspace_key = %(workspace_key)s
  AND archived_at IS NULL
ORDER BY updated_at DESC, conversation_id
LIMIT %(limit)s OFFSET %(offset)s;

-- name: list_all_conversations_for_workspace
SELECT * FROM conversations WHERE workspace_key = %(workspace_key)s
ORDER BY created_at, conversation_id;

-- name: list_archived_conversations
SELECT * FROM conversations
WHERE workspace_key = %(workspace_key)s
  AND archived_at IS NOT NULL
ORDER BY archived_at DESC, conversation_id
LIMIT %(limit)s OFFSET %(offset)s;

-- name: get_conversation
SELECT * FROM conversations
WHERE conversation_id = %(conversation_id)s AND workspace_key = %(workspace_key)s;

-- name: lock_conversation
SELECT * FROM conversations
WHERE conversation_id = %(conversation_id)s AND workspace_key = %(workspace_key)s
FOR UPDATE;

-- name: get_conversation_request
SELECT * FROM conversation_requests
WHERE conversation_id = %(conversation_id)s AND request_id = %(request_id)s;

-- name: count_admission_pending
SELECT
  (SELECT count(*) FROM jobs
   WHERE scope_key = %(workspace_key)s
     AND status IN ('queued', 'running')) AS workspace_pending,
  (SELECT count(*) FROM jobs
   WHERE status IN ('queued', 'running')) AS global_pending;

-- name: metrics_pending_conversation_requests
SELECT count(*) AS requests
FROM conversation_requests
WHERE state = 'pending';

-- name: get_conversation_request_with_job
SELECT r.request_id, r.state AS request_state, r.job_id,
       j.status AS job_status, j.scope_key AS job_scope_key
FROM conversation_requests r
LEFT JOIN jobs j ON j.job_id = r.job_id
WHERE r.conversation_id = %(conversation_id)s AND r.request_id = %(request_id)s;

-- name: create_conversation_request
INSERT INTO conversation_requests (conversation_id, request_id, fingerprint, state, input_snapshot)
VALUES (%(conversation_id)s, %(request_id)s, %(fingerprint)s, 'pending', %(input_snapshot)s);

-- name: create_conversation_stream_request
INSERT INTO conversation_requests (conversation_id, request_id, fingerprint, state, input_snapshot, job_id)
VALUES (%(conversation_id)s, %(request_id)s, %(fingerprint)s, 'pending', %(input_snapshot)s, %(job_id)s);

-- name: insert_conversation_turn
INSERT INTO conversation_turns (conversation_id, sequence, request_id, role, text, status, evidence)
VALUES (%(conversation_id)s, %(sequence)s, %(request_id)s, %(role)s, %(text)s, %(status)s, %(evidence)s);

-- name: advance_conversation
UPDATE conversations
SET last_sequence = %(sequence)s, pending_request_id = %(pending_request_id)s, updated_at = now()
WHERE conversation_id = %(conversation_id)s;

-- name: finish_conversation_request
UPDATE conversation_requests SET state = %(state)s, result = %(result)s, completed_at = now()
WHERE conversation_id = %(conversation_id)s AND request_id = %(request_id)s AND state = 'pending';

-- name: conversation_turns
SELECT * FROM conversation_turns WHERE conversation_id = %(conversation_id)s
AND sequence > %(after)s ORDER BY sequence LIMIT %(limit)s;

-- Research keeps a bounded recent view; older turns remain available by tool.
-- name: conversation_recent_context
SELECT t.*, count(*) OVER () AS total_turns
FROM conversation_turns t
JOIN conversations c ON c.conversation_id = t.conversation_id
WHERE t.conversation_id = %(conversation_id)s
  AND t.sequence > c.context_boundary_sequence
ORDER BY t.sequence DESC LIMIT 50;

-- Source selection survives even when its last advising turn is outside the
-- recent view (for example after many explicit history-mode requests).
-- name: last_advising_context
SELECT t.evidence->'effective' AS effective
FROM conversation_turns t JOIN conversations c ON c.conversation_id = t.conversation_id
WHERE c.conversation_id = %(conversation_id)s AND c.workspace_key = %(workspace_key)s
  AND t.sequence > c.context_boundary_sequence
  AND t.role = 'user' AND coalesce(t.evidence->'request'->>'mode', 'advising') = 'advising'
ORDER BY t.sequence DESC LIMIT 1;

-- name: reset_conversation_memory
UPDATE conversations
SET context_epoch = context_epoch + 1,
    context_boundary_sequence = last_sequence,
    context_reset_at = now(),
    updated_at = now()
WHERE conversation_id = %(conversation_id)s AND workspace_key = %(workspace_key)s
RETURNING conversation_id, title, last_sequence, pending_request_id,
          context_epoch, context_boundary_sequence, context_reset_at,
          created_at, updated_at;

-- name: insert_claim
INSERT INTO claims (claim_id, response_id, ordinal, text, citation_numbers)
VALUES (%(claim_id)s, %(response_id)s, %(ordinal)s, %(text)s, %(citation_numbers)s);


-- name: latest_conversation_research_notebook
SELECT r.run_id, r.state, r.status, r.created_at
FROM research_runs r
JOIN conversations c ON c.conversation_id = r.conversation_id
JOIN conversation_turns t ON t.conversation_id = r.conversation_id
    AND t.request_id = r.conversation_request_id AND t.role = 'assistant'
WHERE c.conversation_id = %(conversation_id)s
  AND c.workspace_key = %(workspace_key)s
  AND r.workspace_key = %(workspace_key)s
  AND t.sequence > c.context_boundary_sequence
  AND t.status = 'answer'
  AND r.state IS NOT NULL
ORDER BY t.sequence DESC LIMIT 1;

-- name: get_published_conversation_turns
SELECT t.sequence, t.request_id, t.role, t.text, t.status, t.evidence, t.created_at
FROM conversation_turns t
JOIN conversation_requests r ON r.conversation_id = t.conversation_id AND r.request_id = t.request_id
WHERE t.conversation_id = %(conversation_id)s
  AND t.status IN ('input', 'answer')
  AND r.state = 'completed'
  AND coalesce(r.result->>'type', 'answer') NOT IN ('failed', 'abandoned')
  AND (%(through_sequence)s::integer IS NULL OR t.sequence <= %(through_sequence)s)
  AND EXISTS (
      SELECT 1 FROM conversation_turns assistant
      WHERE assistant.conversation_id = t.conversation_id AND assistant.request_id = t.request_id
        AND assistant.role = 'assistant' AND assistant.status = 'answer'
        AND (%(through_sequence)s::integer IS NULL OR assistant.sequence <= %(through_sequence)s)
  )
ORDER BY t.sequence ASC;

-- name: count_conversations_for_workspace
SELECT count(*) AS count FROM conversations
WHERE workspace_key = %(workspace_key)s AND archived_at IS NULL;

-- name: archive_conversation
UPDATE conversations
SET archived_at = now(), updated_at = now()
WHERE conversation_id = %(conversation_id)s
  AND workspace_key = %(workspace_key)s
  AND archived_at IS NULL
RETURNING *;

-- name: restore_conversation
UPDATE conversations
SET archived_at = NULL, updated_at = now()
WHERE conversation_id = %(conversation_id)s
  AND workspace_key = %(workspace_key)s
  AND archived_at IS NOT NULL
RETURNING *;

-- A pending request is reconcilable when its job is terminal, its job row is
-- missing, or it never had a job and has outlived the synchronous deadline.
-- A live queued/running lease is deliberately not selected.
-- name: reconcile_pending_conversation_requests
SELECT r.conversation_id, r.request_id, r.job_id, c.workspace_key,
       j.status AS job_status
FROM conversation_requests r
JOIN conversations c ON c.conversation_id = r.conversation_id
LEFT JOIN jobs j ON j.job_id = r.job_id
WHERE r.state = 'pending'
  AND (
      (r.job_id IS NULL
       AND r.created_at <= %(now)s - (%(older_than_seconds)s * interval '1 second'))
      OR (r.job_id IS NOT NULL AND j.job_id IS NULL)
      OR j.status IN ('succeeded', 'failed', 'cancelled')
      OR (j.status = 'running' AND j.lease_expires_at <= %(now)s)
  )
ORDER BY r.created_at, r.request_id
FOR UPDATE OF r SKIP LOCKED
LIMIT %(limit)s;
