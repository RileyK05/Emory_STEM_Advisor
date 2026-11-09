# Cleanup

Started 2026-11-09. Goal: one answering path, CI that is fully required and
green, and no code, flags, scripts or docs left over from experiments.

Survey (2026-11-09, `488b967`):

- Required CI (backend tests, schema baseline, frontend checks) passes. The
  `frontend integration (experimental)` job fails and is `continue-on-error`.
  Cause: its offline fake model only speaks the legacy answer protocol, and
  research is the default since P2.3, so every answer settles as "The advisor
  couldn't answer that." Locally: 1 of 5 pass.
- Dead code is small: vulture finds no unused module-level functions outside
  route handlers and validators; 3 named SQL queries have no caller
  (`admin_list_audit`, `list_storage_objects`, `update_artifact_storage_key`).
- The real weight is the legacy answer path that research replaced (P2.4), and
  everything kept alive only for it: flags, query planning, the prompt
  registry, citation repair, `mode: "history"`, smoke scripts and tests.

| ID | Status | Item |
|---|---|---|
| C1 | done | Delete the legacy answer path (P2.4): the research/legacy splits in `routes/conversations.py` and `query_service.py`, `_effective_scope`, `history` mode (no UI sends it), `answer_tools.py`, `query_plan.py`, the prompt registry and its routes, legacy-only parts of `answer.py`, `controller.py`, `advising_tools.py`. Flags that then have one value (`ADVISING_RESEARCH_ENABLED`, `QUERY_PLANNING_ENABLED`, `ANSWER_CITATION_REPAIR_ENABLED`, the compaction and context-window settings, `ANSWER_TRUNCATION_MAX_OUTPUT_TOKENS`) go too, with query planning, the catalog directory, routing's quality escalation and history compaction. `research_service.py` folded into `query_service.py`. Research now settles token and cost quotas and records unanswered questions as gaps, which only the legacy path did. `TOOLS_ENABLED` stays: it still selects the artifact path. `/api/query` stays: the demo chat uses it. |
| C2 | done | Integration CI: the fixture's fake model speaks the research protocol; the job loses `continue-on-error` once it passes. |
| C3 | done | Scripts: smoke and manual scripts that drive only the legacy path are updated or deleted; `--mode both/legacy` options go. `smoke/adversarial_advising.py` drove only the legacy answer function and is deleted; the clarity probe, the audit's product mode and `smoke/live_advising.py` run research only; the load test's fake speaks research. |
| C4 | done | Docs (P2.5): `architecture.md` describes one answering path (query planning, the one-shot answer section, compaction, history mode, the sync turn route and provisional text are gone or rewritten); the audit README closes "research stays off"; AGENTS updated in C1. The finished plans and round reports stay: they are cross-linked records, and git keeps their history. |
| C5 | done | Plan-ID narration in code comments rewritten (config budgets, debug registry, benchmark, Office QA). `scripts/smoke/sandbox_host.py` keeps its P4.x probe labels: they are the ledger's acceptance names. Applied migration 085 keeps its comment. Unused SQL went in C9; the storage queries stay as hosting groundwork. |
| C6 | todo | Split files that grew by accretion once C1 lands (`research_tools.py` 1,438 lines, `routes/admin.py` 944). |
| C7 | blocked | Debug sessions and experiments (P1.9, U1): the user decides keep or cut. Since C1 a debug session's system-prompt override is stored but not applied. |
| C8 | done | Provider streaming: nothing calls `provider.stream()` since C1. The adapters' `stream` methods, `consume_stream`, `StreamEvent`, the gate's stream wrapper and their tests go. hf-local loses its streaming worker and bounded streamer; the bare-model audit uses `generate`. |
| C9 | done | Conversation summaries (compaction): nothing writes them since C1. The store's summary methods, `queries/conversation_summaries.sql`, the privacy/backup/admin reads, and a migration dropping the table. Migration 088 also drops the prompt registry's tables (`prompt_versions`, `prompt_promotions`, `eval_suite_fixtures`); `queries/prompts.sql`, `queries/planning.sql` and three uncalled queries go. `ConversationStore.context` is the recent window only. |
| C10 | done | The draft preview is gone: no `provisional_*` events relayed or typed, `StreamPreview` carries only the stage, browser tests watch stage labels. `RefusalResponse` and the demo chat's refusal branch go; the mock answers "insufficient evidence" instead. Saved `refusal`/`history` turn statuses stay so old saved chats still display. The `plan` seam name goes. |
| C11 | done | The synchronous `POST /api/conversations/{id}/turns` route: the UI uses only the stream route. Port its tests to the stream route, then delete it. `tests.helpers.send_turn` starts a streamed turn and runs the worker once. |

Found along the way, not yet done:

- `ConversationStore.begin` and `replay` have only test callers since C11; the
  tests seed pending requests with them.
- `conversations.context_boundary_sequence` is read in four queries but nothing
  writes it (the memory-reset feature it served is gone).
- The `streaming` connection capability is still declared and stored, though
  nothing streams.

Every step keeps the gates green: `pytest`, `ruff`, `npm run typecheck`,
`npm test`, `npm run format:check`, `npm run check:callers`, and from C2 on
`npm run test:integration`.
