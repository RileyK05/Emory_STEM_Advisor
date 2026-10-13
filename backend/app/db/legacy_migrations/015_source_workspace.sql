-- Private uploads are visible only to their owner; NULL remains shared official data.
ALTER TABLE sources ADD COLUMN workspace_key text
    CHECK (workspace_key IS NULL OR length(workspace_key) BETWEEN 1 AND 200);

ALTER TABLE sources DROP CONSTRAINT sources_collection_id_file_hash_key;
CREATE UNIQUE INDEX sources_workspace_hash_uq
    ON sources (collection_id, (COALESCE(workspace_key, '')), file_hash);
CREATE INDEX sources_workspace_idx ON sources (workspace_key, collection_id, status);
