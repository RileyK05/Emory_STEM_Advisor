# Schema changes after checkpoint 032

Fresh databases start from `../baseline/032_schema.sql`. Existing databases
retain their original migration records and use the unchanged SQL archive in
`../legacy_migrations/` to reach that checkpoint.

Both paths then apply these append-only changes in order:

- `033_department_topics.sql` — department/topic containment replaces lateral
  course similarity pairs.
- `034_graph_snapshot_ownership.sql` — a topic build owns an immutable snapshot of
  the course identities and vectors it grouped.
- `035_course_canonical_identity.sql` — `courses.canonical_code` deduplicates
  cross-listed listings.
- `036_model_call_cached_tokens.sql` — provider-reported prompt-cache tokens.
- `037_research_advising.sql` — research-advisor contract scaffolding.
- `038_research_data_layer.sql` — program field evidence and the research scope.
- `039_research_execution.sql` — private research step details.
- `040_source_applicability.sql` — the `institutional_publication` category and
  per-source applicability metadata.

The current head is `040`. Append future changes as `041_name.sql` and so on.
Never edit the baseline or an applied migration. `docs/architecture.md` is the
design contract. Run `python -m scripts.schema_baseline` from `backend/` to
check baseline/legacy schema parity without models or live providers.
