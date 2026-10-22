-- Storage objects catalog and opaque storage keys (Item 26).

ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS original_storage_key text;

CREATE TABLE IF NOT EXISTS storage_objects (
    storage_key text PRIMARY KEY,
    size_bytes bigint NOT NULL,
    sha256 text NOT NULL,
    content_type text,
    backend text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_storage_objects_sha256
    ON storage_objects(sha256);

