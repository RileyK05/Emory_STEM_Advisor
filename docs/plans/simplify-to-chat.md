# Plan: one chat, artifacts beside it

Status: proposed, 2026-10-30. Nothing here is implemented yet.

## Goal

The product is one thing: a chat with an advisor for Emory STEM students. When
an answer is worth keeping (a four-semester plan, a requirements checklist, a
comparison table), the advisor makes it as an artifact. The artifact opens in a
panel beside the chat, and the student changes it by asking in the chat.

The test for every surface is whether a student would use it in a normal
advising conversation. If they would not, it is cut from the student app.
Operator features move to a smaller admin page. Features with real advising
value but a bad surface (program audit, comparisons, course exploration, summary
export) are wired into the chat, not deleted.

## Target layout

```
+-------------+------------------------------------+--------------------+
| + New chat  |  Chat title                        |  Artifact          |
|             |                                    |  (opens when one   |
| Chats       |  messages, citations, sources      |   is made or       |
|  ...        |                                    |   picked; closed   |
|  ...        |                                    |   otherwise)       |
|             |                                    |                    |
|-------------|                                    |  preview           |
| Artifacts   |                                    |  versions ▾        |
|  ...        |  [ Ask about courses...      ] ->  |  download  close   |
| Emory       |                                    |                    |
| advising ↗  |                                    |                    |
+-------------+------------------------------------+--------------------+
  top bar: brand, account menu (theme, my data, sign out), Admin link for admins
```

- **Left: chats.** New chat, the chat list, archive and restore.
- **Left, bottom: artifacts.** Every artifact the student has made, newest
  first. This replaces the "All artifacts" dialog. Picking one opens it in the
  panel.
- **Left, bottom: a link** to Emory's advising site.
- **Middle: chat.** Messages with citations and the source list, the
  applicability banner, the open-questions callout, a live draft with Stop, and
  one composer.
- **Right: the artifact panel.** It shows progress while an artifact is being
  made, then a preview with a version picker, Download and Close. It opens on its
  own when the chat makes an artifact. On narrow screens it is a full-screen
  sheet.

## What changes for the student

| Today | After | Why |
| --- | --- | --- |
| "Make a document / spreadsheet / web page" buttons under every answer | Removed. Ask: "make this a plan I can print" | The chat decides; no button row |
| Composer "+" menu (new document, workbook, page; import a file) | Removed | Same |
| New artifact dialog (draft / blank / import tabs, section and sheet plans, call and token limits) | Removed. The server plans the artifact from the request | Students should never see token limits |
| Document and workbook editors in the panel | Read-only preview. Changes go through chat ("make spring lighter") | One way to change things (decision D1) |
| Artifact library dialog | The artifacts list in the sidebar | Always visible, no dialog |
| "Search in" scope picker | Removed. The advisor always uses the current Emory corpus | Students don't choose corpora |
| Sources dialog (upload, reindex) | Moves to admin | Uploading into the shared corpus is an operator job |
| Explore courses dialog | Removed. Ask: "what fits an interest in neuroscience?" | Becomes a chat tool (phase 3) |
| Tools dialog: Academic record | Removed from the UI (decision D2) | The chat already asks which courses you have done |
| Tools: Program audit | Ask: "how far am I in the Biology BS?" | Becomes a chat tool (phase 3) |
| Tools: Program comparison | Ask: "compare CS BA and BS" | Research already compares programs |
| Tools: Response feedback | Thumbs up/down on each answer | One click, in place |
| Tools: Privacy | Account menu: "Download my data", "Delete my data" | Rarely used, but must exist |
| Tools: Unanswered questions | Moves to admin | It is operator triage |
| Tools: Bulk drafts | Removed everywhere | No student use |
| Tools: Model connections | Moves to admin | Students don't pick models |
| Tools: Developer (retrieval details) | Moves to the debug page | Developer only |
| Export Summary button | Ask: "summarize this chat" | Becomes an artifact the chat makes |
| Memory button and dialog | Removed from the student app | Developer only |
| Top bar links to Admin and Debug | One Admin link, for admins only | |

These stay as they are: the chat list with archive, streaming with Stop,
citations and the source list, the applicability and open-questions callouts,
the Swoop/Dooley theme switch, sign-in, and demo mode (it backs the frontend
tests).

## Admin: 16 tabs down to 6

| Keep | Contents |
| --- | --- |
| Overview | Health, readiness, alerts, failed jobs (Jobs merges in here) |
| Users | Roles, revoke sessions, plus the audit log for the selected user |
| Sources | List, review, lifecycle, and the upload and reindex moved from the student app |
| Courses | Course maintenance and course-change publishing (where reviewed requisite corrections land) |
| Student questions | Feedback triage plus unanswered questions (gaps), in one queue |
| Usage & cost | Telemetry and price schedules |

Developer settings (model connections, the retrieval-details toggle and debug
overrides) go on one more admin tab, so the separate `debug.html` bundle goes
away (decision D3).

