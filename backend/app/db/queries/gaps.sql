-- name: get_gap_trace_for_collection
SELECT trace_id, collection_id
FROM retrieval_traces
WHERE trace_id = %(trace_id)s;

-- name: list_gap_scope_sources
SELECT source_id FROM sources
WHERE collection_id = %(collection_id)s
  AND source_id = ANY(%(source_ids)s::uuid[])
  AND is_current
  AND (workspace_key IS NULL OR workspace_key = %(workspace_key)s);

-- name: ensure_gap_identity
INSERT INTO retrieval_gaps (
    gap_id, workspace_key, collection_id, query, normalized_query, query_key,
    scope_source_ids
)
VALUES (
    %(gap_id)s, %(workspace_key)s, %(collection_id)s, %(query)s,
    %(normalized_query)s, %(query_key)s, %(scope_source_ids)s
)
ON CONFLICT (workspace_key, collection_id, query_key) DO NOTHING;

-- name: get_gap_identity_for_update
SELECT * FROM retrieval_gaps
WHERE workspace_key = %(workspace_key)s
  AND collection_id = %(collection_id)s AND query_key = %(query_key)s
FOR UPDATE;

-- name: insert_gap_observation
INSERT INTO gap_observations (workspace_key, trace_id, gap_id, event_kind)
SELECT %(workspace_key)s, t.trace_id, g.gap_id, %(event_kind)s
FROM retrieval_gaps g
JOIN retrieval_traces t ON t.trace_id = %(trace_id)s
WHERE g.gap_id = %(gap_id)s AND g.workspace_key = %(workspace_key)s
  AND g.collection_id = t.collection_id
ON CONFLICT (workspace_key, trace_id, event_kind) DO NOTHING
RETURNING gap_id;

-- name: get_gap_observation_identity
SELECT gap_id FROM gap_observations
WHERE workspace_key = %(workspace_key)s AND trace_id = %(trace_id)s
  AND event_kind = %(event_kind)s;

-- name: get_gap_observations_for_trace
SELECT DISTINCT gap_id FROM gap_observations
WHERE workspace_key = %(workspace_key)s AND trace_id = %(trace_id)s;

-- name: increment_gap_observation
UPDATE retrieval_gaps
SET occurrence_count = occurrence_count + CASE
        WHEN %(event_kind)s IN ('retrieval_refusal', 'generation_withheld') THEN 1 ELSE 0 END,
    recurrence_count = recurrence_count + CASE
        WHEN %(event_kind)s IN ('retrieval_refusal', 'generation_withheld')
             AND occurrence_count > 0 THEN 1 ELSE 0 END,
    successful_recurrence_count = successful_recurrence_count + CASE
        WHEN %(event_kind)s = 'retrieval_success' THEN 1 ELSE 0 END,
    state = CASE
        WHEN %(event_kind)s IN ('retrieval_refusal', 'generation_withheld') THEN 'open'
        ELSE state END,
    revision = revision + CASE
        WHEN state = 'resolved'
             AND %(event_kind)s IN ('retrieval_refusal', 'generation_withheld') THEN 1
        ELSE 0 END,
    resolution_source_ids = CASE
        WHEN %(event_kind)s IN ('retrieval_refusal', 'generation_withheld') THEN NULL
        ELSE resolution_source_ids END,
    resolution_note = CASE
        WHEN %(event_kind)s IN ('retrieval_refusal', 'generation_withheld') THEN NULL
        ELSE resolution_note END,
    resolved_by = CASE
        WHEN %(event_kind)s IN ('retrieval_refusal', 'generation_withheld') THEN NULL
        ELSE resolved_by END,
    resolved_at = CASE
        WHEN %(event_kind)s IN ('retrieval_refusal', 'generation_withheld') THEN NULL
        ELSE resolved_at END,
    last_seen_at = clock_timestamp()
WHERE gap_id = %(gap_id)s AND workspace_key = %(workspace_key)s
RETURNING *;

-- name: list_gaps_for_workspace
SELECT g.*,
       CASE
           WHEN g.state = 'resolved' AND EXISTS (
               SELECT 1 FROM source_invalidation_outbox o
               WHERE o.source_id = ANY(g.resolution_source_ids)
                 AND g.resolved_at IS NOT NULL
                 AND o.created_at >= g.resolved_at
           ) THEN 'ineligible'
           ELSE 'current'
       END AS evidence_state
FROM retrieval_gaps g
WHERE g.workspace_key = %(workspace_key)s
  AND (%(collection_id)s::uuid IS NULL OR g.collection_id = %(collection_id)s)
ORDER BY CASE WHEN g.state = 'open' THEN 0 ELSE 1 END,
         g.first_seen_at ASC, g.gap_id ASC
LIMIT 100;

-- name: gap_metrics_for_workspace
SELECT COALESCE(sum(recurrence_count + successful_recurrence_count), 0)::bigint
           AS repeated_gap_observations,
       COALESCE(sum(successful_recurrence_count), 0)::bigint
           AS successful_repeated_observations
FROM retrieval_gaps
WHERE workspace_key = %(workspace_key)s
  AND (%(collection_id)s::uuid IS NULL OR collection_id = %(collection_id)s);

-- name: get_gap_for_workspace_update
SELECT * FROM retrieval_gaps
WHERE gap_id = %(gap_id)s AND workspace_key = %(workspace_key)s
FOR UPDATE;

-- name: prove_gap_resolution_sources
SELECT s.source_id, s.collection_id, s.workspace_key, s.status,
       counts.chunk_count, counts.locator_count
FROM sources s
CROSS JOIN LATERAL (
    SELECT (SELECT count(*) FROM chunks c WHERE c.source_id = s.source_id) AS chunk_count,
           (SELECT count(*) FROM locators l WHERE l.source_id = s.source_id) AS locator_count
) counts
WHERE s.source_id = ANY(%(source_ids)s::uuid[])
  AND s.collection_id = %(collection_id)s
  AND (s.workspace_key IS NULL OR s.workspace_key = %(workspace_key)s)
  AND s.status = 'indexed'
  AND s.is_current;

-- name: insert_gap_resolution_history
INSERT INTO gap_resolutions (gap_id, revision, source_ids, note, resolved_by)
VALUES (%(gap_id)s, %(revision)s, %(source_ids)s, %(note)s, %(resolved_by)s);

-- name: resolve_gap
UPDATE retrieval_gaps
SET state = 'resolved', revision = revision + 1,
    resolution_source_ids = %(resolution_source_ids)s,
    resolution_note = %(resolution_note)s, resolved_by = %(resolved_by)s,
    resolved_at = clock_timestamp()
WHERE gap_id = %(gap_id)s AND workspace_key = %(workspace_key)s
  AND state = 'open' AND revision = %(expected_revision)s
RETURNING *;
