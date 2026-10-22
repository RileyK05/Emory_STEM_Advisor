-- name: insert_privacy_export
INSERT INTO privacy_exports (
    export_id, workspace_key, user_id, status, manifest, download_token, expires_at
) VALUES (
    %(export_id)s, %(workspace_key)s, %(user_id)s, %(status)s, %(manifest)s, %(download_token)s, %(expires_at)s
) RETURNING *;

-- name: lock_workspace_privacy
SELECT pg_advisory_xact_lock(hashtextextended('privacy:' || %(workspace_key)s, 0));

-- name: get_privacy_export
SELECT * FROM privacy_exports
WHERE export_id = %(export_id)s;

-- name: list_privacy_exports
SELECT * FROM privacy_exports
WHERE workspace_key = %(workspace_key)s
ORDER BY created_at DESC
LIMIT %(limit)s;

-- name: insert_privacy_deletion
INSERT INTO privacy_deletions (
    deletion_id, workspace_key, user_id, status, fenced_at, stage
) VALUES (
    %(deletion_id)s, %(workspace_key)s, %(user_id)s, %(status)s, %(fenced_at)s, %(stage)s
) RETURNING *;

-- name: update_privacy_deletion
UPDATE privacy_deletions
SET status = %(status)s, stage = %(stage)s, deleted_counts = %(deleted_counts)s,
    finished_at = %(finished_at)s, error = %(error)s
WHERE deletion_id = %(deletion_id)s
RETURNING *;

-- name: get_privacy_deletion
SELECT * FROM privacy_deletions
WHERE deletion_id = %(deletion_id)s;

-- name: list_privacy_deletions
SELECT * FROM privacy_deletions
WHERE workspace_key = %(workspace_key)s
ORDER BY created_at DESC
LIMIT %(limit)s;

-- name: insert_deletion_tombstone
INSERT INTO deletion_tombstones (
    tombstone_id, workspace_key, resource_type, resource_id, reason
) VALUES (
    %(tombstone_id)s, %(workspace_key)s, %(resource_type)s, %(resource_id)s, %(reason)s
) ON CONFLICT (workspace_key, resource_type, resource_id) DO NOTHING
RETURNING *;

-- name: check_deletion_tombstone
SELECT * FROM deletion_tombstones
WHERE workspace_key = %(workspace_key)s
  AND resource_type = %(resource_type)s
  AND resource_id = %(resource_id)s;

-- name: cancel_workspace_jobs
UPDATE jobs
SET status = 'cancelled', cancel_requested = true, finished_at = now()
WHERE scope_key = %(workspace_key)s AND status IN ('queued', 'running');

-- name: count_workspace_inventory
SELECT
    (SELECT count(*) FROM conversations WHERE workspace_key = %(workspace_key)s) AS conversations,
    (SELECT count(*) FROM artifacts WHERE workspace_key = %(workspace_key)s) AS artifacts,
    (SELECT count(*) FROM research_runs WHERE workspace_key = %(workspace_key)s) AS research_runs,
    (SELECT count(*) FROM retrieval_gaps WHERE workspace_key = %(workspace_key)s) AS retrieval_gaps,
    (SELECT count(*) FROM auth_sessions WHERE workspace_key = %(workspace_key)s) AS sessions,
    (SELECT count(*) FROM student_academic_records WHERE workspace_key = %(workspace_key)s) AS academic_records,
    (SELECT count(*) FROM academic_record_import_batches WHERE workspace_key = %(workspace_key)s) AS academic_imports,
    (SELECT count(*) FROM degree_audits WHERE workspace_key = %(workspace_key)s) AS degree_audits,
    (SELECT count(*) FROM sources WHERE workspace_key = %(workspace_key)s) AS sources,
    (SELECT count(*) FROM response_feedback WHERE workspace_key = %(workspace_key)s) AS response_feedback,
    (SELECT count(*) FROM jobs WHERE scope_key = %(workspace_key)s) AS jobs;

-- name: list_workspace_feedback_for_export
SELECT feedback_id, response_id, revision, vote, reason_code, comment, is_current, created_at
FROM response_feedback
WHERE workspace_key = %(workspace_key)s
ORDER BY created_at;

-- name: list_workspace_audits_for_export
SELECT audit_id, program_id, overall_status, is_stale, created_at
FROM degree_audits
WHERE workspace_key = %(workspace_key)s
ORDER BY created_at;

-- name: list_workspace_artifact_originals
SELECT original_path
FROM artifacts
WHERE workspace_key = %(workspace_key)s
  AND original_path IS NOT NULL;

