# Immutable legacy migration archive

These are the original 001–032 SQL files, retained byte-for-byte for existing
databases and compatibility checks. New installations use the final schema
in `../baseline/032_schema.sql`; they do not replay this history.

Keep this archive. Old databases and backups may still record these migration
names and digests. Future schema changes belong in `../migrations/` starting
at 033. Do not renumber or edit archived files.
