-- Program projection -> source evidence binding (migration 037).
--
-- A program fact is only citable when it names the source revision it was parsed
-- from. `binding_status` says so explicitly instead of leaving it to be guessed:
--
--   'bound'              the projection names a verified source revision and has
--                        supporting chunk evidence for its items
--   'unbound'            no binding established (all legacy rows: the parse input
--                        is not a database source and there is no provenance to
--                        backfill from). Reads must not present these as cited
--                        requirements.
--   'evidence_unresolved' a source identity was resolved but exact span alignment
--                        failed for one or more items, so those stay discovery
--                        leads only.
--
-- Nothing here infers a binding from a filename or title match. Binding is
-- written from the exact-SHA ingest manifest identity only.

-- name: bind_program_source
UPDATE programs
SET source_id = %(source_id)s,
    source_index_revision = %(source_index_revision)s,
    source_sha256 = %(source_sha256)s,
    origin_key = %(origin_key)s,
    parsed_content_digest = %(parsed_content_digest)s,
    binding_status = %(binding_status)s,
    bound_at = now()
WHERE program_id = %(program_id)s
RETURNING program_id, binding_status;

-- name: get_program_binding
SELECT program_id, collection_id, title, kind,
       source_id, source_index_revision, source_sha256, origin_key,
       parsed_content_digest, binding_status, bound_at
FROM programs
WHERE program_id = %(program_id)s;

-- name: list_programs_with_binding
SELECT program_id, title, kind,
       source_id, source_index_revision, source_sha256, origin_key,
       parsed_content_digest, binding_status, bound_at
FROM programs
WHERE collection_id = %(collection_id)s
ORDER BY kind, title;

-- Ordered supporting chunks for one parsed requirement item. chunk_id and
-- locator_id deliberately carry no foreign key: the evidence must survive
-- reindexing and source removal, like citation and excerpt snapshots.
-- name: insert_program_item_evidence
INSERT INTO program_item_evidence (
    program_id, "position", ordinal, chunk_id, locator_id, source_id,
    source_index_revision, span_start, span_end, text_digest
) VALUES (
    %(program_id)s, %(position)s, %(ordinal)s, %(chunk_id)s, %(locator_id)s, %(source_id)s,
    %(source_index_revision)s, %(span_start)s, %(span_end)s, %(text_digest)s
)
ON CONFLICT (program_id, "position", chunk_id, ordinal) DO NOTHING
RETURNING program_id, "position", ordinal;

-- name: list_program_item_evidence
SELECT program_id, "position", ordinal, chunk_id, locator_id, source_id,
       source_index_revision, span_start, span_end, text_digest
FROM program_item_evidence
WHERE program_id = %(program_id)s
ORDER BY "position", ordinal;

-- A requirement item with no rows here is a discovery lead only; it must never be
-- presented as a cited requirement.
-- name: list_program_items_without_evidence
SELECT i.program_id, i."position", i.section, i.text, i.choose
FROM program_items i
WHERE i.program_id = %(program_id)s
  AND NOT EXISTS (
      SELECT 1 FROM program_item_evidence e
      WHERE e.program_id = i.program_id AND e."position" = i."position"
  )
ORDER BY i."position";

-- The one current, indexed, shared source with exactly this ingest identity
-- (scraper manifest origin key plus content SHA-256). No title or filename match.
-- name: resolve_program_source
SELECT source_id, index_revision, file_hash, origin_key
FROM sources
WHERE collection_id = %(collection_id)s
  AND workspace_key IS NULL
  AND origin_key = %(origin_key)s
  AND file_hash = %(file_hash)s
  AND status = 'indexed'
  AND is_current;

-- name: list_source_chunks_for_binding
SELECT chunk_id, locator_id, chunk_index, start_char, end_char, text
FROM chunks
WHERE source_id = %(source_id)s
ORDER BY chunk_index;

-- name: insert_program_field_evidence
INSERT INTO program_field_evidence (
    program_id, field, ordinal, chunk_id, locator_id, source_id,
    source_index_revision, span_start, span_end, text_digest
) VALUES (
    %(program_id)s, %(field)s, %(ordinal)s, %(chunk_id)s, %(locator_id)s, %(source_id)s,
    %(source_index_revision)s, %(span_start)s, %(span_end)s, %(text_digest)s
);

-- name: list_program_field_evidence
SELECT program_id, field, ordinal, chunk_id, locator_id, source_id,
       source_index_revision, span_start, span_end, text_digest
FROM program_field_evidence
WHERE program_id = %(program_id)s
ORDER BY field, ordinal;

-- Inside the import transaction: the bound source must still be that exact
-- current revision, and stays so until the projection is published.
-- name: lock_program_source
SELECT source_id
FROM sources
WHERE source_id = %(source_id)s
  AND index_revision = %(index_revision)s
  AND file_hash = %(file_hash)s
  AND status = 'indexed'
  AND is_current
FOR SHARE;

-- Share-locked so a concurrent re-import (which deletes the collection's
-- programs) cannot commit between the binding check and the item reads.
-- name: get_program_for_research
SELECT program_id, title, kind, degree, hours, courses_required, department, catalog,
       source_id, source_index_revision, parsed_content_digest, binding_status
FROM programs
WHERE program_id = %(program_id)s
FOR SHARE;
