ALTER TABLE sources
    ADD COLUMN is_current boolean NOT NULL DEFAULT true,
    ADD COLUMN origin_key text;

WITH ranked_atlas AS (
    SELECT s.source_id, s.status,
           EXISTS (SELECT 1 FROM courses c WHERE c.source_id = s.source_id) AS has_graph,
           row_number() OVER (
               PARTITION BY s.collection_id, COALESCE(s.workspace_key, '')
               ORDER BY CASE WHEN s.status = 'indexed' AND EXISTS (
                   SELECT 1 FROM courses c WHERE c.source_id = s.source_id
               ) THEN 0 ELSE 1 END, s.created_at DESC, s.source_id DESC
           ) AS version_rank
    FROM sources AS s
    WHERE s.content_type = 'application/x-course-atlas+jsonl'
)
UPDATE sources AS s
SET origin_key = 'emory:course-atlas',
    is_current = ranked_atlas.version_rank = 1
                 AND ranked_atlas.status = 'indexed'
                 AND ranked_atlas.has_graph
FROM ranked_atlas
WHERE ranked_atlas.source_id = s.source_id;

CREATE UNIQUE INDEX sources_current_origin_uq
    ON sources (collection_id, (COALESCE(workspace_key, '')), origin_key)
    WHERE is_current AND origin_key IS NOT NULL;