-- name: list_workspace_source_originals
SELECT raw_path AS original_path
FROM sources
WHERE workspace_key = %(workspace_key)s;

-- name: set_privacy_export_expiry
UPDATE privacy_exports
SET expires_at = %(expires_at)s
WHERE export_id = %(export_id)s
RETURNING export_id;

-- name: delete_expired_privacy_exports
DELETE FROM privacy_exports
WHERE expires_at IS NOT NULL AND expires_at < %(cutoff)s;

-- name: delete_workspace_comparison_members
DELETE FROM program_comparison_members
WHERE comparison_id IN (
    SELECT comparison_id FROM program_comparisons WHERE workspace_key = %(workspace_key)s
);

-- name: delete_workspace_comparisons
DELETE FROM program_comparisons WHERE workspace_key = %(workspace_key)s;

-- name: delete_workspace_feedback_events
DELETE FROM feedback_triage_events
WHERE feedback_id IN (
    SELECT feedback_id FROM response_feedback WHERE workspace_key = %(workspace_key)s
);

-- name: delete_workspace_feedback
DELETE FROM response_feedback WHERE workspace_key = %(workspace_key)s;

-- name: delete_workspace_academic_records
DELETE FROM student_academic_records WHERE workspace_key = %(workspace_key)s;

-- name: delete_workspace_quota_settlements
DELETE FROM quota_settlements WHERE workspace_key = %(workspace_key)s;

-- name: delete_workspace_jobs
DELETE FROM jobs WHERE scope_key = %(workspace_key)s;

-- name: delete_workspace_sources
-- Private uploads; chunks, locators and embeddings cascade.
DELETE FROM sources WHERE workspace_key = %(workspace_key)s;

-- name: delete_workspace_privacy_exports
DELETE FROM privacy_exports WHERE workspace_key = %(workspace_key)s;

-- name: delete_workspace_research
DELETE FROM research_runs WHERE workspace_key = %(workspace_key)s;

-- name: delete_workspace_summaries
DELETE FROM conversation_summaries WHERE workspace_key = %(workspace_key)s;

-- name: delete_workspace_conversations
DELETE FROM conversations WHERE workspace_key = %(workspace_key)s;

-- name: delete_workspace_artifacts
DELETE FROM artifacts WHERE workspace_key = %(workspace_key)s;

-- name: delete_workspace_gaps
DELETE FROM retrieval_gaps WHERE workspace_key = %(workspace_key)s;

-- name: delete_workspace_gap_observations
DELETE FROM gap_observations WHERE workspace_key = %(workspace_key)s;

-- name: delete_workspace_claim_reviews
DELETE FROM claim_reviews WHERE workspace_key = %(workspace_key)s;

-- name: delete_workspace_decisions
DELETE FROM decisions WHERE workspace_key = %(workspace_key)s;

-- name: delete_workspace_provider_batch_items
DELETE FROM provider_batch_items
WHERE workspace_key = %(workspace_key)s;

-- name: delete_workspace_provider_batches
DELETE FROM provider_batches WHERE workspace_key = %(workspace_key)s;

-- name: delete_workspace_debug_experiments
DELETE FROM debug_experiments WHERE workspace_key = %(workspace_key)s;

-- name: delete_workspace_tool_executions
DELETE FROM tool_executions WHERE workspace_key = %(workspace_key)s;

-- name: delete_workspace_tool_execution_operations
DELETE FROM tool_execution_operations WHERE workspace_key = %(workspace_key)s;

-- name: delete_workspace_retrieval_traces
DELETE FROM retrieval_traces WHERE workspace_key = %(workspace_key)s;

-- name: delete_workspace_legacy_retrieval_traces
-- Older traces predate workspace attribution. Remove the ones still reachable
-- through a saved conversation turn in this workspace.
DELETE FROM retrieval_traces
WHERE workspace_key IS NULL
  AND trace_id IN (
      SELECT (t.evidence->>'traceId')::uuid
      FROM conversation_turns t
      JOIN conversations c ON c.conversation_id = t.conversation_id
      WHERE c.workspace_key = %(workspace_key)s
        AND t.evidence->>'traceId' ~ '^[0-9a-fA-F-]{36}$'
  );

-- name: delete_workspace_quota_buckets
DELETE FROM quota_buckets
WHERE scope_type = 'workspace' AND scope_id = %(workspace_key)s;

-- name: delete_workspace_operations
DELETE FROM operations WHERE workspace_key = %(workspace_key)s;
