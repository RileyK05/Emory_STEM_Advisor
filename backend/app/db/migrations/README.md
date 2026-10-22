# Schema changes after checkpoint 032

Fresh databases start from `../baseline/032_schema.sql`. Existing databases keep
their original migration records and use the unchanged archive in
`../legacy_migrations/` to reach that checkpoint. Both paths then apply the
numbered files in this directory, in order.

- Append a change as the next number (`075_name.sql`, ...). The highest number
  here is the head.
- Never edit the baseline or a migration that may have been applied anywhere.
  Fix a mistake with a new migration.
- Start each file with a comment saying what it changes and why. That comment is
  the changelog; this README does not repeat it.
- `docs/architecture.md` is the design contract the schema follows.
- `python -m scripts.schema_baseline` (from `backend/`) checks baseline/legacy
  schema parity on disposable databases, without models or live providers.
