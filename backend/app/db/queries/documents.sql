-- name: get_source_by_hash
SELECT source_id, status, index_revision, filename, is_current, origin_key
FROM sources
WHERE collection_id = %(collection_id)s AND file_hash = %(file_hash)s
  AND workspace_key IS NOT DISTINCT FROM %(workspace_key)s;

-- name: upsert_source
INSERT INTO sources (
    source_id, collection_id, workspace_key, filename, content_type, file_hash,
    raw_path, status, error_message, chunker_version, origin_key, is_current
)
VALUES (
    %(source_id)s, %(collection_id)s, %(workspace_key)s, %(filename)s,
    %(content_type)s, %(file_hash)s,
    %(raw_path)s, 'uploaded', NULL, NULL, %(origin_key)s,
    CASE WHEN %(origin_key)s::text IS NULL THEN true ELSE false END
)
ON CONFLICT (source_id) DO UPDATE SET
    workspace_key   = EXCLUDED.workspace_key,
    filename        = EXCLUDED.filename,
    content_type    = EXCLUDED.content_type,
    raw_path        = EXCLUDED.raw_path,
    origin_key      = COALESCE(EXCLUDED.origin_key, sources.origin_key),
    is_current      = CASE
        WHEN EXCLUDED.origin_key IS NOT NULL AND sources.origin_key IS NULL THEN false
        ELSE sources.is_current END,
    status          = CASE WHEN sources.status = 'indexed' THEN 'indexed' ELSE 'uploaded' END,
    updated_at      = now();

-- name: set_source_failed
UPDATE sources
SET status = CASE WHEN status = 'indexed' THEN 'indexed' ELSE 'failed' END,
    error_message = %(error_message)s, updated_at = now()
WHERE source_id = %(source_id)s AND index_revision = %(index_revision)s;

-- name: claim_source_index_attempt
UPDATE sources
SET index_revision = index_revision + 1,
    status = CASE WHEN status = 'indexed' THEN 'indexed' ELSE 'uploaded' END,
    error_message = NULL,
    updated_at = now()
WHERE source_id = %(source_id)s
  AND (%(expected_index_revision)s::bigint IS NULL
       OR index_revision = %(expected_index_revision)s)
RETURNING index_revision;

-- name: get_owned_source_for_reindex
SELECT source_id, collection_id, filename, content_type, file_hash, raw_path,
       index_revision
FROM sources
WHERE source_id = %(source_id)s AND workspace_key = %(workspace_key)s;

-- name: lock_source_index_revision
SELECT index_revision
FROM sources
WHERE source_id = %(source_id)s
FOR UPDATE;

-- name: set_source_chunked
UPDATE sources
SET chunker_version = %(chunker_version)s, updated_at = now()
WHERE source_id = %(source_id)s AND index_revision = %(index_revision)s;

-- name: set_source_indexed
UPDATE sources
SET status = 'indexed', error_message = NULL, updated_at = now()
WHERE source_id = %(source_id)s AND index_revision = %(index_revision)s;

-- name: lock_source_origin_publication
SELECT pg_advisory_xact_lock(hashtextextended(
    %(collection_id)s::text || ':' || COALESCE(%(workspace_key)s::text, '') || ':' || %(origin_key)s,
    0));

-- name: supersede_source_origin
UPDATE sources
SET is_current = false, updated_at = now()
WHERE collection_id = %(collection_id)s
  AND workspace_key IS NOT DISTINCT FROM %(workspace_key)s
  AND origin_key = %(origin_key)s AND source_id <> %(source_id)s AND is_current;

-- name: activate_source_origin
UPDATE sources
SET is_current = true, updated_at = now()
WHERE source_id = %(source_id)s AND status = 'indexed'
  AND collection_id = %(collection_id)s
  AND workspace_key IS NOT DISTINCT FROM %(workspace_key)s
  AND origin_key = %(origin_key)s;

-- name: list_origin_reconciliation_sources
SELECT source_id, file_hash, raw_path, content_type, status, workspace_key,
       origin_key, is_current
FROM sources
WHERE collection_id = %(collection_id)s
  AND workspace_key IS NOT DISTINCT FROM %(workspace_key)s
  AND file_hash = ANY(%(file_hashes)s::text[])
ORDER BY file_hash, source_id;

-- name: lock_origin_reconciliation_sources
SELECT source_id, file_hash, raw_path, content_type, status, workspace_key,
       origin_key, is_current
FROM sources
WHERE collection_id = %(collection_id)s
  AND workspace_key IS NOT DISTINCT FROM %(workspace_key)s
  AND source_id = ANY(%(source_ids)s::uuid[])
ORDER BY source_id
FOR UPDATE;

-- name: deactivate_sources_for_origin
UPDATE sources
SET is_current = false, updated_at = now()
WHERE collection_id = %(collection_id)s
  AND workspace_key IS NOT DISTINCT FROM %(workspace_key)s
  AND origin_key = %(origin_key)s AND is_current;

-- name: assign_reconciled_source_origin
UPDATE sources
SET origin_key = %(origin_key)s, is_current = false, updated_at = now()
WHERE source_id = %(source_id)s AND collection_id = %(collection_id)s
  AND workspace_key IS NOT DISTINCT FROM %(workspace_key)s
  AND file_hash = %(file_hash)s AND status = 'indexed';

