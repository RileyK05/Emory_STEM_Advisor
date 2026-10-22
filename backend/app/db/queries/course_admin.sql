-- Reviewed course maintenance: search, detail, change history and publication.

-- name: list_admin_courses
SELECT c.course_id, c.code, c.subject, c.title, c.canonical_code,
       c.parse_status, c.lifecycle, c.authority, c.admin_revision,
       c.requisites_raw,
       (position('_OX ' in c.code) > 0) AS oxford,
       (SELECT count(*) FROM prereq_mentions m
         WHERE m.course_id = c.course_id) AS mention_count,
       (SELECT count(*) FROM prereq_mentions m
         WHERE m.course_id = c.course_id AND m.target_course_id IS NULL) AS unresolved_count,
       (SELECT count(DISTINCT pm.program_id)
          FROM program_course_mentions pm
          JOIN programs p ON p.program_id = pm.program_id
         WHERE p.collection_id = c.collection_id
           AND p.is_active
           AND pm.target_code = c.code) AS program_count
FROM courses c
WHERE c.collection_id = %(collection_id)s
  AND (%(pattern)s = '' OR c.code ILIKE %(pattern)s OR c.title ILIKE %(pattern)s)
  AND (%(subject)s = '' OR c.subject = %(subject)s)
  AND (%(lifecycle)s = '' OR c.lifecycle = %(lifecycle)s)
  AND (%(authority)s = '' OR c.authority = %(authority)s)
  AND (%(campus)s = ''
       OR (%(campus)s = 'oxford' AND position('_OX ' in c.code) > 0)
       OR (%(campus)s = 'atlanta' AND position('_OX ' in c.code) = 0))
ORDER BY c.code
LIMIT %(limit)s OFFSET %(offset)s;

-- name: count_admin_courses
SELECT count(*) AS total
FROM courses c
WHERE c.collection_id = %(collection_id)s
  AND (%(pattern)s = '' OR c.code ILIKE %(pattern)s OR c.title ILIKE %(pattern)s)
  AND (%(subject)s = '' OR c.subject = %(subject)s)
  AND (%(lifecycle)s = '' OR c.lifecycle = %(lifecycle)s)
  AND (%(authority)s = '' OR c.authority = %(authority)s)
  AND (%(campus)s = ''
       OR (%(campus)s = 'oxford' AND position('_OX ' in c.code) > 0)
       OR (%(campus)s = 'atlanta' AND position('_OX ' in c.code) = 0));

-- name: list_admin_course_subjects
SELECT DISTINCT subject FROM courses
WHERE collection_id = %(collection_id)s
ORDER BY subject;

-- name: get_admin_course
SELECT c.course_id, c.collection_id, c.code, c.subject, c.title, c.canonical_code,
       c.requisites_raw, c.parse_status, c.parser_version, c.lifecycle,
       c.authority, c.admin_revision, c.source_id, c.locator_id,
       (position('_OX ' in c.code) > 0) AS oxford,
       s.filename, s.content_type, s.origin_key, s.index_revision,
       s.status AS source_status, s.is_current AS source_is_current,
       provenance.publisher, provenance.source_url, provenance.title AS source_title,
       provenance.institution, provenance.issuing_unit, provenance.document_roles,
       provenance.applicability, provenance.applicability_status
FROM courses c
JOIN sources s ON s.source_id = c.source_id
LEFT JOIN source_provenance provenance ON provenance.source_id = c.source_id
WHERE c.collection_id = %(collection_id)s AND c.code = %(code)s;

-- name: get_course_admin_state
SELECT course_id, collection_id, code, authority, admin_revision, title,
       requisites_raw, parse_status, lifecycle, source_id, locator_id
FROM courses
WHERE collection_id = %(collection_id)s AND code = %(code)s;

-- name: list_admin_course_dependents
SELECT m.course_id, c.code, c.title, m.kind, m.target_code,
       (m.target_course_id IS NOT NULL) AS resolved
FROM prereq_mentions m
JOIN courses c ON c.course_id = m.course_id
WHERE m.target_code = %(code)s
  AND c.collection_id = %(collection_id)s
ORDER BY c.code;

-- name: count_stale_audits_for_course
SELECT count(*) AS total
FROM degree_audits a
WHERE a.is_stale = false
  AND a.program_id IN (
      SELECT pm.program_id FROM program_course_mentions pm
      WHERE pm.target_code = %(code)s
  );

-- name: mark_degree_audits_stale_for_course
UPDATE degree_audits
SET is_stale = true
WHERE is_stale = false
  AND program_id IN (
      SELECT pm.program_id FROM program_course_mentions pm
      WHERE pm.target_code = %(code)s
  )
RETURNING audit_id;

-- name: count_current_topic_builds
SELECT count(*) AS total
FROM sim_builds
WHERE collection_id = %(collection_id)s AND is_current AND graph_version = 2;

-- name: retire_current_topic_builds
UPDATE sim_builds SET is_current = false
WHERE collection_id = %(collection_id)s AND is_current AND graph_version = 2;

-- name: unresolve_mentions_for_retired_course
-- A retired identity cannot satisfy another course's prerequisite. The edge
-- stays as an explicit unresolved dependency rather than disappearing.
UPDATE prereq_mentions
SET target_course_id = NULL
WHERE target_course_id = %(course_id)s;

-- name: resolve_mentions_for_restored_course
UPDATE prereq_mentions m
SET target_course_id = %(course_id)s
FROM courses c
WHERE c.course_id = %(course_id)s
  AND c.lifecycle = 'current'
  AND m.target_code = c.code
  AND m.target_course_id IS NULL
  AND m.course_id IN (
      SELECT course_id FROM courses WHERE collection_id = %(collection_id)s
  );

