-- Response feedback. The current vote is one row per owner and response.

-- name: get_current_response_feedback
SELECT feedback_id, workspace_key, response_id, revision, vote, reason_code, comment, is_current, created_at
FROM response_feedback
WHERE workspace_key = %(workspace_key)s AND response_id = %(response_id)s AND is_current;

-- name: clear_current_response_feedback
UPDATE response_feedback
SET is_current = false
WHERE workspace_key = %(workspace_key)s AND response_id = %(response_id)s AND is_current;

-- name: insert_response_feedback
INSERT INTO response_feedback (
    feedback_id, workspace_key, response_id, revision, vote, reason_code, comment, is_current
) VALUES (
    %(feedback_id)s, %(workspace_key)s, %(response_id)s, %(revision)s, %(vote)s,
    %(reason_code)s, %(comment)s, true
)
RETURNING feedback_id, workspace_key, response_id, revision, vote, reason_code, comment, is_current, created_at;

-- name: count_current_feedback_votes
SELECT count(*) AS votes
FROM response_feedback
WHERE response_id = %(response_id)s AND is_current;

-- name: insert_eval_candidate
INSERT INTO eval_candidates (candidate_id, redacted_prompt, redacted_expected, source_feedback_id)
VALUES (%(candidate_id)s, %(redacted_prompt)s, %(redacted_expected)s, %(source_feedback_id)s)
RETURNING candidate_id, redacted_prompt, redacted_expected, source_feedback_id, created_at;

-- name: get_eval_candidate
SELECT candidate_id, redacted_prompt, redacted_expected, source_feedback_id, created_at
FROM eval_candidates
WHERE candidate_id = %(candidate_id)s;

-- name: get_response_feedback_for_update
SELECT feedback_id, workspace_key, response_id, revision, vote, reason_code, comment,
       issue_type, triage_status, triage_revision, is_current, created_at
FROM response_feedback
WHERE feedback_id = %(feedback_id)s
FOR UPDATE;

-- name: latest_feedback_triage_event
SELECT event_id, feedback_id, issue_type, from_status, to_status, gap_id, note, created_at
FROM feedback_triage_events
WHERE feedback_id = %(feedback_id)s
ORDER BY created_at DESC
LIMIT 1;

-- name: insert_feedback_triage_event
INSERT INTO feedback_triage_events (
    event_id, feedback_id, issue_type, from_status, to_status, actor_user_id,
    note, expected_revision, gap_id
) VALUES (
    %(event_id)s, %(feedback_id)s, %(issue_type)s, %(from_status)s, %(to_status)s,
    %(actor_user_id)s, %(note)s, %(expected_revision)s, %(gap_id)s
)
RETURNING event_id, feedback_id, issue_type, from_status, to_status, gap_id, note, created_at;

-- name: set_feedback_triage
UPDATE response_feedback
SET issue_type = %(issue_type)s,
    triage_status = %(to_status)s,
    triage_revision = triage_revision + 1
WHERE feedback_id = %(feedback_id)s
  AND revision = %(expected_revision)s
  AND triage_revision = %(expected_triage_revision)s
RETURNING feedback_id, workspace_key, response_id, revision, vote, reason_code, comment,
          issue_type, triage_status, triage_revision;

-- name: list_feedback_triage_events
SELECT e.event_id, e.feedback_id, e.issue_type, e.to_status, e.gap_id, e.created_at,
       f.revision, f.triage_revision, f.vote
FROM feedback_triage_events e
JOIN response_feedback f ON f.feedback_id = e.feedback_id
ORDER BY e.created_at DESC
LIMIT 50;

-- name: get_source_index_for_promotion
SELECT source_id, collection_id, workspace_key, index_revision, is_current
FROM sources
WHERE source_id = %(source_id)s;

-- name: get_current_eval_candidate_by_digest
SELECT candidate_id, redacted_prompt, redacted_expected, source_feedback_id,
       candidate_digest, source_id, source_index_revision, promotion_state, review_state,
       gap_id, created_at
FROM eval_candidates
WHERE candidate_digest = %(candidate_digest)s AND promotion_state = 'current';

-- name: stale_current_eval_candidate
UPDATE eval_candidates
SET promotion_state = 'stale'
WHERE candidate_digest = %(candidate_digest)s AND promotion_state = 'current'
RETURNING candidate_id;

-- name: insert_promoted_eval_candidate
INSERT INTO eval_candidates (
    candidate_id, redacted_prompt, redacted_expected, source_feedback_id,
    candidate_digest, source_id, source_index_revision, promotion_state, review_state, gap_id
) VALUES (
    %(candidate_id)s, %(redacted_prompt)s, %(redacted_expected)s, %(source_feedback_id)s,
    %(candidate_digest)s, %(source_id)s, %(source_index_revision)s, 'current',
    %(review_state)s, %(gap_id)s
)
RETURNING candidate_id, redacted_prompt, redacted_expected, source_feedback_id,
          candidate_digest, source_id, source_index_revision, promotion_state, review_state,
          gap_id, created_at;