-- name: delete_source
DELETE FROM sources WHERE source_id = %(source_id)s;

-- name: delete_chunks_for_source
DELETE FROM chunks WHERE source_id = %(source_id)s;

-- name: delete_locators_for_source
DELETE FROM locators WHERE source_id = %(source_id)s;

-- name: insert_locator
INSERT INTO locators (
    locator_id, source_id, ordinal, locator_type, label, start_char, end_char
)
VALUES (
    %(locator_id)s, %(source_id)s, %(ordinal)s, %(locator_type)s, %(label)s,
    %(start_char)s, %(end_char)s
);

-- name: insert_chunk
INSERT INTO chunks (
    chunk_id, source_id, locator_id, chunk_index, start_char, end_char,
    token_count, text
)
VALUES (
    %(chunk_id)s, %(source_id)s, %(locator_id)s, %(chunk_index)s,
    %(start_char)s, %(end_char)s, %(token_count)s, %(text)s
);

-- name: list_locators_for_source
SELECT locator_id, ordinal, locator_type, label, start_char, end_char
FROM locators
WHERE source_id = %(source_id)s
ORDER BY ordinal;

-- name: list_chunks_for_source
SELECT chunk_id, locator_id, chunk_index, start_char, end_char, token_count, text
FROM chunks
WHERE source_id = %(source_id)s
ORDER BY chunk_index;

-- name: cited_chunk_details
SELECT c.chunk_id,
       c.source_id,
       c.chunk_index,
       c.text,
       s.filename AS source_name,
       s.collection_id AS source_collection_id,
       s.status AS source_status,
       l.label AS locator_label,
       s.workspace_key
FROM chunks c
JOIN sources s ON s.source_id = c.source_id
JOIN locators l ON l.locator_id = c.locator_id
WHERE c.chunk_id = %(chunk_id)s
  AND (s.workspace_key IS NULL OR s.workspace_key = %(workspace_key)s)
  AND s.is_current;

-- name: cited_chunk_details_many
SELECT c.chunk_id,
       c.source_id,
       c.chunk_index,
       c.text,
       s.filename AS source_name,
       s.collection_id AS source_collection_id,
       s.status AS source_status,
       l.label AS locator_label,
       co.canonical_code AS canonical_code,
       s.workspace_key
FROM chunks c
JOIN sources s ON s.source_id = c.source_id
JOIN locators l ON l.locator_id = c.locator_id
LEFT JOIN courses co ON co.locator_id = c.locator_id
WHERE c.chunk_id = ANY(%(chunk_ids)s::uuid[])
  AND (s.workspace_key IS NULL OR s.workspace_key = %(workspace_key)s)
  AND s.is_current;

-- name: list_sources
SELECT s.source_id,
       s.filename,
       s.content_type,
       s.status,
       s.index_revision,
       s.error_message,
       s.warnings,
       COALESCE(l.locator_type, 'line_range') AS locator_type,
       COALESCE(c.n, 0) AS chunk_count
FROM sources s
LEFT JOIN LATERAL (
    SELECT locator_type FROM locators l
    WHERE l.source_id = s.source_id
    ORDER BY ordinal LIMIT 1
) l ON true
LEFT JOIN LATERAL (
    SELECT COUNT(*) AS n FROM chunks c WHERE c.source_id = s.source_id
) c ON true
WHERE s.collection_id = %(collection_id)s
  AND s.is_current
ORDER BY s.created_at, s.source_id;

-- name: list_source_fences_for_workspace
SELECT s.source_id, s.collection_id, s.status, s.index_revision
FROM sources s
WHERE s.collection_id = %(collection_id)s
  AND s.is_current
  AND (s.workspace_key IS NULL OR s.workspace_key = %(workspace_key)s)
ORDER BY s.created_at, s.source_id;

-- name: list_sources_for_workspace
SELECT s.source_id,
       s.collection_id,
       s.workspace_key,
       s.filename,
       s.content_type,
       s.status,
       s.index_revision,
       s.error_message,
       s.warnings,
       COALESCE(l.locator_type, 'line_range') AS locator_type,
       COALESCE(c.n, 0) AS chunk_count
FROM sources s
LEFT JOIN LATERAL (
    SELECT locator_type FROM locators l
    WHERE l.source_id = s.source_id
    ORDER BY ordinal LIMIT 1
) l ON true
LEFT JOIN LATERAL (
    SELECT COUNT(*) AS n FROM chunks c WHERE c.source_id = s.source_id
) c ON true
WHERE s.collection_id = %(collection_id)s
  AND (s.workspace_key IS NULL OR s.workspace_key = %(workspace_key)s)
  AND s.is_current
ORDER BY s.created_at, s.source_id;

-- name: lock_source_fences_for_workspace
SELECT source_id, collection_id, status, index_revision
FROM sources
WHERE source_id = ANY(%(source_ids)s::uuid[])
  AND (workspace_key IS NULL OR workspace_key = %(workspace_key)s)
  AND is_current
ORDER BY source_id
FOR SHARE;

-- name: set_source_warnings
UPDATE sources SET warnings = %(warnings)s, updated_at = now()
WHERE source_id = %(source_id)s AND index_revision = %(index_revision)s;
