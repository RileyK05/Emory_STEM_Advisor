-- name: diagnostic_current_chunks
SELECT ch.chunk_id, ch.source_id, ch.chunk_index, ch.text,
       s.filename AS source_name, l.label AS locator_label,
       co.code, co.title, co.requisites_raw, co.parse_status,
       e.embedding, e.dim
FROM chunks ch
JOIN sources s ON s.source_id = ch.source_id
JOIN locators l ON l.locator_id = ch.locator_id
LEFT JOIN courses co ON co.source_id = ch.source_id AND co.locator_id = ch.locator_id
LEFT JOIN chunk_embeddings e ON e.chunk_id = ch.chunk_id AND e.model = %(model)s
WHERE s.collection_id = %(collection_id)s AND s.status = 'indexed' AND s.is_current
  AND (s.workspace_key IS NULL OR s.workspace_key = %(workspace_key)s)
ORDER BY ch.source_id, ch.chunk_index, ch.chunk_id;

-- name: diagnostic_connection_states
SELECT coalesce(state, 'unknown') AS state, count(*) AS connections
FROM pg_stat_activity
WHERE datname = current_database()
GROUP BY state
ORDER BY state;

-- name: count_chunks_for_collection
SELECT count(*) AS chunks
FROM chunks c
JOIN sources s ON s.source_id = c.source_id
WHERE s.collection_id = %(collection_id)s
  AND s.status = 'indexed' AND s.is_current;

-- name: diagnostic_recent_research_runs
SELECT run_id, operation_id, status, stop_reason FROM research_runs
WHERE workspace_key = %(workspace_key)s ORDER BY created_at DESC LIMIT 5;

-- name: diagnostic_research_tool_results
SELECT call_id, tool_id, status, result_json FROM tool_executions
WHERE operation_id = %(operation_id)s AND workspace_key = %(workspace_key)s
ORDER BY created_at;

-- name: diagnostic_keyword_weighted
SELECT c.chunk_id, c.source_id, c.chunk_index,
       ts_rank_cd(c.search_vector, to_tsquery('english', %(q)s), 32)
       + %(title_weight)s * ts_rank_cd(
           to_tsvector('english', coalesce(co.title, l.label)), to_tsquery('english', %(q)s), 32)
           AS score
FROM chunks c
JOIN sources s ON s.source_id = c.source_id
JOIN locators l ON l.locator_id = c.locator_id
LEFT JOIN courses co ON co.source_id = c.source_id AND co.locator_id = c.locator_id
WHERE s.collection_id = %(collection_id)s AND s.status = 'indexed' AND s.is_current
  AND (s.workspace_key IS NULL OR s.workspace_key = %(workspace_key)s)
  AND c.search_vector @@ to_tsquery('english', %(q)s)
ORDER BY score DESC, c.source_id, c.chunk_index
LIMIT %(limit)s;

-- name: diagnostic_keyword_idf
WITH scoped AS MATERIALIZED (
    SELECT c.chunk_id, c.source_id, c.chunk_index, c.search_vector,
           to_tsvector('english', coalesce(co.title, l.label)) AS title_vector
    FROM chunks c
    JOIN sources s ON s.source_id = c.source_id
    JOIN locators l ON l.locator_id = c.locator_id
    LEFT JOIN courses co ON co.source_id = c.source_id AND co.locator_id = c.locator_id
    WHERE s.collection_id = %(collection_id)s AND s.status = 'indexed' AND s.is_current
      AND (s.workspace_key IS NULL OR s.workspace_key = %(workspace_key)s)
), terms AS (
    SELECT DISTINCT to_tsquery('english', token) AS term
    FROM unnest(%(tokens)s::text[]) token
), weights AS (
    SELECT term, ln(1 + (SELECT count(*) FROM scoped)::float /
                   (1 + count(*) FILTER (WHERE search_vector @@ term))) AS weight
    FROM terms CROSS JOIN scoped GROUP BY term
)
SELECT chunk_id, source_id, chunk_index,
       sum(weight * (ts_rank_cd(search_vector, term, 32)
           + %(title_weight)s * ts_rank_cd(title_vector, term, 32))) AS score
FROM scoped CROSS JOIN weights
WHERE search_vector @@ term
GROUP BY chunk_id, source_id, chunk_index
ORDER BY score DESC, source_id, chunk_index LIMIT %(limit)s;
