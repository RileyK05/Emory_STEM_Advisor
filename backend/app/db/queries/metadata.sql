-- name: ensure_schema_migrations
CREATE TABLE IF NOT EXISTS schema_migrations (
    version int PRIMARY KEY,
    name text NOT NULL,
    sha256 text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);

-- name: list_applied_migrations
SELECT version, name, sha256 FROM schema_migrations ORDER BY version;

-- name: record_applied_migration
INSERT INTO schema_migrations (version, name, sha256)
VALUES (%(version)s, %(name)s, %(sha)s);

-- name: lock_schema_migrations
SELECT pg_advisory_xact_lock(1938306);

-- name: normalize_migration_checksum
UPDATE schema_migrations SET sha256 = %(sha)s
WHERE version = %(version)s AND sha256 = %(old_sha)s;
