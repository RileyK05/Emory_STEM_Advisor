-- name: list_atlas_sources
SELECT source_id
FROM sources
WHERE collection_id = %(collection_id)s
  AND content_type = %(content_type)s
  AND is_current;

-- name: list_collection_course_codes
SELECT code FROM courses
WHERE collection_id = %(collection_id)s AND lifecycle = 'current'
ORDER BY code;

-- name: lock_atlas_source_for_reparse
SELECT source_id
FROM sources
WHERE collection_id = %(collection_id)s
  AND file_hash = %(file_hash)s
  AND content_type = %(content_type)s
  AND workspace_key IS NULL
  AND status = 'indexed' AND is_current
FOR UPDATE;

-- name: advance_atlas_parser_revision
UPDATE sources
SET index_revision = index_revision + 1, updated_at = now()
WHERE source_id = %(source_id)s
RETURNING index_revision;

-- name: insert_course
INSERT INTO courses (
    course_id, collection_id, source_id, locator_id, code, subject, title,
    requisites_raw, parse_status, parser_version, canonical_code, lifecycle,
    authority
)
VALUES (
    %(course_id)s, %(collection_id)s, %(source_id)s, %(locator_id)s, %(code)s,
    %(subject)s, %(title)s, %(requisites_raw)s, %(parse_status)s,
    %(parser_version)s, %(canonical_code)s, %(lifecycle)s, %(authority)s
)
ON CONFLICT (course_id) DO UPDATE SET
    source_id = EXCLUDED.source_id,
    locator_id = EXCLUDED.locator_id,
    subject = EXCLUDED.subject,
    title = EXCLUDED.title,
    requisites_raw = EXCLUDED.requisites_raw,
    parse_status = EXCLUDED.parse_status,
    parser_version = EXCLUDED.parser_version,
    canonical_code = EXCLUDED.canonical_code,
    lifecycle = EXCLUDED.lifecycle,
    authority = EXCLUDED.authority;

-- name: delete_prereq_mentions_for_course
DELETE FROM prereq_mentions WHERE course_id = %(course_id)s;

-- name: delete_prereq_nodes_for_course
DELETE FROM prereq_nodes WHERE course_id = %(course_id)s;

-- name: retire_courses_not_in_atlas
-- A course that left the published catalog keeps its historical row, source,
-- locator and parsed evidence. Current reads filter it out by lifecycle.
-- Admin-controlled identities are never retired by a baseline import: a removed
-- baseline row is a conflict for review, not a silent deletion of reviewed work.
UPDATE courses
SET lifecycle = 'retired'
WHERE collection_id = %(collection_id)s
  AND lifecycle = 'current'
  AND authority NOT IN ('admin_correction', 'admin_created')
  AND NOT (code = ANY(%(codes)s::text[]));

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
       locator_id, parser_version, canonical_code, lifecycle, authority,
       admin_revision
FROM courses
WHERE source_id = %(source_id)s
ORDER BY code;

-- name: list_retired_courses
SELECT course_id, code, title, subject, canonical_code
FROM courses
WHERE collection_id = %(collection_id)s
  AND lifecycle = 'retired'
  AND (%(pattern)s::text IS NULL
       OR code ILIKE %(pattern)s OR title ILIKE %(pattern)s)
ORDER BY code
LIMIT 100;

-- name: get_current_atlas_requirement_holds
SELECT h.hold_id, h.source_id, h.course_code, h.master_file_hash,
       h.evidence_capture_sha256
FROM atlas_requirement_holds h
JOIN sources s ON s.source_id = h.source_id
WHERE s.collection_id = %(collection_id)s
  AND s.origin_key = 'emory:course-atlas'
  AND s.is_current = true AND h.status = 'active';

-- name: get_current_course_atlas_source
SELECT s.source_id, p.source_url, p.publisher
FROM sources s
LEFT JOIN source_provenance p ON p.source_id = s.source_id
WHERE s.collection_id = %(collection_id)s
  AND s.origin_key = 'emory:course-atlas' AND s.is_current = true;

-- name: get_indexed_atlas_offering_snapshot
SELECT snapshot.source_id, snapshot.career, snapshot.term,
       snapshot.capture_set_hash, source.index_revision
FROM atlas_offering_snapshots snapshot
JOIN sources source ON source.source_id = snapshot.source_id
WHERE source.collection_id = %(collection_id)s
  AND source.status = 'indexed'
  AND source.is_current = true
  AND source.content_type = 'text/markdown'
  AND source.origin_key = ('emory:course-atlas-offerings:' || snapshot.career || ':' || snapshot.term)
  AND snapshot.career = %(career)s AND snapshot.term = %(term)s
  AND snapshot.capture_set_hash = %(capture_set_hash)s
FOR SHARE OF source;

-- name: list_canonical_group_members
SELECT canonical_code, array_agg(code ORDER BY code) AS codes
FROM courses
WHERE collection_id = %(collection_id)s
  AND canonical_code = ANY(%(canonical_codes)s::text[])
GROUP BY canonical_code;

-- name: get_course_by_code
SELECT course_id, code, title, requisites_raw, parse_status, lifecycle
FROM courses
WHERE collection_id = %(collection_id)s AND code = %(code)s;

-- name: set_course_canonical_code
UPDATE courses SET canonical_code = %(canonical_code)s
WHERE course_id = %(course_id)s;

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
