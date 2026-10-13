-- name: lock_active_artifact_run_job
SELECT job_id, kind, scope_key, status, lease_token, lease_expires_at, cancel_requested
FROM jobs
WHERE job_id = %(job_id)s AND kind = 'artifact.run'
  AND scope_key = %(workspace_key)s AND status = 'running'
  AND lease_token = %(lease_token)s AND cancel_requested = false
  AND lease_expires_at > clock_timestamp()
FOR UPDATE;

-- name: get_sources_for_artifact_run_plan
SELECT source_id, collection_id, file_hash, index_revision, workspace_key, status, is_current
FROM sources
WHERE source_id = ANY(%(source_ids)s::uuid[])
  AND collection_id = %(collection_id)s
  AND (workspace_key IS NULL OR workspace_key = %(workspace_key)s)
  AND status = 'indexed'
  AND is_current
ORDER BY source_id
FOR SHARE OF sources;

-- name: list_indexed_sources_for_artifact_run
SELECT source_id, collection_id, file_hash, index_revision, workspace_key, filename
FROM sources
WHERE collection_id = %(collection_id)s
  AND (workspace_key IS NULL OR workspace_key = %(workspace_key)s)
  AND status = 'indexed'
  AND is_current
ORDER BY source_id
FOR SHARE;

-- name: insert_artifact_run_plan
INSERT INTO artifact_run_plans (
    job_id, workspace_key, collection_id, artifact_kind, status,
    input_fingerprint, frozen_inputs, quotas, target_artifact_id,
    target_revision_id, profile_id, profile_revision
) VALUES (
    %(job_id)s, %(workspace_key)s, %(collection_id)s, %(artifact_kind)s, 'ready',
    %(input_fingerprint)s, %(frozen_inputs)s, %(quotas)s, %(target_artifact_id)s,
    %(target_revision_id)s, %(profile_id)s, %(profile_revision)s
)
ON CONFLICT (job_id) DO NOTHING
RETURNING *;

-- name: get_artifact_run_plan
SELECT p.*
FROM artifact_run_plans AS p
JOIN jobs AS j ON j.job_id = p.job_id
WHERE p.job_id = %(job_id)s AND p.workspace_key = %(workspace_key)s
  AND j.scope_key = %(workspace_key)s;

-- name: artifact_run_target_head_current
SELECT a.artifact_id, a.head_revision_id
FROM artifacts AS a
JOIN artifact_revisions AS r
  ON r.artifact_id = a.artifact_id AND r.revision_id = %(target_revision_id)s
WHERE a.artifact_id = %(target_artifact_id)s
  AND a.workspace_key = %(workspace_key)s
  AND a.head_revision_id = %(target_revision_id)s
FOR SHARE OF a;

-- name: lock_artifact_run_plan
SELECT p.*
FROM artifact_run_plans AS p
JOIN jobs AS j ON j.job_id = p.job_id
WHERE p.job_id = %(job_id)s AND p.workspace_key = %(workspace_key)s
  AND j.scope_key = %(workspace_key)s
FOR UPDATE OF p;

-- name: insert_artifact_run_plan_source
INSERT INTO artifact_run_plan_sources (
    job_id, source_id, collection_id, file_hash, index_revision
) VALUES (
    %(job_id)s, %(source_id)s, %(collection_id)s, %(file_hash)s, %(index_revision)s
);

-- name: get_exact_excerpts_for_artifact_plan
SELECT c.chunk_id, c.locator_id, c.source_id, c.text, l.label AS locator_label, s.filename AS source_name,
       s.file_hash, s.index_revision, s.collection_id, s.workspace_key, s.status
FROM chunks AS c
JOIN sources AS s ON s.source_id = c.source_id
JOIN locators AS l ON l.locator_id = c.locator_id AND l.source_id = c.source_id
WHERE c.chunk_id = ANY(%(chunk_ids)s::uuid[])
  AND s.collection_id = %(collection_id)s
  AND (s.workspace_key IS NULL OR s.workspace_key = %(workspace_key)s)
  AND s.status = 'indexed' AND s.is_current
ORDER BY c.chunk_id
FOR SHARE OF s, c, l;

-- name: insert_artifact_run_plan_excerpt
INSERT INTO artifact_run_plan_excerpts (
    job_id, excerpt_id, source_id, chunk_id, locator_id, source_name, locator_label, text_snapshot,
    excerpt_fingerprint
) VALUES (
    %(job_id)s, %(excerpt_id)s, %(source_id)s, %(chunk_id)s, %(locator_id)s,
    %(source_name)s, %(locator_label)s,
    %(text_snapshot)s, %(excerpt_fingerprint)s
);

