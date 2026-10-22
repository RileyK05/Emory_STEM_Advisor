-- Persist term-specific Atlas offerings separately from master courses.

-- name: get_atlas_offerings_first_observation
SELECT min(observed_from)::date AS acquired_at
FROM atlas_offering_snapshots
WHERE source_id = %(source_id)s;

-- name: insert_atlas_offering_snapshot
INSERT INTO atlas_offering_snapshots (
    source_id, capture_set_hash, career, term, index_evidence_hash, announced_count,
    result_row_count, eligible_row_count, held_row_count, search_criteria, capture_hashes,
    observed_from, observed_until
) VALUES (
    %(source_id)s, %(capture_set_hash)s, %(career)s, %(term)s,
    %(index_evidence_hash)s, %(announced_count)s,
    %(result_row_count)s, %(eligible_row_count)s, %(held_row_count)s,
    %(search_criteria)s, %(capture_hashes)s, %(observed_from)s, %(observed_until)s
)
ON CONFLICT (source_id, capture_set_hash) DO NOTHING;

-- name: insert_atlas_offering_capture_file
INSERT INTO atlas_offering_capture_files (
    source_id, capture_set_hash, capture_hash, raw_path
) VALUES (
    %(source_id)s, %(capture_set_hash)s, %(capture_hash)s, %(raw_path)s
)
ON CONFLICT (source_id, capture_set_hash, capture_hash) DO UPDATE
SET raw_path = EXCLUDED.raw_path
WHERE atlas_offering_capture_files.raw_path = EXCLUDED.raw_path
RETURNING source_id;

-- name: list_atlas_offering_capture_files
SELECT capture_hash, raw_path
FROM atlas_offering_capture_files
WHERE source_id = %(source_id)s AND capture_set_hash = %(capture_set_hash)s
ORDER BY capture_hash;

-- name: insert_atlas_offering_row
INSERT INTO atlas_offering_rows (
    source_id, capture_set_hash, row_ordinal, result_cursor, raw_course_code, title, class_number,
    group_identity, is_grouped, matched_identity, srcdb, credits, description,
    restrictions, notes, notices, raw_metadata, evidence_hash, review_status,
    observed_at, capture_envelope_hash
) VALUES (
    %(source_id)s, %(capture_set_hash)s, %(row_ordinal)s, %(result_cursor)s,
    %(raw_course_code)s,
    %(title)s, %(class_number)s, %(group_identity)s, %(is_grouped)s,
    %(matched_identity)s, %(srcdb)s, %(credits)s, %(description)s, %(restrictions)s,
    %(notes)s, %(notices)s, %(raw_metadata)s, %(evidence_hash)s, %(review_status)s,
    %(observed_at)s, %(capture_envelope_hash)s
)
ON CONFLICT (source_id, capture_set_hash, row_ordinal) DO NOTHING;

-- The optional set hash is pinned when requested; otherwise return the newest
-- immutable acquisition set for the same canonical indexed source revision.

-- name: get_atlas_offering_snapshot_for_revision
SELECT o.source_id, o.capture_set_hash, s.collection_id, s.file_hash, s.index_revision, s.status,
       s.is_current, o.career, o.term, o.index_evidence_hash,
       o.announced_count, o.result_row_count, o.eligible_row_count,
       o.held_row_count, o.search_criteria,
       o.capture_hashes, o.observed_from, o.observed_until, o.created_at
FROM atlas_offering_snapshots o
JOIN sources s ON s.source_id = o.source_id
WHERE o.source_id = %(source_id)s
  AND s.index_revision = %(index_revision)s
  AND s.collection_id = %(collection_id)s
  AND s.workspace_key IS NULL
  AND s.status = 'indexed'
  AND (%(capture_set_hash)s::text IS NULL OR o.capture_set_hash = %(capture_set_hash)s)
ORDER BY o.observed_until DESC, o.capture_set_hash DESC
LIMIT 1;

-- name: list_atlas_offering_rows_for_revision
SELECT r.row_ordinal, r.result_cursor, r.raw_course_code, r.title,
       r.class_number, r.group_identity, r.is_grouped, r.matched_identity,
       r.srcdb, r.credits, r.description, r.restrictions, r.notes,
       r.notices, r.raw_metadata, r.evidence_hash, r.review_status,
       r.observed_at, r.capture_envelope_hash
FROM atlas_offering_rows r
JOIN sources s ON s.source_id = r.source_id
WHERE r.source_id = %(source_id)s
  AND r.capture_set_hash = %(capture_set_hash)s
  AND s.index_revision = %(index_revision)s
  AND s.collection_id = %(collection_id)s
  AND s.workspace_key IS NULL
  AND s.status = 'indexed'
  AND (%(include_held)s OR r.review_status = 'eligible')
ORDER BY r.row_ordinal;
