# Audit status

Closed findings from the 2026-10-10 systematic audit and the 2026-10-20 code and
data scans, the items still open, and behavior a refactor must not "fix" away.
The original reports were removed once every confirmed finding closed; they are in
git history. [db-data-review.md](db-data-review.md) is the live data review and
stays until its R1–R11 acceptance pass is re-run.

## Closed findings

| ID | Disposition | Regression |
| --- | --- | --- |
| DA-01 | Closed. The catalog splitter stops the last program at a following department section and keeps that appendix as department text. The cached catalog and the user dev corpus were not regenerated. | `test_catalog_pdf_stops_the_last_program_at_a_department_section` |
| DA-02 | Closed. Official cross-listed aliases collapse by canonical key before the embedding quota, and the strongest chunk identity is kept. The earlier live REL 340 sample was not re-run. | `test_cross_listed_aliases_do_not_consume_context_slots` |
| DA-03 | Closed. A longer topical token ranks in [1, 2). A keyword set whose best raw score is below 0.05 stays absolute, so the fusion floor can drop it. One exact term stays citable. "work", "first", and "fit" stay searchable. | `test_weak_lexical_matches_are_not_promoted_to_the_top`; `test_repeated_generic_words_do_not_outrank_a_longer_topic`; `test_soft_source_selection_never_expands_exact_source_scope` |
| DA-04 | Closed for an absent or extended course code in a cited passage. A model does not judge free-text entailment. A claim that names a code present in the passage, such as a career guarantee for CS 100, is still accepted. | `test_course_code_must_appear_in_the_cited_passage` |
| DA-05 | Closed. A SQL error in one retrieval seam is isolated, and later retrieval and trace statements still run. | `test_sql_statement_error_in_one_seam_is_isolated` |
| DA-06 | Closed. Catalog replacement retires the previous topic build and keeps its history. | `test_changed_catalog_retires_the_topic_build_and_keeps_history` |
| DA-07 | Closed. A completed requisite evidence run is recognized before a later ambiguous index is considered. | `test_completed_requisite_run_survives_a_later_ambiguous_index` |
| UI-01 | Closed. Changing provider clears hidden account state that the new provider cannot store. | Browser: `provider change drops hidden account state and a credential draft cannot move profiles` |
| UI-02 | Closed. The same browser test keeps the other profile's Replace credential control disabled while a draft is open. | Same browser test as UI-01 |
| UI-03 | Closed. A citation opens the cited passage when two sources share a display name. | Browser: `a citation opens the cited passage when two sources share a display name` |
| RT-01 | Closed. Abandoning a conversation releases the request borrow before job cancel on a one-slot pool. | `test_conversation_abandon_releases_the_request_borrow_before_job_cancel` |
| RT-02 | Closed. A tool-using query does not take a second borrow from the one-slot request pool. | `test_tool_query_does_not_self_starve_a_one_slot_pool` |
| WF-01 | Closed as a point-in-time human attestation. A later source change is handled by the invalidation outbox. Source rows are not locked through commit. | `test_invalidation_consumption_is_idempotent_and_keeps_history` |

## Still open

- **DA-08 (design question):** should a strong small topic admit a department
  that its aggregate centroid excluded? Centroid gating is configured search
  policy, not a defect.
- **Code quality:** no-LLM guard coverage beyond `app/atlas/` and the prerequisite
  seam; contract-drift coverage for types present on only one side; real `sleep`
  calls in tests that should use `FakeClock`.
- **Data:** parser fixes change future imports only. Until the atlas is re-imported
  and the R1–R11 pass in [db-data-review.md](db-data-review.md) is re-run, its
  counts and anomalies stand. Never hand-edit `prereq_nodes` or `prereq_mentions`:
  fix the parser, re-import, re-run the sweep.

## Preserve while refining

- Fixed departments → learned within-department topics → courses; containment links only. Embeddings serve semantic lookup. No lateral related-course graph.
- Deterministic prerequisite parsing/planning; no model-created graph facts. Ambiguity, permission, grades, and missing evidence remain blockers.
- Current-source/workspace scoping, stable content identity, historical evidence, atomic source replacement, expected revisions, lease/cancellation fences, and conservative budget reservations.
- Unknown provider outcomes are not blindly replayed. Artifact revisions remain immutable. HTML stays inert; Office calculation/rendering requires the existing isolation gates.
