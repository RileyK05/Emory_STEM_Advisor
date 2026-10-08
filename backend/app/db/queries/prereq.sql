-- name: prereq_self_chunks
SELECT c.chunk_id, c.source_id, c.chunk_index
FROM courses co
JOIN chunks c ON c.locator_id = co.locator_id
JOIN sources s ON s.source_id = c.source_id
WHERE co.collection_id = %(collection_id)s
  AND co.code = %(code)s
  AND s.status = 'indexed'
ORDER BY c.chunk_index;

-- name: prereq_upstream
SELECT DISTINCT c.chunk_id, c.source_id, c.chunk_index
FROM prereq_mentions m
JOIN courses target ON target.course_id = m.target_course_id
JOIN chunks c ON c.locator_id = target.locator_id
JOIN sources s ON s.source_id = c.source_id
WHERE m.course_id = %(course_id)s
  AND m.kind IN ('prerequisite', 'corequisite')
  AND s.status = 'indexed'
ORDER BY c.chunk_index;

-- name: prereq_downstream
SELECT DISTINCT c.chunk_id, c.source_id, c.chunk_index
FROM prereq_mentions m
JOIN courses dependent ON dependent.course_id = m.course_id
JOIN chunks c ON c.locator_id = dependent.locator_id
JOIN sources s ON s.source_id = c.source_id
WHERE m.target_course_id = %(course_id)s
  AND m.kind IN ('prerequisite', 'corequisite')
  AND s.status = 'indexed'
ORDER BY c.chunk_index;

-- name: get_course_id_by_code
SELECT course_id
FROM courses
WHERE collection_id = %(collection_id)s AND code = %(code)s;
