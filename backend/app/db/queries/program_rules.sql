-- Program requirement rules and release management (Feature 12).

-- name: get_program_inspection_detail
SELECT p.program_id, p.collection_id, p.title, p.kind, p.catalog, p.source_url, p.is_active,
       p.first_page, p.last_page, p.parser_version, p.source_id,
       p.source_index_revision, p.source_sha256, p.origin_key, p.parsed_content_digest,
       p.binding_status, p.bound_at, p.current_rule_release_id,
       p.degree, p.hours, p.courses_required, p.department,
       s.filename AS source_filename, s.is_current AS source_is_current,
       s.index_revision AS current_source_index_revision, s.file_hash AS current_source_sha256,
       s.status AS source_status,
       prov.source_category, prov.metadata_revision AS source_metadata_revision,
       prov.lifecycle_status AS source_lifecycle_status
FROM programs p
LEFT JOIN sources s ON s.source_id = p.source_id
LEFT JOIN source_provenance prov ON prov.source_id = p.source_id
WHERE p.program_id = %(program_id)s;

-- name: list_program_items_for_inspection
SELECT pi.program_id, pi."position", pi.section, pi.text, pi.choose,
       COALESCE(
           (SELECT json_agg(pcm.target_code ORDER BY pcm.target_code)
            FROM program_course_mentions pcm
            WHERE pcm.program_id = pi.program_id AND pcm."position" = pi."position"),
           '[]'::json
       ) AS mentions,
       COALESCE(
           (SELECT json_agg(
               json_build_object(
                   'chunkId', pie.chunk_id,
                   'locatorId', pie.locator_id,
                   'ordinal', pie.ordinal,
                   'spanStart', pie.span_start,
                   'spanEnd', pie.span_end,
                   'textDigest', pie.text_digest
               ) ORDER BY pie.ordinal
           )
           FROM program_item_evidence pie
           WHERE pie.program_id = pi.program_id AND pie."position" = pi."position"),
           '[]'::json
       ) AS evidence
FROM program_items pi
WHERE pi.program_id = %(program_id)s
ORDER BY pi."position";

-- Identity facts must be verified against the exact indexed chunk span at every
-- rule-release transition. Historical evidence rows intentionally survive
-- reindexing, so callers also compare these identities with the locked program.
-- name: list_program_identity_evidence
SELECT e.field, e.ordinal, e.chunk_id, e.locator_id, e.source_id, e.source_index_revision,
       e.span_start, e.span_end, e.text_digest,
       c.source_id AS chunk_source_id, c.start_char AS chunk_start_char,
       c.text AS chunk_text
FROM program_field_evidence e
LEFT JOIN chunks c ON c.chunk_id = e.chunk_id
WHERE e.program_id = %(program_id)s
  AND e.field IN ('title', 'degree')
ORDER BY e.field, e.ordinal;

-- name: insert_program_rule_release
INSERT INTO program_rule_releases (
    release_id, program_id, collection_id, source_id,
    source_index_revision, source_sha256, release_version,
    status, rules_tree, rules_digest, coverage_summary,
    created_by_user_id, notes, bound_program_digest, source_metadata_revision
) VALUES (
    %(release_id)s, %(program_id)s, %(collection_id)s, %(source_id)s,
    %(source_index_revision)s, %(source_sha256)s, %(release_version)s,
    %(status)s, %(rules_tree)s, %(rules_digest)s, %(coverage_summary)s,
    %(created_by_user_id)s, %(notes)s, %(bound_program_digest)s, %(source_metadata_revision)s
) RETURNING *;

-- name: lock_program_rule_review
SELECT program_id FROM programs WHERE program_id = %(program_id)s FOR UPDATE;

-- name: lock_program_rule_release
SELECT * FROM program_rule_releases WHERE release_id = %(release_id)s FOR UPDATE;

-- name: get_program_rule_release
SELECT * FROM program_rule_releases
WHERE release_id = %(release_id)s;

-- name: get_latest_rule_release_version
SELECT COALESCE(MAX(release_version), 0) AS max_version
FROM program_rule_releases
WHERE program_id = %(program_id)s;

-- name: list_rule_releases_for_program
SELECT release_id, program_id, collection_id, source_id,
       source_index_revision, source_sha256, release_version,
       status, rules_digest, coverage_summary,
       created_by_user_id, approved_by_user_id, approved_at,
       notes, created_at, updated_at
FROM program_rule_releases
WHERE program_id = %(program_id)s
ORDER BY release_version DESC;

-- name: update_program_rule_release_status
UPDATE program_rule_releases
SET status = %(status)s,
    approved_by_user_id = %(approved_by_user_id)s,
    approved_at = %(approved_at)s,
    updated_at = now()
WHERE release_id = %(release_id)s
RETURNING *;

-- name: set_program_current_rule_release
UPDATE programs
SET current_rule_release_id = %(release_id)s
WHERE program_id = %(program_id)s;

-- name: insert_program_rule_review
INSERT INTO program_rule_reviews (
    review_id, release_id, reviewer_user_id, action, review_notes
) VALUES (
    %(review_id)s, %(release_id)s, %(reviewer_user_id)s, %(action)s, %(review_notes)s
) RETURNING *;