-- name: list_artifact_run_plan_excerpts
SELECT e.excerpt_id, e.source_id, e.chunk_id, e.locator_id, e.source_name, e.locator_label, e.text_snapshot,
       e.excerpt_fingerprint
FROM artifact_run_plan_excerpts AS e
JOIN artifact_run_plans AS p ON p.job_id = e.job_id
JOIN jobs AS j ON j.job_id = p.job_id
WHERE e.job_id = %(job_id)s AND p.workspace_key = %(workspace_key)s
  AND j.scope_key = %(workspace_key)s
ORDER BY e.excerpt_id;

-- name: artifact_run_plan_sources_lock_for_unit
SELECT s.source_id
FROM artifact_run_unit_sources AS us
JOIN artifact_run_plan_sources AS ps
  ON ps.job_id = us.job_id AND ps.source_id = us.source_id
JOIN sources AS s ON s.source_id = ps.source_id
WHERE us.job_id = %(job_id)s AND us.unit_id = %(unit_id)s
FOR SHARE OF s;

-- name: artifact_run_plan_excerpts_lock_for_unit
SELECT c.chunk_id, l.locator_id, s.source_id
FROM artifact_run_unit_sources AS us
JOIN artifact_run_plan_excerpts AS e
  ON e.job_id = us.job_id AND e.source_id = us.source_id
JOIN chunks AS c ON c.chunk_id = e.chunk_id AND c.source_id = e.source_id
JOIN locators AS l ON l.locator_id = c.locator_id AND l.source_id = c.source_id
JOIN sources AS s ON s.source_id = c.source_id
WHERE us.job_id = %(job_id)s AND us.unit_id = %(unit_id)s
FOR SHARE OF c, l, s;

-- name: get_artifact_run_plan_sources
SELECT ps.source_id, ps.collection_id, ps.file_hash, ps.index_revision
FROM artifact_run_plan_sources AS ps
JOIN artifact_run_plans AS p ON p.job_id = ps.job_id
JOIN jobs AS j ON j.job_id = p.job_id
WHERE ps.job_id = %(job_id)s AND p.workspace_key = %(workspace_key)s
  AND j.scope_key = %(workspace_key)s
ORDER BY ps.source_id;

-- name: insert_artifact_run_unit
INSERT INTO artifact_run_units (
    unit_id, job_id, ordinal, unit_key, unit_kind, status,
    input_fingerprint, input_snapshot, profile_id, profile_revision
) VALUES (
    %(unit_id)s, %(job_id)s, %(ordinal)s, %(unit_key)s, %(unit_kind)s, 'pending',
    %(input_fingerprint)s, %(input_snapshot)s, %(profile_id)s, %(profile_revision)s
);

-- name: insert_artifact_run_unit_source
INSERT INTO artifact_run_unit_sources (job_id, unit_id, source_id)
VALUES (%(job_id)s, %(unit_id)s, %(source_id)s);

-- name: insert_artifact_run_unit_dependency
INSERT INTO artifact_run_unit_dependencies (job_id, unit_id, depends_on_unit_id)
VALUES (%(job_id)s, %(unit_id)s, %(depends_on_unit_id)s);

-- name: list_artifact_run_units
SELECT u.*
FROM artifact_run_units AS u
JOIN artifact_run_plans AS p ON p.job_id = u.job_id
JOIN jobs AS j ON j.job_id = p.job_id
WHERE u.job_id = %(job_id)s AND p.workspace_key = %(workspace_key)s
  AND j.scope_key = %(workspace_key)s
ORDER BY u.ordinal;

-- name: list_artifact_run_unit_sources
SELECT us.unit_id, us.source_id
FROM artifact_run_unit_sources AS us
JOIN artifact_run_plans AS p ON p.job_id = us.job_id
JOIN jobs AS j ON j.job_id = p.job_id
WHERE us.job_id = %(job_id)s AND p.workspace_key = %(workspace_key)s
  AND j.scope_key = %(workspace_key)s
ORDER BY us.unit_id, us.source_id;

