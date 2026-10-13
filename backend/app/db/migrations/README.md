# Schema changes after checkpoint 032

Fresh databases start from `../baseline/032_schema.sql`. Existing databases
retain their original migration records and use the unchanged SQL archive in
`../legacy_migrations/` to reach that checkpoint.

`033_department_topics.sql` replaces course similarity pairs with department/topic
containment. Both fresh and legacy paths apply it after checkpoint 032.
Append future changes here as `034_name.sql`, `035_name.sql`, and so on.
Never edit the baseline or an applied migration. `docs/architecture.md` is the
design contract. Run `python -m scripts.schema_baseline` from `backend/` to
check baseline/legacy schema parity without models or live providers.
