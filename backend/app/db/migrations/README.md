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

The current head is `072_course_lifecycle.sql`. This directory contains
migrations `041` through `072`. `058` adds support-grant requests,
`059` leases the source-invalidation consumer, `060` preserves indexed chunk
text by revision, `061` stores immutable academic-record snapshots, `062`
stores program-comparison scenarios without turning overlays into attempts,
`063` stages catalog refresh batches without retiring the published edition,
`064` stores one current feedback vote per owner plus redacted eval
candidates, `065` records feedback triage and source-pinned candidate
promotion, `066` stores debug experiments separately from conversation replay,
`067` stores immutable prompt versions and human promotion, and `068` stores
one generation quota settlement per request, `069` attributes retrieval
traces to the requesting workspace, `070` stores immutable term-scoped
Atlas offering snapshots and durable raw-capture file references apart from
master course and prerequisite facts, and
`071` records curator-reviewed requirement conflict holds by exact master
source and course without rewriting graph facts, `072` tags each course
identity as `current` or `retired` so catalog removals retain historical
sources and parsed evidence outside current reads, and `073` adds reviewed
course authority, an admin revision, and the versioned `course_changes`
record (draft, publication and baseline-conflict) behind admin course
maintenance. Append future changes as `074_name.sql` and so on.
Never edit the baseline or an applied migration. `docs/architecture.md` is the
design contract. Run `python -m scripts.schema_baseline` from `backend/` to
check baseline/legacy schema parity without models or live providers.
