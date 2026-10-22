# Frontend audit

## Scope and method

Read-only review of the API type/client and application entry/state paths; source selection/reindex, citation navigation, conversation/streaming, artifact workspace/job/batch, gap review, and connection settings workflows; supporting async/error/preview helpers; relevant `docs/architecture.md` contracts; corresponding FastAPI routes/validation; and existing frontend behavior-test cases. This was not a line-by-line review of every presentation leaf, CSS rule, or mock-client implementation. No code, generated output, database, credentials, or model state was changed. Tests were not executed; coverage notes describe inspected cases.

## Findings

### UI-01 — Confirmed bug · Medium: provider changes retain incompatible profile state

**Trigger and impact:** Edit an existing `cloudflare-clef` connection profile, change Provider to any other provider, and save. The form hides the Account ID control but retains `account_id` in its state; backend validation rejects that update with 422, and the hidden value has no visible clear control after switching. The broader transition issue also affects decision-only roles: changing to `cloudflare-clef` or `openai-decisions` while retaining a standard role set is rejected; changing back while `decision` remains selected is also rejected. Those role mismatches are recoverable by editing the visible roles, while the hidden Cloudflare account ID is not.

**Evidence:** `frontend/src/components/connection/ProfileForm.tsx:34` changes only `provider` and the new-profile `secret`; `frontend/src/components/connection/formState.ts:48` copies `account_id` from the profile; `frontend/src/components/connection/ConnectionSettings.tsx:87` submits the full form state; `backend/app/connections/models.py:164` rejects provider/role mismatches and `:179` rejects `account_id` for non-Cloudflare providers.

**Coverage:** `frontend/tests/browser-workflows.test.mjs:962` covers profile create/edit/credential DTO behavior, but the inspected test does not change an existing profile's provider across the Cloudflare boundary.

**Minimal recommendation:** Normalize provider-dependent state when Provider changes: clear hidden fields that are invalid for the new provider and guide or update the role set when entering/leaving a decision-only provider.

**Closure:** Changing provider clears hidden account state that the new provider cannot store, and the saved profile drops that account id. Browser regression: `provider change drops hidden account state and a credential draft cannot move profiles`.

### UI-02 — Confirmed bug · Medium: an open credential draft can silently change target profile

**Trigger and impact:** Open “Replace credential” for profile A and type a value, then click “Replace credential” on profile B without cancelling A. `secretProfile` changes to B, while the shared `secretDraft` remains unchanged. The same visible password value is now submitted for B by `replaceSecret(profile)`. This can store a credential under the wrong profile/provider and leave the intended profile unchanged.

**Evidence:** `frontend/src/components/connection/ConnectionSettings.tsx:23` stores one shared draft; `frontend/src/components/connection/ConnectionSettings.tsx:67` clears it only when closing; `frontend/src/components/connection/ConnectionSettings.tsx:156` changes the target without clearing the draft; `frontend/src/components/connection/ConnectionSettings.tsx:106` submits that shared draft for the clicked profile; `frontend/src/components/connection/ProfileCard.tsx:40` leaves each profile's replace button enabled while another profile's form is open.

**Coverage:** `frontend/tests/browser-workflows.test.mjs:962` exercises a single profile credential replacement and transient masking; the inspected case does not switch targets with a non-empty draft.

**Minimal recommendation:** Clear `secretDraft` whenever the target profile changes, and disable other profile actions while a replacement form is open (or make the target explicit in the form).

**Closure:** While a credential draft is open, the other profile's Replace credential control stays disabled, so the draft cannot be submitted for a different profile. Browser regression: `provider change drops hidden account state and a credential draft cannot move profiles`.

### UI-03 — Confirmed bug · Low: a citation can scroll to a different same-named source

**Trigger and impact:** With two visible source rows sharing a display name, clicking a citation deterministically finds the first matching name and scrolls/highlights that row, even if the cited chunk belongs to the other row. The passage panel remains exact because it looks up `chunkId` in the response trace, but the source-row highlight can identify the wrong upload. This is a confirmed control-flow defect: `showCitationSource` searches by name only, and the API contract has no source ID in citation/context items. Duplicate names are allowed: uploads retain filenames, different file hashes can coexist, and the visible list combines current shared and workspace-owned rows.

**Evidence:** `frontend/src/api/types.ts:62` defines citation identity without `sourceId`; `frontend/src/state/AppContext.tsx:150` selects the first source whose name matches; `frontend/src/components/source/SourcePanel.tsx:36` scrolls the resolved source row; backend listing includes current shared and workspace-owned rows (`backend/app/db/queries/documents.sql:226`; route at `backend/app/api/routes/corpus.py:80`), the uniqueness index is by workspace/hash rather than filename (`backend/app/db/baseline/032_schema.sql:2202`), and citations serialize the chunk's source name at `backend/app/api/serialize.py:84`.

**Coverage:** `frontend/tests/browser-workflows.test.mjs:354` verifies the displayed cited passage, but does not create same-named source rows or assert which row is highlighted.

**Minimal recommendation:** Carry a stable source identity in citations/context and use it for row navigation. If the contract intentionally omits that identity, avoid implying a unique source row was located and add an explicit duplicate-name behavior test.

**Closure:** A citation opens the cited passage when two sources share a display name. The highlighted passage is the cited upload, not the other same-named row. Browser regression: `a citation opens the cited passage when two sources share a display name`.

## Intentional correct behavior (looked suspicious at first)

- **An explicitly empty source selection stays empty.** One-off chat sends `sourceIds: []` when selection is enabled with no checked sources; the frontend does not omit it or reinterpret it as “all.” This matches `QueryRequest`'s documented null/omitted versus empty distinction (`frontend/src/api/types.ts:109`) and is exercised by `frontend/tests/browser-workflows.test.mjs:354`.
- **Saved conversation history is a persisted, model-free read.** The UI sends `mode: 'history'`; it does not synthesize history from local chat state. The backend route and architecture contract distinguish the history endpoint behavior, and `frontend/tests/browser-workflows.test.mjs:412` asserts that showing history does not increment provider calls.
- **A reopened pending conversation resumes event reads without replaying generation.** The client advances an event cursor and only polls the stored event endpoint; it does not submit another turn. The architecture describes reconnect without generation (`docs/architecture.md:1391`), and `frontend/tests/browser-workflows.test.mjs:467` checks that reopening posts no stream request and makes no provider call. The “Request was not replayed” notice for a terminally unknown pending request is a deliberate safety state, not a missing retry.
- **HTML preview strips active/navigation features and adds a sandboxed CSP-locked iframe.** `frontend/src/components/artifact/previewHtml.ts:14` sanitizes untrusted HTML, and `frontend/src/components/artifact/PreviewPane.tsx:22` uses an empty sandbox. `frontend/tests/live-api-integration.test.mjs:190` covers external navigation blocking. The UI's static-preview message is accurate even though downloaded HTML has a separate backend-generated wrapper.

## Limits

This pass reviewed the API type/client and application entry/state paths; the named product workflows; corresponding backend contracts; and existing frontend test coverage. It did not execute browser tests; the duplicate-name result is established from the allowed source identity constraints and the name-only frontend lookup. It is not a semantic-answer-quality review, backend security audit, visual/accessibility certification, or a review of the pre-existing dirty worktree.