-- name: get_open_draft_for_course
SELECT change_id, status FROM course_changes
WHERE collection_id = %(collection_id)s AND code = %(code)s AND status = 'draft';

-- name: get_conflicted_change_for_course
SELECT change_id FROM course_changes
WHERE collection_id = %(collection_id)s AND code = %(code)s
  AND status = 'conflicted'
LIMIT 1;

-- name: get_published_change_for_course
SELECT change_id, change_kind, base_value, proposed_value, published_revision
FROM course_changes
WHERE collection_id = %(collection_id)s AND code = %(code)s
  AND status = 'published'
ORDER BY created_at DESC
LIMIT 1;

-- name: list_admin_controlled_courses
SELECT course_id, code, authority, admin_revision, title, requisites_raw, lifecycle
FROM courses
WHERE collection_id = %(collection_id)s
  AND authority IN ('admin_correction', 'admin_created')
ORDER BY code;

-- name: insert_course_change
INSERT INTO course_changes (
    change_id, collection_id, course_id, code, change_kind, status,
    expected_revision, base_authority, base_value, proposed_value, reason,
    actor_user_id, conflict_note
) VALUES (
    %(change_id)s, %(collection_id)s, %(course_id)s, %(code)s, %(change_kind)s,
    %(status)s, %(expected_revision)s, %(base_authority)s, %(base_value)s,
    %(proposed_value)s, %(reason)s, %(actor_user_id)s, %(conflict_note)s
)
RETURNING change_id, status;

-- name: get_course_change
SELECT change_id, collection_id, course_id, code, change_kind, status,
       expected_revision, published_revision, base_authority, base_value,
       proposed_value, reason, actor_user_id, published_by_user_id,
       conflict_note, created_at, updated_at, published_at
FROM course_changes
WHERE change_id = %(change_id)s;

-- name: lock_course_change
SELECT change_id, status, expected_revision, code, change_kind, collection_id
FROM course_changes
WHERE change_id = %(change_id)s
FOR UPDATE;

-- name: list_course_changes_for_course
SELECT change_id, code, change_kind, status, expected_revision, published_revision,
       base_authority, base_value, proposed_value, reason, actor_user_id,
       published_by_user_id, conflict_note,
       created_at, updated_at, published_at
FROM course_changes
WHERE collection_id = %(collection_id)s AND code = %(code)s
ORDER BY created_at DESC;

-- name: list_conflicted_course_changes
SELECT change_id, code, change_kind, status, conflict_note, created_at
FROM course_changes
WHERE collection_id = %(collection_id)s AND status = 'conflicted'
ORDER BY created_at DESC
LIMIT %(limit)s;

-- name: update_course_change_status
UPDATE course_changes
SET status = %(status)s, updated_at = now(), conflict_note = %(conflict_note)s
WHERE change_id = %(change_id)s
RETURNING change_id, status;

-- name: mark_course_change_published
UPDATE course_changes
SET status = 'published', published_revision = %(published_revision)s,
    published_by_user_id = %(published_by_user_id)s, published_at = now(),
    updated_at = now(), conflict_note = NULL
WHERE change_id = %(change_id)s AND status = 'draft'
RETURNING change_id, status, published_revision;

-- name: update_course_from_change
UPDATE courses
SET title = %(title)s, requisites_raw = %(requisites_raw)s,
    parse_status = %(parse_status)s, parser_version = %(parser_version)s,
    authority = %(authority)s, admin_revision = admin_revision + 1
WHERE course_id = %(course_id)s AND admin_revision = %(expected_revision)s
RETURNING admin_revision;

-- name: update_course_lifecycle_from_change
UPDATE courses
SET lifecycle = %(lifecycle)s, authority = %(authority)s,
    admin_revision = admin_revision + 1
WHERE course_id = %(course_id)s AND admin_revision = %(expected_revision)s
RETURNING admin_revision;

-- name: insert_admin_course
INSERT INTO courses (
    course_id, collection_id, source_id, locator_id, code, subject, title,
    requisites_raw, parse_status, parser_version, canonical_code, lifecycle,
    authority, admin_revision
) VALUES (
    %(course_id)s, %(collection_id)s, %(source_id)s, %(locator_id)s, %(code)s,
    %(subject)s, %(title)s, %(requisites_raw)s, %(parse_status)s,
    %(parser_version)s, %(canonical_code)s, %(lifecycle)s,
    %(authority)s, 1
)
ON CONFLICT (course_id) DO NOTHING
RETURNING admin_revision;

-- name: insert_admin_course_locator
INSERT INTO locators (
    locator_id, source_id, ordinal, locator_type, label, start_char, end_char
)
SELECT %(locator_id)s, %(source_id)s,
       coalesce((SELECT max(ordinal) + 1 FROM locators
                 WHERE source_id = %(source_id)s), 0),
       'admin_record', %(label)s, 0, 1
RETURNING locator_id;

-- name: get_admin_course_source
SELECT source_id, filename, content_type, is_current
FROM sources
WHERE collection_id = %(collection_id)s
  AND source_id = %(source_id)s
  AND status = 'indexed'
  AND is_current;

-- name: get_default_admin_course_source
SELECT s.source_id
FROM sources s
WHERE s.collection_id = %(collection_id)s
  AND s.status = 'indexed'
  AND s.is_current
  AND s.workspace_key IS NULL
ORDER BY (s.content_type = 'application/x-course-atlas+jsonl') DESC,
         s.updated_at DESC
LIMIT 1;
