-- Read-only inspection of exact input identities before origin reconciliation.

-- name: intake_read_only
SET TRANSACTION READ ONLY;

-- name: inspect_intake_source_versions
SELECT s.source_id, s.collection_id, s.filename, s.file_hash, s.content_type,
       s.status, s.is_current, s.origin_key, s.index_revision,
       to_jsonb(p) AS provenance
FROM sources s
LEFT JOIN source_provenance p ON p.source_id = s.source_id
WHERE s.collection_id = %(collection_id)s
  AND s.workspace_key IS NULL
  AND s.file_hash = ANY(%(file_hashes)s::text[])
ORDER BY s.filename, s.source_id;
