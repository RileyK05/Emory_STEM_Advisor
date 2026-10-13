-- Extraction quality is separate from indexed/failed status.
ALTER TABLE sources ADD COLUMN warnings jsonb NOT NULL DEFAULT '[]'::jsonb;
