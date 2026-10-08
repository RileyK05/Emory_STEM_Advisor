-- name: get_source_by_hash
SELECT source_id, status
FROM sources
WHERE collection_id = %(collection_id)s AND file_hash = %(file_hash)s;

-- name: upsert_source
INSERT INTO sources (
    source_id, collection_id, filename, content_type, file_hash,
    raw_path, status, error_message, chunker_version
)
VALUES (
    %(source_id)s, %(collection_id)s, %(filename)s, %(content_type)s, %(file_hash)s,
    %(raw_path)s, 'uploaded', NULL, NULL
)
ON CONFLICT (source_id) DO UPDATE SET
    filename        = EXCLUDED.filename,
    content_type    = EXCLUDED.content_type,
    raw_path        = EXCLUDED.raw_path,
    status          = 'uploaded',
    error_message   = NULL,
    chunker_version = NULL,
    updated_at      = now();

-- name: set_source_failed
UPDATE sources
SET status = 'failed', error_message = %(error_message)s, updated_at = now()
WHERE source_id = %(source_id)s;

-- name: set_source_chunked
UPDATE sources
SET status = 'chunked', chunker_version = %(chunker_version)s, updated_at = now()
WHERE source_id = %(source_id)s;

-- name: set_source_indexed
UPDATE sources
SET status = 'indexed', updated_at = now()
WHERE source_id = %(source_id)s;

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
       s.status   AS source_status,
       l.label    AS locator_label
FROM chunks c
JOIN sources s  ON s.source_id = c.source_id
JOIN locators l ON l.locator_id = c.locator_id
WHERE c.chunk_id = %(chunk_id)s;

-- name: list_sources
SELECT s.source_id,
       s.filename,
       s.content_type,
       s.status,
       s.error_message,
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
ORDER BY s.created_at, s.source_id;
