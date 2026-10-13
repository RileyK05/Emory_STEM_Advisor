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

-- name: insert_claim
INSERT INTO claims (claim_id, response_id, ordinal, text, citation_numbers)
VALUES (%(claim_id)s, %(response_id)s, %(ordinal)s, %(text)s, %(citation_numbers)s);
