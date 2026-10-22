-- Catalog refresh batches. Publication never deletes a prior edition or topic build.

-- name: get_current_catalog_edition
SELECT edition_id, collection_id, revision, edition_key, source_ids, topic_build_id, is_current
FROM catalog_editions
WHERE collection_id = %(collection_id)s AND is_current;

-- name: lock_current_catalog_edition
SELECT edition_id, collection_id, revision, edition_key, source_ids, topic_build_id, is_current
FROM catalog_editions
WHERE collection_id = %(collection_id)s AND is_current
FOR UPDATE;

-- name: get_catalog_edition
SELECT edition_id, collection_id, revision, edition_key, source_ids, topic_build_id, is_current
FROM catalog_editions
WHERE edition_id = %(edition_id)s;

-- name: insert_catalog_edition
INSERT INTO catalog_editions (
    edition_id, collection_id, revision, edition_key, source_ids, topic_build_id, is_current
) VALUES (
    %(edition_id)s, %(collection_id)s, %(revision)s, %(edition_key)s,
    %(source_ids)s, %(topic_build_id)s, %(is_current)s
);

-- name: insert_catalog_refresh_batch
INSERT INTO catalog_refresh_batches (
    batch_id, collection_id, edition_key, status, base_revision, staged_revision,
    staged_manifest, compatible, staged_edition_id
) VALUES (
    %(batch_id)s, %(collection_id)s, %(edition_key)s, 'staging', %(base_revision)s,
    %(staged_revision)s, %(staged_manifest)s, %(compatible)s, %(staged_edition_id)s
);

-- name: get_catalog_refresh_batch
SELECT batch_id, collection_id, edition_key, status, base_revision, staged_revision,
       staged_manifest, compatible, staged_edition_id, created_at, cancelled_at, published_at
FROM catalog_refresh_batches
WHERE batch_id = %(batch_id)s;

-- name: cancel_catalog_refresh_batch
UPDATE catalog_refresh_batches
SET status = 'cancelled', cancelled_at = now()
WHERE batch_id = %(batch_id)s AND status = 'staging'
RETURNING batch_id;

-- name: mark_catalog_edition_current
UPDATE catalog_editions
SET is_current = (edition_id = %(edition_id)s)
WHERE collection_id = %(collection_id)s AND (is_current OR edition_id = %(edition_id)s);

-- name: publish_catalog_refresh_batch
UPDATE catalog_refresh_batches
SET status = 'published', published_at = now()
WHERE batch_id = %(batch_id)s AND status = 'staging'
RETURNING batch_id;

-- name: list_catalog_edition_source_ids
-- The institutional sources an edition pins: current, indexed, not private uploads.
SELECT source_id
FROM sources
WHERE collection_id = %(collection_id)s
  AND workspace_key IS NULL
  AND is_current
  AND status = 'indexed'
ORDER BY source_id;

-- name: get_staging_catalog_refresh_batch
SELECT batch_id
FROM catalog_refresh_batches
WHERE collection_id = %(collection_id)s AND status = 'staging'
ORDER BY created_at DESC
LIMIT 1;

-- name: next_catalog_edition_revision
SELECT coalesce(max(revision), 0) + 1 AS revision
FROM catalog_editions
WHERE collection_id = %(collection_id)s;
