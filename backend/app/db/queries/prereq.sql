-- name: prereq_self_chunks
SELECT c.chunk_id, c.source_id, c.chunk_index
FROM courses co
JOIN chunks c ON c.locator_id = co.locator_id
JOIN sources s ON s.source_id = c.source_id
WHERE co.collection_id = %(collection_id)s
  AND co.code = %(code)s
  AND s.status = 'indexed'
  AND s.is_current
  AND (s.workspace_key IS NULL OR s.workspace_key = %(workspace_key)s)
  AND (%(source_ids)s::uuid[] IS NULL OR c.source_id = ANY(%(source_ids)s::uuid[]))
ORDER BY c.chunk_index;

-- name: prereq_upstream
-- An edge names the course it requires. When it names an Oxford course, the
-- edge reaches that course and the Atlanta course it is the other campus's
-- offering of. The codes are expanded before the join so the index on
-- courses.code is still used; a correlated subquery in the join condition
-- made this seam eighty times slower.
WITH edge_targets AS (
    SELECT edge.course_id, edge.kind, edge.node_type, target.code AS target_code
    FROM prereq_nodes edge
    JOIN courses requiring ON requiring.course_id = edge.course_id
    CROSS JOIN LATERAL (
        SELECT edge.target_code AS code
        UNION ALL
        SELECT e.atlanta_code
        FROM course_campus_equivalents e
        WHERE e.collection_id = requiring.collection_id
          AND e.oxford_code = edge.target_code
    ) target
)
SELECT DISTINCT c.chunk_id, c.source_id, c.chunk_index
FROM edge_targets edge
JOIN courses requiring ON requiring.course_id = edge.course_id
                       AND requiring.parse_status = 'parsed'
                       AND requiring.lifecycle = 'current'
                       AND NOT EXISTS (
                           SELECT 1
                           FROM atlas_requirement_holds hold
                           JOIN sources master ON master.source_id = hold.source_id
                           WHERE master.collection_id = requiring.collection_id
                             AND master.content_type = 'application/x-course-atlas+jsonl'
                             AND master.status = 'indexed'
                             AND master.is_current
                             AND hold.course_code = requiring.code
                             AND hold.master_file_hash = master.file_hash
                             AND hold.status = 'active'
                       )
JOIN courses target ON target.collection_id = requiring.collection_id
                   AND target.code = edge.target_code
                   AND target.lifecycle = 'current'
                   AND NOT EXISTS (
                       SELECT 1
                       FROM atlas_requirement_holds hold
                       JOIN sources master ON master.source_id = hold.source_id
                       WHERE master.collection_id = target.collection_id
                         AND master.content_type = 'application/x-course-atlas+jsonl'
                         AND master.status = 'indexed'
                         AND master.is_current
                         AND hold.course_code = target.code
                         AND hold.master_file_hash = master.file_hash
                         AND hold.status = 'active'
                   )
JOIN chunks c ON c.locator_id = target.locator_id
JOIN sources s ON s.source_id = c.source_id
WHERE edge.course_id = %(course_id)s
  AND s.collection_id = %(collection_id)s
  AND edge.kind IN ('prerequisite', 'corequisite')
  AND edge.node_type = 'course'
  AND s.status = 'indexed'
  AND s.is_current
  AND (s.workspace_key IS NULL OR s.workspace_key = %(workspace_key)s)
  AND (%(source_ids)s::uuid[] IS NULL OR c.source_id = ANY(%(source_ids)s::uuid[]))
ORDER BY c.chunk_index;

-- name: prereq_downstream
-- The mirror: a course's Oxford counterpart is the same course, so an edge
-- naming either is an edge naming the course the student asked about. The
-- codes are expanded before the join so the index on courses.code is used.
WITH target_codes AS (
    SELECT codes.code
    FROM courses target
    CROSS JOIN LATERAL (
        SELECT target.code AS code
        UNION ALL
        SELECT e.oxford_code
        FROM course_campus_equivalents e
        WHERE e.collection_id = target.collection_id
          AND e.atlanta_code = target.code
    ) codes
    WHERE target.course_id = %(course_id)s
)
SELECT DISTINCT c.chunk_id, c.source_id, c.chunk_index
FROM target_codes
JOIN prereq_nodes edge ON edge.target_code = target_codes.code
JOIN courses requiring ON requiring.course_id = edge.course_id
                       AND requiring.parse_status = 'parsed'
                       AND requiring.lifecycle = 'current'
                       AND NOT EXISTS (
                           SELECT 1
                           FROM atlas_requirement_holds hold
                           JOIN sources master ON master.source_id = hold.source_id
                           WHERE hold.source_id = requiring.source_id
                             AND hold.course_code = requiring.code
                             AND hold.master_file_hash = master.file_hash
                             AND hold.status = 'active'
                       )
JOIN courses target ON target.collection_id = requiring.collection_id
                   AND target.course_id = %(course_id)s
                   AND target.lifecycle = 'current'
JOIN chunks c ON c.locator_id = requiring.locator_id
JOIN sources s ON s.source_id = c.source_id
WHERE target.course_id = %(course_id)s
  AND NOT EXISTS (
      SELECT 1
      FROM atlas_requirement_holds hold
      JOIN sources master ON master.source_id = hold.source_id
      WHERE master.collection_id = target.collection_id
        AND master.content_type = 'application/x-course-atlas+jsonl'
        AND master.status = 'indexed'
        AND master.is_current
        AND hold.course_code = target.code
        AND hold.master_file_hash = master.file_hash
        AND hold.status = 'active'
  )
  AND s.collection_id = %(collection_id)s
  AND edge.kind IN ('prerequisite', 'corequisite')
  AND edge.node_type = 'course'
  AND s.status = 'indexed'
  AND s.is_current
  AND (s.workspace_key IS NULL OR s.workspace_key = %(workspace_key)s)
  AND (%(source_ids)s::uuid[] IS NULL OR c.source_id = ANY(%(source_ids)s::uuid[]))
ORDER BY c.chunk_index;

-- name: artifact_prerequisite_nodes_for_code
SELECT n.node_id, n.kind, n.parent_node_id, n.position, n.node_type,
       n.target_code, n.condition_text
FROM prereq_nodes n
JOIN courses co ON co.course_id = n.course_id
WHERE co.collection_id = %(collection_id)s
  AND co.code = %(code)s
  AND co.lifecycle = 'current'
  AND co.parse_status = 'parsed'
  AND n.kind = 'prerequisite';

-- name: get_course_id_by_code
SELECT course_id
FROM courses
WHERE collection_id = %(collection_id)s AND code = %(code)s;
