-- Debug experiments are separate from saved conversation replay.

-- name: insert_debug_experiment
INSERT INTO debug_experiments (
    experiment_id, workspace_key, source_conversation_id, source_request_id, snapshot, state
) VALUES (
    %(experiment_id)s, %(workspace_key)s, %(source_conversation_id)s, %(source_request_id)s,
    %(snapshot)s, %(state)s
)
RETURNING experiment_id, workspace_key, source_conversation_id, source_request_id, snapshot,
          state, created_at;

-- name: count_debug_experiments
SELECT count(*) AS experiments FROM debug_experiments;