| Cut from admin | Why |
| --- | --- |
| Chats (read student transcripts) and Support grants | Privacy surface with no need: triage gets the answer through feedback |
| Artifacts (browse students' artifacts) | Same |
| Collections | There is one collection; scripts manage it |
| Feature rollouts (flags, cohorts, overrides, kill switches) | The env settings that already exist are enough |
| Catalog refresh | Course data is refreshed from the Atlas by `scripts.reconcile_atlas`, not a catalog batch |
| Experiments, Prompt promotion | Eval tooling; answer generation still reads the active prompt version |

## The core change: the chat makes artifacts

Today, the chat answers through the research loop, and artifacts are separate
runs that the browser starts from a form, with an output plan built in
`outputPlan.ts`. The new flow:

1. **The model asks for an artifact.** The research loop's `finish` decision
   gets an optional `artifact` field: `{kind: document|workbook|html, title,
   brief, revises?: {artifactId, revisionId}}`. The model fills it only when the
   student asks for something to keep, or clearly needs one.
2. **The worker starts the run.** When it publishes the turn, the conversation
   worker starts a dependent run from the turn's active evidence and the brief,
   using the existing runs and `planned_artifacts` machinery. The server builds
   the output plan, so `outputPlan.ts` and `PlanEditor` move server-side or are
   deleted. The turn response carries `artifactRun: {runId}`.
3. **The panel opens.** The browser sees `artifactRun` on the turn and opens the
   panel to the run's progress card. When the run publishes, it shows the
   revision. This reuses `useArtifactWorkspace`'s run polling.
4. **Revisions go through the chat.** While an artifact is open, the composer
   attaches it automatically, using the existing `artifactReferences`. The model
   can then set `revises`, and the run checks the expected revision as revise
   runs do today.
5. **Summary export becomes the same path.** "Summarize this chat" produces an
   `artifact` request, and the run uses `artifacts/advising_summary.py` as the
   generator for that brief instead of a separate button and endpoint.

The prerequisite rule is unchanged: artifacts read the deterministic graph and
cited evidence; nothing writes graph nodes or edges.

## Phases

Each phase leaves `main` green: `pytest`, `ruff`, `npm run typecheck`, `npm
test`, and `test_contract_drift.py` (types.ts and schemas.py change together).

**Phase 1: student frontend cut (frontend only, no API change)**

- Delete:
  - `ToolsDialog`;
  - `batch/`, `records/`, `audits/`, `feedback/FeedbackPanel`;
  - `MemoryDialog`, `ScopePicker`, `ExploreDialog`;
  - `NewArtifactDialog`, `PlanEditor`, `outputPlan.ts`, `ArtifactLibraryDialog`;
  - the make-buttons, the composer "+" menu and the Export Summary button.
- Move `connection/`, `gap/`, `DebugPanel` and `SourcesDialog` upload into admin.
- Sidebar: chats on top; artifacts list and the advising link at the bottom.
- Account menu: theme, my data (privacy export and delete), sign out.
- Add thumbs up/down on answers, which posts to the existing `/api/feedback`.
- The panel becomes read-only preview, versions, download and close.
- Prune `styles.css` and the browser tests to match.
- Expected removal is roughly 3,500 lines of student UI.

This phase temporarily loses making artifacts from the UI. To avoid shipping
that gap, land phases 1 and 2 together, or keep "Make a document" as one button
until phase 2 lands.

**Phase 2: chat-made artifacts (backend plus frontend)**

- Add the `finish.artifact` field and its validation. Add a prompt rule for when
  to make an artifact.
- The worker starts the dependent run. The turn response and `types.ts` gain
  `artifactRun`.
- Server-side output planning goes in `planned_artifacts`.
- The open artifact auto-attaches in the composer.
- Tests:
  - one research scenario row: finish with artifact → run queued;
  - one revise row: stale revision → conflict;
  - one browser test: the panel opens on a turn with `artifactRun`.

**Phase 3: fold the useful tools into research**

- `advisor_audit_program`: the program audit engine, run against the
  `completed_codes` the student reported (like `check_preparation`). It needs no
  saved record.
- `advisor_suggest_courses`: the similarity-graph recommendations for an
  interest, labeled as suggestions and not requirements.
- `compare_mentions` already covers comparisons. `ProgramComparisonPanel`'s
  backend route can go once nothing calls it.

**Phase 4: admin cut and backend removal**

- Admin goes from 16 tabs to 6 plus Developer; drop `debug.html`.
- Delete backend features with no remaining caller. Each one needs a new
  numbered migration that drops its tables; never edit the baseline. Candidates:
  - batches (`routes/batches.py`, `jobs/native_artifact_batches.py`, and
    `provider_batches` if only batches use it);
  - catalog refresh (`admin/catalog_refresh.py`);
  - support grants (`auth/grants.py` and the grant routes);
  - feature flags (`flags/`, replaced by the settings in `EXECUTION_FLAGS`);
  - experiments;
  - prompt promotion routes (keep the active-prompt lookup that `answer.py`
    reads);
  - `/api/decisions` and `/api/claim-reviews` (no frontend caller today;
    confirm nothing else uses them);
  - conversation memory routes;
  - admin chat, artifact and collection routes.
- Update `docs/architecture.md` (HTTP API, Governance) and AGENTS.md's layout
  section in the same change.

`app/storage/` stays: it is hosting groundwork.

## Decisions for you

- **D1. Editing artifacts.** Recommended: preview-only, with changes through the
  chat. The alternative keeps the document and workbook editors for small hand
  fixes.
- **D2. Academic record.** Recommended: no saved record; the chat asks which
  courses you have done. The alternative keeps one "My courses" list in the
  account menu, which the audit tool would read.
- **D3. Debug page.** Recommended: fold it into admin as a Developer tab. The
  alternative keeps `debug.html` as a separate developer bundle.
- **D4. Advising link.** Which URL? It becomes a frontend config value.
