-- name: backup_ready
SELECT NOT EXISTS (
    SELECT 1 FROM jobs
    WHERE status = 'queued'
       OR (status = 'running' AND lease_expires_at > statement_timestamp())
    UNION ALL SELECT 1 FROM provider_batches WHERE state IN (
        'prepared', 'submitting', 'pending', 'validating', 'in_progress',
        'finalizing', 'cancel_requested', 'cancelling'
    )
    UNION ALL SELECT 1 FROM conversation_requests WHERE state = 'pending'
    UNION ALL SELECT 1 FROM decisions WHERE state = 'pending'
    UNION ALL SELECT 1 FROM conversation_summaries WHERE state = 'pending'
    UNION ALL SELECT 1 FROM claim_reviews WHERE state = 'pending'
    UNION ALL SELECT 1 FROM operation_calls c JOIN operations o USING (operation_id)
        WHERE c.status = 'reserved' AND o.deadline_at > statement_timestamp()
) AS ready;

-- name: backup_export_snapshot
SELECT pg_export_snapshot() AS snapshot_id;

-- name: backup_source_files
SELECT source_id, raw_path, file_hash FROM sources ORDER BY source_id;

-- name: backup_optional_file_tables
SELECT to_regclass('public.atlas_offering_capture_files') IS NOT NULL AS offerings,
       to_regclass('public.atlas_requirement_holds') IS NOT NULL AS requirement_holds;

-- name: backup_atlas_capture_files
SELECT source_id, capture_set_hash, capture_hash, raw_path
FROM atlas_offering_capture_files
ORDER BY source_id, capture_set_hash, capture_hash;

-- name: backup_requirement_hold_capture_files
SELECT hold_id, evidence_capture_sha256 AS capture_hash, evidence_capture_path AS raw_path
FROM atlas_requirement_holds
ORDER BY hold_id;

-- name: backup_artifact_files
SELECT artifact_id, original_path, original_sha256 FROM artifacts
WHERE original_path IS NOT NULL ORDER BY artifact_id;

-- name: backup_restore_source_path
UPDATE sources SET raw_path = %(raw_path)s WHERE source_id = %(source_id)s::uuid;

-- name: backup_restore_artifact_path
UPDATE artifacts SET original_path = %(original_path)s WHERE artifact_id = %(artifact_id)s::uuid;

-- name: backup_restore_atlas_capture_path
UPDATE atlas_offering_capture_files SET raw_path = %(raw_path)s
WHERE source_id = %(source_id)s::uuid AND capture_set_hash = %(capture_set_hash)s
  AND capture_hash = %(capture_hash)s;

-- name: backup_restore_requirement_hold_capture_path
UPDATE atlas_requirement_holds SET evidence_capture_path = %(raw_path)s
WHERE hold_id = %(hold_id)s::uuid AND evidence_capture_sha256 = %(capture_hash)s;

-- name: backup_get_artifact_original
SELECT original_path FROM artifacts WHERE artifact_id = %(artifact_id)s;

-- name: backup_operation_usage
SELECT c.status, o.reserved_tokens, o.accounted_tokens
FROM operation_calls c JOIN operations o USING (operation_id)
WHERE c.operation_id = %(operation_id)s AND c.attempt_no = %(attempt_no)s;

-- name: backup_provider_batch_state
SELECT state, failure_code FROM provider_batches WHERE batch_id = %(batch_id)s;

-- name: backup_set_repeatable_read_snapshot
SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;

-- name: backup_target_empty
SELECT NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
) AS is_empty;

-- name: backup_server_version
SHOW server_version_num;
