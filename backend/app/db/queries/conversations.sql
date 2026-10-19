-- name: create_conversation
INSERT INTO conversations (conversation_id, workspace_key, title)
VALUES (%(conversation_id)s, %(workspace_key)s, %(title)s)
RETURNING *;

-- name: list_conversations
SELECT * FROM conversations WHERE workspace_key = %(workspace_key)s
ORDER BY updated_at DESC, conversation_id LIMIT 200;

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
SELECT *, count(*) OVER () AS total_turns
FROM conversation_turns WHERE conversation_id = %(conversation_id)s
ORDER BY sequence DESC LIMIT 50;

-- Source selection survives even when its last advising turn is outside the
-- recent view (for example after many explicit history-mode requests).
-- name: last_advising_context
SELECT t.evidence->'effective' AS effective
FROM conversation_turns t JOIN conversations c ON c.conversation_id = t.conversation_id
WHERE c.conversation_id = %(conversation_id)s AND c.workspace_key = %(workspace_key)s
  AND t.role = 'user' AND coalesce(t.evidence->'request'->>'mode', 'advising') = 'advising'
ORDER BY t.sequence DESC LIMIT 1;

-- name: insert_claim
INSERT INTO claims (claim_id, response_id, ordinal, text, citation_numbers)
VALUES (%(claim_id)s, %(response_id)s, %(ordinal)s, %(text)s, %(citation_numbers)s);
