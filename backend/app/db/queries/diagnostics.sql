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

-- name: diagnostic_audit_courses
SELECT course_id, code, subject, title, requisites_raw, parse_status, parser_version
FROM courses
WHERE collection_id = %(collection_id)s AND lifecycle = 'current'
ORDER BY code;

-- name: diagnostic_audit_prereq_nodes
SELECT n.course_id, n.node_id, n.kind, n.parent_node_id, n.position, n.node_type,
       n.target_code, n.condition_text
FROM prereq_nodes n
JOIN courses co ON co.course_id = n.course_id
WHERE co.collection_id = %(collection_id)s AND co.lifecycle = 'current';

-- name: diagnostic_audit_prereq_mentions
SELECT m.course_id, m.kind, m.target_code, m.target_course_id
FROM prereq_mentions m
JOIN courses co ON co.course_id = m.course_id
WHERE co.collection_id = %(collection_id)s AND co.lifecycle = 'current';

-- name: diagnostic_audit_program_slots
SELECT p.title AS program_title, p.kind AS program_kind, pcm.target_code,
       target.code AS resolved_code, target.lifecycle AS resolved_lifecycle
FROM program_course_mentions pcm
JOIN programs p ON p.program_id = pcm.program_id
LEFT JOIN courses target ON target.course_id = pcm.target_course_id
WHERE p.collection_id = %(collection_id)s;

-- name: diagnostic_audit_retired_codes
SELECT code FROM courses
WHERE collection_id = %(collection_id)s AND lifecycle = 'retired';

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

-- name: diagnostic_recent_research_runs
SELECT run_id, operation_id, status, stop_reason FROM research_runs
WHERE workspace_key = %(workspace_key)s ORDER BY created_at DESC LIMIT 5;
