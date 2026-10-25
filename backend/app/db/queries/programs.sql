-- name: lock_program_collection
SELECT collection_id FROM collections
WHERE collection_id = %(collection_id)s FOR UPDATE;

-- name: retire_missing_programs
UPDATE programs SET is_active = false, current_rule_release_id = NULL
WHERE collection_id = %(collection_id)s
  AND NOT (program_id = ANY(%(program_ids)s::uuid[]));

-- name: clear_program_projection
DELETE FROM program_items WHERE program_id = %(program_id)s;

-- name: clear_program_field_evidence
DELETE FROM program_field_evidence WHERE program_id = %(program_id)s;

-- name: archive_incompatible_program_releases
UPDATE program_rule_releases r SET status = 'archived', updated_at = now()
FROM programs p LEFT JOIN source_provenance provenance ON provenance.source_id = p.source_id
WHERE r.program_id = p.program_id AND p.collection_id = %(collection_id)s
  AND r.status IN ('draft', 'reviewing', 'approved', 'published')
  AND (NOT p.is_active OR p.source_id IS DISTINCT FROM r.source_id
       OR p.source_index_revision IS DISTINCT FROM r.source_index_revision
       OR p.source_sha256 IS DISTINCT FROM r.source_sha256
       OR p.parsed_content_digest IS DISTINCT FROM r.bound_program_digest
       OR coalesce(provenance.metadata_revision, 0) <> r.source_metadata_revision
       OR (r.status = 'published' AND p.current_rule_release_id IS DISTINCT FROM r.release_id));

-- name: clear_inactive_program_release_pointers
UPDATE programs p SET current_rule_release_id = NULL
FROM program_rule_releases r
WHERE r.release_id = p.current_rule_release_id AND p.collection_id = %(collection_id)s
  AND r.status <> 'published';

-- name: insert_program
INSERT INTO programs (
    program_id, collection_id, title, kind, degree, hours, courses_required,
    department, catalog, source_url, first_page, last_page, parser_version
)
VALUES (
    %(program_id)s, %(collection_id)s, %(title)s, %(kind)s, %(degree)s, %(hours)s,
    %(courses_required)s, %(department)s, %(catalog)s, %(source_url)s,
    %(first_page)s, %(last_page)s, %(parser_version)s
)
ON CONFLICT (program_id) DO UPDATE SET
    title = EXCLUDED.title, kind = EXCLUDED.kind, degree = EXCLUDED.degree,
    hours = EXCLUDED.hours, courses_required = EXCLUDED.courses_required,
    department = EXCLUDED.department, catalog = EXCLUDED.catalog,
    source_url = EXCLUDED.source_url, first_page = EXCLUDED.first_page,
    last_page = EXCLUDED.last_page, parser_version = EXCLUDED.parser_version,
    is_active = true;

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
  AND p.is_active
  AND c.code = m.target_code
  AND m.target_course_id IS DISTINCT FROM c.course_id;

-- name: program_mention_counts
SELECT count(*) AS mentions, count(m.target_course_id) AS resolved
FROM program_course_mentions m
JOIN programs p ON p.program_id = m.program_id
WHERE p.collection_id = %(collection_id)s AND p.is_active;

-- name: list_programs
SELECT program_id, title, kind, degree, hours, courses_required, department
FROM programs
WHERE collection_id = %(collection_id)s AND is_active
ORDER BY title;

-- name: list_advising_course_subjects
SELECT DISTINCT split_part(code, ' ', 1) AS subject
FROM courses WHERE collection_id = %(collection_id)s
UNION
SELECT DISTINCT split_part(m.target_code, ' ', 1) AS subject
FROM program_course_mentions m JOIN programs p ON p.program_id = m.program_id
WHERE p.collection_id = %(collection_id)s AND p.is_active;

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
  AND p.is_active
  AND m.target_code = %(code)s
ORDER BY p.title;
