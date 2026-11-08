-- Storage objects and opaque key queries (Item 26).

-- name: get_storage_object
SELECT storage_key, size_bytes, sha256, content_type, backend, created_at, updated_at
FROM storage_objects
WHERE storage_key = %(storage_key)s;

-- name: upsert_storage_object
INSERT INTO storage_objects (
    storage_key, size_bytes, sha256, content_type, backend, updated_at
) VALUES (
    %(storage_key)s, %(size_bytes)s, %(sha256)s, %(content_type)s, %(backend)s, CURRENT_TIMESTAMP
)
ON CONFLICT (storage_key)
DO UPDATE SET
    size_bytes = EXCLUDED.size_bytes,
    sha256 = EXCLUDED.sha256,
    content_type = EXCLUDED.content_type,
    backend = EXCLUDED.backend,
    updated_at = CURRENT_TIMESTAMP
RETURNING storage_key, size_bytes, sha256, content_type, backend, created_at, updated_at;

-- name: delete_storage_object
DELETE FROM storage_objects
WHERE storage_key = %(storage_key)s;

-- name: list_storage_objects
SELECT storage_key, size_bytes, sha256, content_type, backend, created_at, updated_at
FROM storage_objects
WHERE storage_key LIKE %(prefix)s
ORDER BY storage_key
LIMIT %(limit)s;

-- name: update_artifact_storage_key
UPDATE artifacts
SET original_storage_key = %(storage_key)s
WHERE artifact_id = %(artifact_id)s;

-- name: list_sources_for_storage_backfill
SELECT source_id, collection_id, workspace_key, file_hash, content_type, raw_path
FROM sources
WHERE raw_path NOT LIKE 'raw/%'
ORDER BY source_id
LIMIT %(limit)s;

-- name: list_artifacts_for_storage_backfill
SELECT artifact_id, workspace_key, original_sha256, original_path
FROM artifacts
WHERE original_path IS NOT NULL
  AND original_path NOT LIKE 'artifacts/originals/%'
ORDER BY artifact_id
LIMIT %(limit)s;

-- name: update_source_storage_path
UPDATE sources
SET raw_path = %(raw_path)s
WHERE source_id = %(source_id)s::uuid;

-- name: update_artifact_storage_reference
UPDATE artifacts
SET original_path = %(storage_key)s, original_storage_key = %(storage_key)s
WHERE artifact_id = %(artifact_id)s::uuid;

