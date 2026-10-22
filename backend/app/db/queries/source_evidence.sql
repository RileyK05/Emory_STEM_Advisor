-- name: list_chunks_at_indexed_revision
SELECT s.indexed_content_revision, c.chunk_id, c.chunk_index, l.label, c.text
FROM sources s
JOIN chunks c ON c.source_id = s.source_id
JOIN locators l ON l.locator_id = c.locator_id
WHERE s.source_id = %(source_id)s
  AND s.indexed_content_revision IS NOT NULL
ORDER BY c.chunk_index, c.chunk_id;

-- name: insert_source_revision_evidence
INSERT INTO source_revision_evidence (
    source_id, index_revision, chunk_id, chunk_index, locator_label, text, text_digest
) VALUES (
    %(source_id)s, %(index_revision)s, %(chunk_id)s, %(chunk_index)s,
    %(locator_label)s, %(text)s, %(text_digest)s
)
ON CONFLICT (source_id, index_revision, chunk_id) DO NOTHING;

-- name: list_source_revision_evidence
SELECT chunk_id, chunk_index, locator_label, text, text_digest
FROM source_revision_evidence
WHERE source_id = %(source_id)s AND index_revision = %(index_revision)s
ORDER BY chunk_index, chunk_id;

-- name: get_source_indexed_revision
SELECT source_id, index_revision, indexed_content_revision, status
FROM sources
WHERE source_id = %(source_id)s;
