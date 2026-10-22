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
SELECT DISTINCT c.chunk_id, c.source_id, c.chunk_index
FROM prereq_nodes edge
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
SELECT DISTINCT c.chunk_id, c.source_id, c.chunk_index
FROM prereq_nodes edge
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
                   AND target.code = edge.target_code
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

-- name: get_course_id_by_code
SELECT course_id
FROM courses
WHERE collection_id = %(collection_id)s AND code = %(code)s;
