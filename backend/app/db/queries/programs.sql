-- name: delete_programs_for_collection
DELETE FROM programs
WHERE collection_id = %(collection_id)s;

-- name: insert_program
INSERT INTO programs (
    program_id, collection_id, title, kind, degree, hours, courses_required,
    department, catalog, source_url, first_page, last_page, parser_version
)
VALUES (
    %(program_id)s, %(collection_id)s, %(title)s, %(kind)s, %(degree)s, %(hours)s,
    %(courses_required)s, %(department)s, %(catalog)s, %(source_url)s,
    %(first_page)s, %(last_page)s, %(parser_version)s
);

-- name: insert_program_item
INSERT INTO program_items (program_id, position, section, text, choose)
VALUES (%(program_id)s, %(position)s, %(section)s, %(text)s, %(choose)s);

-- name: insert_program_course_mention
INSERT INTO program_course_mentions (program_id, position, target_code)
VALUES (%(program_id)s, %(position)s, %(target_code)s)
ON CONFLICT DO NOTHING;

-- name: resolve_program_mentions
UPDATE program_course_mentions m
SET target_course_id = c.course_id
FROM programs p, courses c
WHERE p.program_id = m.program_id
  AND p.collection_id = %(collection_id)s
  AND c.collection_id = p.collection_id
  AND c.code = m.target_code
  AND m.target_course_id IS DISTINCT FROM c.course_id;

-- name: program_mention_counts
SELECT count(*) AS mentions, count(m.target_course_id) AS resolved
FROM program_course_mentions m
JOIN programs p ON p.program_id = m.program_id
WHERE p.collection_id = %(collection_id)s;

-- name: list_programs
SELECT program_id, title, kind, degree, hours, courses_required, department
FROM programs
WHERE collection_id = %(collection_id)s
ORDER BY title;

-- name: list_program_items
SELECT i.position, i.section, i.text, i.choose,
       coalesce(array_agg(m.target_code ORDER BY m.target_code)
                FILTER (WHERE m.target_code IS NOT NULL), '{}') AS courses
FROM program_items i
LEFT JOIN program_course_mentions m
       ON m.program_id = i.program_id AND m.position = i.position
WHERE i.program_id = %(program_id)s
GROUP BY i.position, i.section, i.text, i.choose
ORDER BY i.position;

-- name: programs_requiring_course
SELECT DISTINCT p.program_id, p.title, p.kind
FROM program_course_mentions m
JOIN programs p ON p.program_id = m.program_id
WHERE p.collection_id = %(collection_id)s
  AND m.target_code = %(code)s
ORDER BY p.title;