-- name: list_artifact_run_unit_dependencies
SELECT d.unit_id, d.depends_on_unit_id
FROM artifact_run_unit_dependencies AS d
JOIN artifact_run_plans AS p ON p.job_id = d.job_id
JOIN jobs AS j ON j.job_id = p.job_id
WHERE d.job_id = %(job_id)s AND p.workspace_key = %(workspace_key)s
  AND j.scope_key = %(workspace_key)s
ORDER BY d.unit_id, d.depends_on_unit_id;

-- name: lock_artifact_run_unit
SELECT u.*
FROM artifact_run_units AS u
JOIN artifact_run_plans AS p ON p.job_id = u.job_id
JOIN jobs AS j ON j.job_id = p.job_id
WHERE u.job_id = %(job_id)s AND u.unit_key = %(unit_key)s
  AND p.workspace_key = %(workspace_key)s AND j.scope_key = %(workspace_key)s
FOR UPDATE OF u;

-- name: artifact_run_unit_dependencies_ready
SELECT d.depends_on_unit_id, parent.status
FROM artifact_run_unit_dependencies AS d
JOIN artifact_run_units AS parent
  ON parent.job_id = d.job_id AND parent.unit_id = d.depends_on_unit_id
WHERE d.job_id = %(job_id)s AND d.unit_id = %(unit_id)s
ORDER BY parent.ordinal;

-- name: artifact_run_plan_sources_current_for_unit
SELECT ps.source_id, ps.collection_id, ps.file_hash, ps.index_revision,
       s.collection_id AS current_collection_id, s.file_hash AS current_file_hash,
       s.index_revision AS current_index_revision, s.status AS current_status,
       s.workspace_key AS current_workspace_key, s.is_current AS current_is_current
FROM artifact_run_unit_sources AS us
JOIN artifact_run_plan_sources AS ps
  ON ps.job_id = us.job_id AND ps.source_id = us.source_id
JOIN artifact_run_plans AS p ON p.job_id = us.job_id
JOIN jobs AS j ON j.job_id = p.job_id
LEFT JOIN sources AS s ON s.source_id = ps.source_id
WHERE us.job_id = %(job_id)s AND us.unit_id = %(unit_id)s
  AND p.workspace_key = %(workspace_key)s AND j.scope_key = %(workspace_key)s
ORDER BY ps.source_id;

-- name: artifact_run_plan_excerpts_current_for_unit
SELECT e.excerpt_id, e.chunk_id, e.locator_id, e.source_name, e.locator_label, e.text_snapshot,
       c.source_id AS current_source_id, c.text AS current_text,
       c.locator_id AS current_locator_id,
       l.label AS current_locator_label, s.filename AS current_source_name,
       s.is_current AS current_is_current
FROM artifact_run_unit_sources AS us
JOIN artifact_run_plan_excerpts AS e
  ON e.job_id = us.job_id AND e.source_id = us.source_id
LEFT JOIN chunks AS c ON c.chunk_id = e.chunk_id AND c.source_id = e.source_id
LEFT JOIN locators AS l ON l.locator_id = c.locator_id AND l.source_id = c.source_id
LEFT JOIN sources AS s ON s.source_id = e.source_id
WHERE us.job_id = %(job_id)s AND us.unit_id = %(unit_id)s
ORDER BY e.excerpt_id;

-- name: get_reserved_operation_call_for_artifact_unit
SELECT c.operation_id, c.attempt_no, c.status, o.workspace_key,
       o.deadline_at, clock_timestamp() AS observed_at
FROM operation_calls AS c
JOIN operations AS o ON o.operation_id = c.operation_id
WHERE c.operation_id = %(job_id)s AND c.attempt_no = %(attempt_no)s;

-- name: insert_artifact_run_unit_call
INSERT INTO artifact_run_unit_calls (job_id, unit_id, attempt_no)
VALUES (%(job_id)s, %(unit_id)s, %(attempt_no)s);

-- name: insert_artifact_run_unit_attempt
INSERT INTO artifact_run_unit_attempts (
    job_id, unit_id, attempt_no, outcome, candidate_text, candidate_sha256,
    candidate_truncated, validation_issues
) VALUES (
    %(job_id)s, %(unit_id)s, %(attempt_no)s, %(outcome)s, %(candidate_text)s,
    %(candidate_sha256)s, %(candidate_truncated)s, %(validation_issues)s
);

-- name: artifact_run_call_attempt_exists
SELECT 1 FROM artifact_run_unit_calls
WHERE job_id = %(job_id)s AND unit_id = %(unit_id)s AND attempt_no = %(attempt_no)s;

