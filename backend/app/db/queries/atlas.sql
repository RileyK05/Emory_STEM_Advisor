-- name: list_atlas_sources
SELECT source_id
FROM sources
WHERE collection_id = %(collection_id)s
  AND content_type = %(content_type)s;

-- name: insert_course
INSERT INTO courses (
    course_id, collection_id, source_id, locator_id, code, subject, title,
    requisites_raw, parse_status, parser_version
)
VALUES (
    %(course_id)s, %(collection_id)s, %(source_id)s, %(locator_id)s, %(code)s,
    %(subject)s, %(title)s, %(requisites_raw)s, %(parse_status)s,
    %(parser_version)s
);

-- name: insert_prereq_mention
INSERT INTO prereq_mentions (
    course_id, kind, target_code, target_course_id, position
)
VALUES (
    %(course_id)s, %(kind)s, %(target_code)s, NULL, %(position)s
);

-- name: insert_prereq_node
INSERT INTO prereq_nodes (
    node_id, course_id, kind, parent_node_id, position, node_type,
    target_code, condition_text
)
VALUES (
    %(node_id)s, %(course_id)s, %(kind)s, %(parent_node_id)s, %(position)s,
    %(node_type)s, %(target_code)s, %(condition_text)s
);

-- name: resolve_mention_targets
UPDATE prereq_mentions m
SET target_course_id = c.course_id
FROM courses c
WHERE m.course_id = %(course_id)s
  AND c.collection_id = %(collection_id)s
  AND c.code = m.target_code;

-- name: list_courses_for_source
SELECT course_id, code, subject, title, requisites_raw, parse_status,
       locator_id, parser_version
FROM courses
WHERE source_id = %(source_id)s
ORDER BY code;

-- name: get_course_by_code
SELECT course_id, code, title, requisites_raw, parse_status
FROM courses
WHERE collection_id = %(collection_id)s AND code = %(code)s;

-- name: list_mentions_for_course
SELECT kind, target_code, target_course_id, position
FROM prereq_mentions
WHERE course_id = %(course_id)s
ORDER BY kind, position;

-- name: list_nodes_for_course
SELECT node_id, kind, parent_node_id, position, node_type, target_code,
       condition_text
FROM prereq_nodes
WHERE course_id = %(course_id)s
ORDER BY kind, position;
