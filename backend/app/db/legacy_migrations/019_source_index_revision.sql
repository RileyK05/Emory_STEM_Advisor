ALTER TABLE sources
    ADD COLUMN index_revision bigint NOT NULL DEFAULT 0
    CHECK (index_revision >= 0);