-- name: list_artifact_run_unit_attempts
SELECT a.*, u.unit_key, u.ordinal
FROM artifact_run_unit_attempts AS a
JOIN artifact_run_units AS u ON u.job_id = a.job_id AND u.unit_id = a.unit_id
JOIN artifact_run_plans AS p ON p.job_id = a.job_id
JOIN jobs AS j ON j.job_id = p.job_id
WHERE a.job_id = %(job_id)s AND p.workspace_key = %(workspace_key)s
  AND j.scope_key = %(workspace_key)s
ORDER BY u.ordinal, a.attempt_no;

-- name: artifact_run_unit_next_attempt_no
SELECT count(*) + 1 AS attempt_no
FROM artifact_run_unit_calls WHERE job_id = %(job_id)s AND unit_id = %(unit_id)s;

-- name: artifact_run_unit_invalid_attempts
SELECT count(*) AS n
FROM artifact_run_unit_attempts
WHERE job_id = %(job_id)s AND unit_id = %(unit_id)s AND outcome = 'invalid';

-- name: update_artifact_run_unit_needs_repair
UPDATE artifact_run_units
SET status = 'needs_repair', failure_code = 'invalid_output', updated_at = clock_timestamp()
WHERE job_id = %(job_id)s AND unit_id = %(unit_id)s AND status = 'calling'
RETURNING *;

-- name: update_artifact_run_unit_calling
UPDATE artifact_run_units
SET status = 'calling', updated_at = clock_timestamp()
WHERE job_id = %(job_id)s AND unit_id = %(unit_id)s AND status IN ('pending','needs_repair')
RETURNING *;

-- name: update_artifact_run_plan_running
UPDATE artifact_run_plans
SET status = 'running', updated_at = clock_timestamp()
WHERE job_id = %(job_id)s AND workspace_key = %(workspace_key)s
  AND status IN ('ready','running')
RETURNING *;

-- name: artifact_run_checkpoint_bytes
SELECT COALESCE(sum(octet_length(checkpoint::text)), 0)::bigint AS checkpoint_bytes
FROM artifact_run_units
WHERE job_id = %(job_id)s AND checkpoint IS NOT NULL;

-- name: artifact_run_checkpoint_usage
SELECT COALESCE(sum((checkpoint->'quota_usage'->>'blocks')::bigint), 0)::bigint AS blocks,
       COALESCE(sum((checkpoint->'quota_usage'->>'cells')::bigint), 0)::bigint AS cells
FROM artifact_run_units
WHERE job_id = %(job_id)s AND checkpoint IS NOT NULL;

-- name: measure_artifact_run_json_bytes
SELECT octet_length(%(value)s::jsonb::text)::bigint AS value_bytes;

-- name: artifact_run_call_link_exists
SELECT 1 FROM artifact_run_unit_calls
WHERE job_id = %(job_id)s AND unit_id = %(unit_id)s;

-- name: update_artifact_run_unit_validated
UPDATE artifact_run_units
SET status = 'validated', checkpoint = %(checkpoint)s,
    checkpoint_fingerprint = %(checkpoint_fingerprint)s,
    failure_code = NULL, updated_at = clock_timestamp()
WHERE job_id = %(job_id)s AND unit_id = %(unit_id)s
  AND status IN ('pending','calling') AND checkpoint IS NULL
RETURNING *;

-- name: update_artifact_run_plan_validated
UPDATE artifact_run_plans AS p
SET status = 'validated', updated_at = clock_timestamp()
WHERE p.job_id = %(job_id)s AND p.workspace_key = %(workspace_key)s
  AND p.status IN ('ready','running')
  AND NOT EXISTS (
      SELECT 1 FROM artifact_run_units AS u
      WHERE u.job_id = p.job_id AND u.status <> 'validated'
  )
RETURNING p.*;

-- name: update_artifact_run_unit_terminal
UPDATE artifact_run_units
SET status = %(status)s, failure_code = %(failure_code)s,
    updated_at = clock_timestamp()
WHERE job_id = %(job_id)s AND unit_id = %(unit_id)s
  AND status IN ('pending','calling','needs_repair')
RETURNING *;

-- name: update_artifact_run_plan_terminal
UPDATE artifact_run_plans
SET status = %(status)s, updated_at = clock_timestamp()
WHERE job_id = %(job_id)s AND workspace_key = %(workspace_key)s
  AND status IN ('ready','running')
RETURNING *;
