-- name: insert_job
INSERT INTO jobs (
    job_id, kind, scope_key, idempotency_key, payload, status, max_attempts, available_at
) VALUES (
    %(job_id)s, %(kind)s, %(scope_key)s, %(idempotency_key)s, %(payload)s,
    'queued', %(max_attempts)s, %(available_at)s
)
ON CONFLICT (scope_key, idempotency_key) DO NOTHING
RETURNING *;

-- name: get_job_by_idempotency
SELECT * FROM jobs WHERE scope_key = %(scope_key)s AND idempotency_key = %(idempotency_key)s;

-- name: get_job
SELECT * FROM jobs WHERE job_id = %(job_id)s;

-- name: claim_next_job
WITH candidate AS (
    SELECT job_id
    FROM jobs
    WHERE status = 'queued' AND available_at <= %(now)s
    ORDER BY available_at, created_at, job_id
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
UPDATE jobs AS j
SET status = 'running',
    attempt_count = j.attempt_count + 1,
    lease_owner = %(worker_id)s,
    lease_token = %(lease_token)s,
    lease_expires_at = %(lease_expires_at)s,
    heartbeat_at = %(now)s,
    cancel_requested = false,
    updated_at = %(now)s,
    finished_at = NULL,
    error = NULL
FROM candidate
WHERE j.job_id = candidate.job_id
RETURNING j.*;

-- name: heartbeat_job
UPDATE jobs
SET heartbeat_at = %(now)s,
    lease_expires_at = %(lease_expires_at)s,
    updated_at = %(now)s
WHERE job_id = %(job_id)s AND status = 'running' AND lease_token = %(lease_token)s
  AND cancel_requested = false AND lease_expires_at > %(now)s
RETURNING *;

-- name: request_job_cancel
UPDATE jobs
SET status = CASE WHEN status = 'queued' THEN 'cancelled' ELSE status END,
    cancel_requested = CASE WHEN status = 'queued' THEN false ELSE true END,
    lease_owner = CASE WHEN status = 'queued' THEN NULL ELSE lease_owner END,
    lease_token = CASE WHEN status = 'queued' THEN NULL ELSE lease_token END,
    lease_expires_at = CASE WHEN status = 'queued' THEN NULL ELSE lease_expires_at END,
    finished_at = CASE WHEN status = 'queued' THEN %(now)s ELSE finished_at END,
    updated_at = %(now)s
WHERE job_id = %(job_id)s AND status IN ('queued', 'running')
RETURNING *;

-- name: finish_job_success
UPDATE jobs
SET status = CASE WHEN cancel_requested THEN 'cancelled' ELSE 'succeeded' END,
    result = CASE WHEN cancel_requested THEN NULL ELSE %(result)s END,
    error = NULL,
    lease_owner = NULL,
    lease_token = NULL,
    lease_expires_at = NULL,
    cancel_requested = false,
    finished_at = %(now)s,
    updated_at = %(now)s
WHERE job_id = %(job_id)s AND status = 'running' AND lease_token = %(lease_token)s
  AND lease_expires_at > %(now)s
RETURNING *;

-- name: finish_job_cancelled
UPDATE jobs
SET status = 'cancelled',
    result = NULL,
    error = NULL,
    lease_owner = NULL,
    lease_token = NULL,
    lease_expires_at = NULL,
    cancel_requested = false,
    finished_at = %(now)s,
    updated_at = %(now)s
WHERE job_id = %(job_id)s AND status = 'running' AND lease_token = %(lease_token)s
  AND lease_expires_at > %(now)s
RETURNING *;

-- name: finish_job_failure
WITH unknown_calls AS (
    UPDATE operation_calls AS c
    SET status = 'unknown', accounted_at = clock_timestamp()
    FROM jobs AS j
    WHERE c.operation_id = j.job_id
      AND j.job_id = %(job_id)s
      AND j.kind = 'artifact.run'
      AND j.status = 'running'
      AND j.lease_token = %(lease_token)s
      AND j.lease_expires_at > %(now)s
      AND c.status = 'reserved'
    RETURNING c.operation_id
), unknown_units AS (
    UPDATE artifact_run_units AS u
    SET status = 'unknown', failure_code = 'unknown_outcome', updated_at = clock_timestamp()
    FROM unknown_calls AS c
    WHERE u.job_id = c.operation_id AND u.status = 'calling'
    RETURNING u.job_id
), unknown_plans AS (
    UPDATE artifact_run_plans AS p
    SET status = 'unknown', updated_at = clock_timestamp()
    FROM unknown_calls AS u
    WHERE p.job_id = u.operation_id AND p.status IN ('ready','running')
    RETURNING p.job_id
), safe_artifact_plans AS (
    SELECT p.job_id
    FROM artifact_run_plans AS p
    WHERE p.status IN ('ready','running','validated')
      AND NOT EXISTS (SELECT 1 FROM unknown_calls u WHERE u.operation_id = p.job_id)
      AND NOT EXISTS (
          SELECT 1
          FROM operation_calls AS c
          LEFT JOIN artifact_run_unit_calls AS uc
            ON uc.job_id = c.operation_id AND uc.attempt_no = c.attempt_no
          LEFT JOIN artifact_run_units AS u
            ON u.job_id = uc.job_id AND u.unit_id = uc.unit_id
          LEFT JOIN artifact_run_unit_attempts AS a
            ON a.job_id = uc.job_id AND a.unit_id = uc.unit_id AND a.attempt_no = uc.attempt_no
          WHERE c.operation_id = p.job_id
            AND (uc.job_id IS NULL OR c.status NOT IN ('completed','unknown') OR NOT (
                (a.outcome = 'invalid' AND u.status IN ('needs_repair','validated'))
                OR (u.status = 'validated' AND u.checkpoint IS NOT NULL
                    AND uc.attempt_no = (
                        SELECT max(latest.attempt_no) FROM artifact_run_unit_calls AS latest
                        WHERE latest.job_id = uc.job_id AND latest.unit_id = uc.unit_id
                    ))
            ))
      )
      AND NOT EXISTS (
          SELECT 1
          FROM artifact_run_plan_sources ps
          LEFT JOIN sources s ON s.source_id = ps.source_id
          WHERE ps.job_id = p.job_id AND (
              s.source_id IS NULL OR s.collection_id <> ps.collection_id
              OR s.file_hash <> ps.file_hash OR s.index_revision <> ps.index_revision
              OR s.status <> 'indexed'
              OR NOT s.is_current
              OR (s.workspace_key IS NOT NULL AND s.workspace_key <> p.workspace_key)
          )
      )
      AND NOT EXISTS (
          SELECT 1
          FROM artifact_run_plan_excerpts e
          LEFT JOIN chunks c ON c.chunk_id = e.chunk_id AND c.source_id = e.source_id
          LEFT JOIN locators l ON l.locator_id = c.locator_id AND l.source_id = c.source_id
          LEFT JOIN sources s ON s.source_id = e.source_id
          WHERE e.job_id = p.job_id AND (
              c.chunk_id IS NULL OR c.text <> e.text_snapshot
              OR c.locator_id <> e.locator_id OR l.locator_id IS NULL
              OR l.label <> e.locator_label OR s.filename <> e.source_name
              OR NOT s.is_current
          )
      )
      AND (p.target_artifact_id IS NULL OR EXISTS (
          SELECT 1 FROM artifacts a
          WHERE a.artifact_id = p.target_artifact_id
            AND a.workspace_key = p.workspace_key
            AND a.head_revision_id = p.target_revision_id
      ))
      AND NOT EXISTS (
          SELECT 1
          FROM artifact_run_units u
          LEFT JOIN connection_profiles cp ON cp.profile_id = u.profile_id
          WHERE u.job_id = p.job_id AND u.profile_id IS NOT NULL
            AND (cp.profile_id IS NULL OR NOT cp.enabled OR cp.revision <> u.profile_revision)
      )
)
UPDATE jobs AS j
SET status = CASE
        WHEN j.cancel_requested THEN 'cancelled'
        WHEN j.kind = 'artifact.run' AND EXISTS (
            SELECT 1 FROM operation_calls c WHERE c.operation_id = j.job_id
        ) AND EXISTS (SELECT 1 FROM safe_artifact_plans p WHERE p.job_id = j.job_id)
             AND %(retry)s THEN 'queued'
        WHEN j.kind = 'artifact.run' AND EXISTS (
            SELECT 1 FROM operation_calls c WHERE c.operation_id = j.job_id
        ) THEN 'failed'
        WHEN %(retry)s THEN 'queued'
        ELSE 'failed'
    END,
    available_at = CASE
        WHEN NOT j.cancel_requested AND %(retry)s AND (
             j.kind <> 'artifact.run' OR NOT EXISTS (
                 SELECT 1 FROM operation_calls c WHERE c.operation_id = j.job_id
             ) OR EXISTS (SELECT 1 FROM safe_artifact_plans p WHERE p.job_id = j.job_id)
        ) THEN %(retry_at)s
        ELSE j.available_at END,
    result = NULL,
    error = CASE
        WHEN j.cancel_requested THEN NULL
        WHEN j.kind = 'artifact.run' AND EXISTS (
            SELECT 1 FROM operation_calls c
            WHERE c.operation_id = j.job_id AND c.status IN ('reserved', 'unknown')
        ) THEN jsonb_build_object(
            'type', 'unknown_outcome',
            'message', 'A model call may have completed; output was withheld to prevent duplicate execution.'
        )
        ELSE %(error)s END,
    lease_owner = NULL,
    lease_token = NULL,
    lease_expires_at = NULL,
    cancel_requested = false,
    finished_at = CASE
        WHEN NOT j.cancel_requested AND %(retry)s AND (
             j.kind <> 'artifact.run' OR NOT EXISTS (
                 SELECT 1 FROM operation_calls c WHERE c.operation_id = j.job_id
             ) OR EXISTS (SELECT 1 FROM safe_artifact_plans p WHERE p.job_id = j.job_id)
        ) THEN NULL
        ELSE %(now)s END,
    updated_at = %(now)s
WHERE j.job_id = %(job_id)s AND j.status = 'running' AND j.lease_token = %(lease_token)s
  AND j.lease_expires_at > %(now)s
RETURNING *;

-- name: recover_expired_jobs
WITH expired AS (
    SELECT job_id
    FROM jobs
    WHERE status = 'running' AND lease_expires_at <= %(now)s
    ORDER BY lease_expires_at, job_id
    FOR UPDATE SKIP LOCKED
    LIMIT %(limit)s
), recovered AS (
    UPDATE operation_calls AS c
    SET status = 'unknown', accounted_at = clock_timestamp()
    FROM expired AS e
    JOIN jobs AS j ON j.job_id = e.job_id
    WHERE c.operation_id = e.job_id
      AND j.kind = 'artifact.run'
      AND c.status = 'reserved'
    RETURNING c.operation_id
), unknown_units AS (
    UPDATE artifact_run_units AS u
    SET status = 'unknown', failure_code = 'unknown_outcome', updated_at = clock_timestamp()
    FROM recovered AS c
    WHERE u.job_id = c.operation_id AND u.status = 'calling'
    RETURNING u.job_id
), unknown_plans AS (
    UPDATE artifact_run_plans AS p
    SET status = 'unknown', updated_at = clock_timestamp()
    FROM recovered AS u
    WHERE p.job_id = u.operation_id AND p.status IN ('ready','running')
    RETURNING p.job_id
), safe_artifact_plans AS (
    SELECT p.job_id
    FROM artifact_run_plans AS p
    WHERE p.status IN ('ready','running','validated')
      AND NOT EXISTS (SELECT 1 FROM recovered r WHERE r.operation_id = p.job_id)
      AND NOT EXISTS (
          SELECT 1
          FROM operation_calls AS c
          LEFT JOIN artifact_run_unit_calls AS uc
            ON uc.job_id = c.operation_id AND uc.attempt_no = c.attempt_no
          LEFT JOIN artifact_run_units AS u
            ON u.job_id = uc.job_id AND u.unit_id = uc.unit_id
          LEFT JOIN artifact_run_unit_attempts AS a
            ON a.job_id = uc.job_id AND a.unit_id = uc.unit_id AND a.attempt_no = uc.attempt_no
          WHERE c.operation_id = p.job_id
            AND (uc.job_id IS NULL OR c.status NOT IN ('completed','unknown') OR NOT (
                (a.outcome = 'invalid' AND u.status IN ('needs_repair','validated'))
                OR (u.status = 'validated' AND u.checkpoint IS NOT NULL
                    AND uc.attempt_no = (
                        SELECT max(latest.attempt_no) FROM artifact_run_unit_calls AS latest
                        WHERE latest.job_id = uc.job_id AND latest.unit_id = uc.unit_id
                    ))
            ))
      )
      AND NOT EXISTS (
          SELECT 1
          FROM artifact_run_plan_sources ps
          LEFT JOIN sources s ON s.source_id = ps.source_id
          WHERE ps.job_id = p.job_id AND (
              s.source_id IS NULL OR s.collection_id <> ps.collection_id
              OR s.file_hash <> ps.file_hash OR s.index_revision <> ps.index_revision
              OR s.status <> 'indexed'
              OR NOT s.is_current
              OR (s.workspace_key IS NOT NULL AND s.workspace_key <> p.workspace_key)
          )
      )
      AND NOT EXISTS (
          SELECT 1
          FROM artifact_run_plan_excerpts e
          LEFT JOIN chunks c ON c.chunk_id = e.chunk_id AND c.source_id = e.source_id
          LEFT JOIN locators l ON l.locator_id = c.locator_id AND l.source_id = c.source_id
          LEFT JOIN sources s ON s.source_id = e.source_id
          WHERE e.job_id = p.job_id AND (
              c.chunk_id IS NULL OR c.text <> e.text_snapshot
              OR c.locator_id <> e.locator_id OR l.locator_id IS NULL
              OR l.label <> e.locator_label OR s.filename <> e.source_name
              OR NOT s.is_current
          )
      )
      AND (p.target_artifact_id IS NULL OR EXISTS (
          SELECT 1 FROM artifacts a
          WHERE a.artifact_id = p.target_artifact_id
            AND a.workspace_key = p.workspace_key
            AND a.head_revision_id = p.target_revision_id
      ))
      AND NOT EXISTS (
          SELECT 1
          FROM artifact_run_units u
          LEFT JOIN connection_profiles cp ON cp.profile_id = u.profile_id
          WHERE u.job_id = p.job_id AND u.profile_id IS NOT NULL
            AND (cp.profile_id IS NULL OR NOT cp.enabled OR cp.revision <> u.profile_revision)
      )
), terminalized AS (
    UPDATE jobs AS j
    SET status = CASE
            WHEN j.cancel_requested THEN 'cancelled'
            WHEN j.kind = 'artifact.run' AND EXISTS (
                SELECT 1 FROM operation_calls c WHERE c.operation_id = j.job_id
            ) AND EXISTS (SELECT 1 FROM safe_artifact_plans p WHERE p.job_id = j.job_id)
                 AND j.attempt_count < j.max_attempts THEN 'queued'
            WHEN j.kind = 'artifact.run' AND EXISTS (
                SELECT 1 FROM operation_calls c WHERE c.operation_id = j.job_id
            ) THEN 'failed'
            WHEN j.attempt_count < j.max_attempts THEN 'queued'
            ELSE 'failed'
        END,
        available_at = CASE
            WHEN NOT j.cancel_requested AND j.attempt_count < j.max_attempts
                 AND (j.kind <> 'artifact.run' OR NOT EXISTS (
                     SELECT 1 FROM operation_calls c WHERE c.operation_id = j.job_id
                 ) OR EXISTS (SELECT 1 FROM safe_artifact_plans p WHERE p.job_id = j.job_id)) THEN %(now)s
            ELSE j.available_at
        END,
        error = CASE
            WHEN j.cancel_requested THEN NULL
            WHEN j.kind = 'artifact.run' AND EXISTS (
                SELECT 1 FROM operation_calls c WHERE c.operation_id = j.job_id
                  AND c.status IN ('reserved','unknown')
            ) THEN jsonb_build_object(
                'type', 'unknown_outcome',
                'message', 'A model call may have completed; output was withheld to prevent duplicate execution.'
            )
            WHEN j.attempt_count < j.max_attempts THEN jsonb_build_object('type', 'lease_expired', 'message', 'Worker lease expired; job returned to the queue.')
            ELSE jsonb_build_object('type', 'lease_expired', 'message', 'Worker lease expired after the final attempt.')
        END,
        lease_owner = NULL,
        lease_token = NULL,
        lease_expires_at = NULL,
        cancel_requested = false,
        finished_at = CASE
            WHEN j.cancel_requested OR j.attempt_count >= j.max_attempts
                 OR (j.kind = 'artifact.run' AND EXISTS (
                     SELECT 1 FROM operation_calls c WHERE c.operation_id = j.job_id
                 ) AND NOT EXISTS (SELECT 1 FROM safe_artifact_plans p WHERE p.job_id = j.job_id)) THEN %(now)s
            ELSE NULL
        END,
        updated_at = %(now)s
    FROM expired
    WHERE j.job_id = expired.job_id
    RETURNING j.job_id, j.status, j.attempt_count, j.error
)
INSERT INTO job_events (job_id, event_type, payload, created_at)
SELECT job_id,
       CASE status WHEN 'queued' THEN 'lease_recovered' WHEN 'cancelled' THEN 'cancelled' ELSE 'failed' END,
       jsonb_build_object('status', status, 'attempt_count', attempt_count, 'error', error),
       %(now)s
FROM terminalized
RETURNING job_id;

-- name: append_job_progress
WITH valid_lease AS (
    UPDATE jobs
    SET updated_at = %(now)s
    WHERE job_id = %(job_id)s AND status = 'running'
      AND lease_token = %(lease_token)s AND lease_expires_at > %(now)s
      AND cancel_requested = false
    RETURNING job_id
)
INSERT INTO job_events (job_id, event_type, payload, created_at)
SELECT job_id, 'progress', %(payload)s, %(now)s FROM valid_lease
RETURNING event_id, job_id, event_type, payload, created_at;

-- name: append_job_event
INSERT INTO job_events (job_id, event_type, payload, created_at)
VALUES (%(job_id)s, %(event_type)s, %(payload)s, %(now)s)
RETURNING event_id, job_id, event_type, payload, created_at;

-- name: list_job_events
SELECT event_id, job_id, event_type, payload, created_at
FROM job_events
WHERE job_id = %(job_id)s AND event_id > %(after_event_id)s
ORDER BY event_id
LIMIT %(limit)s;

-- name: job_cancel_state
SELECT status, cancel_requested, lease_token, lease_expires_at
FROM jobs WHERE job_id = %(job_id)s;

-- name: lock_job_for_success_publish
SELECT status, cancel_requested, lease_token, lease_expires_at
FROM jobs
WHERE job_id = %(job_id)s
FOR UPDATE;
