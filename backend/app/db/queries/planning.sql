-- Scoped catalog directory for query planning. Every read is limited to the
-- collection's current, indexed sources visible to the workspace and, when a
-- selection is given, to the selected sources. An empty selection is empty.

-- name: list_directory_source_revisions
SELECT s.source_id, s.index_revision, COALESCE(p.metadata_revision, 0) AS metadata_revision
FROM sources s
LEFT JOIN source_provenance p ON p.source_id = s.source_id
WHERE s.collection_id = %(collection_id)s
  AND s.status = 'indexed'
  AND s.is_current
  AND (s.workspace_key IS NULL OR s.workspace_key = %(workspace_key)s)
  AND (%(source_ids)s::uuid[] IS NULL OR s.source_id = ANY(%(source_ids)s::uuid[]))
ORDER BY s.source_id;

-- name: list_directory_courses
SELECT co.code, co.subject, co.title
FROM courses co
JOIN sources s ON s.source_id = co.source_id
WHERE co.collection_id = %(collection_id)s
  AND s.collection_id = %(collection_id)s
  AND s.status = 'indexed'
  AND s.is_current
  AND (s.workspace_key IS NULL OR s.workspace_key = %(workspace_key)s)
  AND (%(source_ids)s::uuid[] IS NULL OR s.source_id = ANY(%(source_ids)s::uuid[]))
ORDER BY co.subject, co.code;

-- name: list_directory_documents
SELECT s.source_id, s.filename, s.workspace_key, p.title, p.source_category, p.publisher,
       p.institution, p.issuing_unit, p.edition, p.document_roles, p.applicability,
       p.applicability_status, p.source_url, p.published_at, p.acquired_at
FROM sources s
LEFT JOIN source_provenance p ON p.source_id = s.source_id
WHERE s.collection_id = %(collection_id)s
  AND s.status = 'indexed'
  AND s.is_current
  AND (s.workspace_key IS NULL OR s.workspace_key = %(workspace_key)s)
  AND (%(source_ids)s::uuid[] IS NULL OR s.source_id = ANY(%(source_ids)s::uuid[]))
  AND NOT EXISTS (SELECT 1 FROM courses co WHERE co.source_id = s.source_id)
ORDER BY lower(s.filename), s.source_id;
