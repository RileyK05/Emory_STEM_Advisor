# Systematic audit — 2026-10-10

Requested read-only sweep by three smaller Luna agents plus the coordinating agent. Only audit Markdown was added; implementation, migrations, development data, keys, and graph state were not changed. These are review findings, not a new design contract or a claim that the project is finished.

The user's subsequent decisions and the full proposed implementation sequence are in [repair-plan.md](repair-plan.md). That plan adopts top-down department/topic search, explicit cross-list identity, and renewed human review when depended-on evidence changes. The current disposition table is the completion status. The repair plan remains the earlier implementation sequence.

## Start here

| File | Scope |
| --- | --- |
| [Data and advising](data-and-advising.md) | Ingestion, catalog programs, retrieval, answer grounding, deterministic prerequisites, department/topic graph |
| [Frontend](frontend.md) | API/client/state, connection forms, sources/citations, conversation and artifact flows |
| [Runtime](runtime.md) | Connection ownership, model routing, API/auth/scoping, runtime/deployment boundaries |
| [Workflows](workflows.md) | Sampled artifact/job/tool/budget/sandbox/conversation/gap transitions |
| [mimo_scan.md](mimo_scan.md) | 2026-10-20 full code review; Tier 0–3 findings |
| [db-data-review.md](db-data-review.md) | 2026-10-20 read-only review of the live prerequisite graph |

## 2026-10-20 code and data scans

The code-level Tier 0 and Tier 1 defects, plus Tier 2 items 2.1–2.5,
2.7–2.18, 2.20, 2.21, and 2.23, are fixed in the current working tree with
regression coverage. Item 2.20 was a TypeScript shape mismatch. Item 2.6
(no-LLM guard coverage), 2.19 (contract-drift coverage), 2.22 (`sleep` in
tests), and the remaining Tier 3 notes are still open code-quality work.

The parser fixes change future imports only. **No atlas re-import or database
backfill was performed**, so the counts and anomalies in
[db-data-review.md](db-data-review.md) remain until the manual R1–R11 pass.
Do not hand-edit `prereq_nodes` or `prereq_mentions`; fix the parser, re-import,
and re-run that document's acceptance sweep.

**12 findings are marked confirmed:** seven data/advising issues, three frontend issues, and two runtime issues. Some are previously observed answer-quality failures; others were reproduced here or established by control-flow inspection. The defensive evidence-stitching case is not known to occur in normal retrieval. Two additional items are design questions, not established bugs. Each report also identifies intentional behavior to preserve.

## Current disposition (2026-10-20)

The 2026-10-10 audit recorded the findings below as open and did not implement fixes. The completion pass closed each confirmed defect on the regression named here. None of them is the next open finding. DA-03 is closed in the keyword ranking path. A varied live-corpus comparison was not re-run.

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

DA-01, DA-02, and DA-03 were overlapping relevance failures. Each is closed on its own regression. DA-04's free-text entailment limit stays outside model judgment. RT-01 and RT-02 were closed by releasing or separating the borrowed connection, not by raising the pool size.

## Questions that remain open

- **DA-08:** Should a strong small topic admit a department that its aggregate centroid excluded? Current department centroid gating is configured search policy, not a defect. No behavior change.

## Separate static scan

[mimo_scan.md](mimo_scan.md) is a 2026-10-10 static scan, not part of the confirmed-finding queue above. Its six fix-first notes match current code as follows. They are not open findings in this index.

| Note | Current code | Regression |
| --- | --- | --- |
| Pool slot leak on vector registration | `types.get("vector")` and `putconn` on the failure path | The RT-01 and RT-02 one-slot pool tests |
| Dependent artifact unit stuck in `calling` | A resumed `calling` unit is closed with `mark_unknown` and the output is withheld | `test_plan_recovery_never_requeues_unknown_provider_intent` closes an expired calling unit as `unknown_outcome` and does not requeue it. `test_unknown_call_is_terminal_and_cannot_be_retried` shows that unknown call cannot be retried |
| Program parser carries a subject onto "Classics 350" or reads a credit number as `choose` | A non-subject word does not inherit the previous subject, and a credit number is not the choose count | `test_course_mentions` case "Math 211, 250, and Classics 350"; `test_choose_count_only_when_explicit` |
| XLSX import materializes an empty used rectangle | Import walks declared cells, not `iter_rows()` over the rectangle | `test_sparse_sheet_imports_only_declared_cells` imports one declared cell at row 200000 |
| Standalone worker stops embedded Postgres | `open_connection` stays open for the worker lifetime, including `--once` | Inspected in `scripts/worker.py`. A process regression would start a second embedded database or load the model handler, so none was added |
| New conversation during the first list load sticks the panel | A new chat sets `skipInitialOpen` and does not leave the list load stuck | Browser: `new chat during the first list load does not reopen the previous chat` |

Two medium notes from the same scan are closed in the current code. When the cited-set budget already covers every source, a stronger sibling does not displace a covered source (`test_full_source_budget_keeps_every_covered_source`). A tight budget still admits a strictly stronger sibling (`test_soft_source_cap_admits_a_stronger_sibling`) and keeps the weaker source when the sibling does not outrank it (`test_soft_source_cap_keeps_diversity_when_sibling_is_weaker`). Import and export refuse a legacy DDE formula (`test_xlsx_import_and_export_refuse_dde_formula`). The historical scan text is unchanged.

[REPORT-round1.md](REPORT-round1.md) is a live-model sweep. Those rows were not re-run. [repair-plan.md](repair-plan.md) remains the earlier implementation sequence.

## Preserve while refining

- Fixed departments → learned within-department topics → courses; containment links only. Embeddings serve semantic lookup. No lateral related-course graph.
- Deterministic prerequisite parsing/planning; no model-created graph facts. Ambiguity, permission, grades, and missing evidence remain blockers.
- Current-source/workspace scoping, stable content identity, historical evidence, atomic source replacement, expected revisions, lease/cancellation fences, and conservative budget reservations.
- Unknown provider outcomes are not blindly replayed. Artifact revisions remain immutable. HTML stays inert; Office calculation/rendering requires the existing isolation gates.

These safeguards are explained in the reports so another model does not “fix” them away.

## Validation and limits

The coordinator checked the agents' findings against relevant code, corrected status/scope overclaims, and verified the new pure/disposable-DB reproductions. The runtime agent's focused offline selection finished with 70 passes and 19 DB tests intentionally deselected; it does not reproduce the two pool bugs. The frontend agent ran a synthetic name-lookup example, not browser tests. The workflow agent performed static path review only.

No new paid/live-model sweep, full backend/frontend suite, deployed sandbox/Office check, or production-provider batch check was performed during the 2026-10-10 audit. The earlier 12 live questions are exploratory evidence, not a statistical accuracy score. Areas were assigned systematically, but the code reviews are focused samples, not exhaustive line-by-line certification. No proposed fixes were implemented as part of that audit. The current disposition table records the later closures.
