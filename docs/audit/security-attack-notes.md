# Adversarial input and boundary notes (2026-10-23 ET)

Scope: authenticated student surfaces, unauthenticated API boundary, LLM
context, upload/import paths, and export formats. This is a focused attack pass,
not a formal penetration test or a claim that the system is unhackable.

## Fixed in this pass

1. **Unbounded request strings.** `QueryRequest.studentCohort` and
   `collectionId` accepted a 1 MB value. `studentCohort` is echoed into
   applicability output; `collectionId` reaches collection lookup. Both are now
   capped at 200 bytes. The same pass bounded `ExportSummaryRequest.title`
   (500), `throughSequence` (nonnegative), `artifactReferences` (100),
   `PrivacyDeletionRequest.confirmation` (200), academic-record `workspaceKey`
   values (200), and `AcademicRecordImportPreviewRequest.rawContent` (2 MB).
   Regression: `test_request_values_are_bounded`.

   Related integer overflow path: `expectedSequence`, `throughSequence`,
   `expectedIndexRevision`, and academic-record revisions accepted arbitrary
   Python integers, which can exceed PostgreSQL's integer range and surface as a
   server error. They are now capped at PostgreSQL's signed 32-bit range.
   Regression: `test_request_values_are_bounded`.

2. **PDF page bomb.** A small compressed PDF can carry a very large page tree;
   extraction walked every page before the extracted-text limit applied. A
   2,000-page ceiling now rejects it before extraction. Regression:
   `test_pdf_page_count_is_bounded_before_extraction`.

3. **Windows-reserved upload names.** Source uploads rejected `..` and path
   separators but accepted `CON.txt`, trailing dots/spaces, and similar names
   that can fail or resolve to devices on Windows. The route now rejects those
   before creating a temp file. Regression:
   `test_upload_rejects_unsafe_filenames`.

4. **Reflected collection name.** An unknown collection returned
   `unknown collection '<raw input>'`. The name is user-controlled and could be
   reflected into a client or log. The public 404 is now generic. The same test
   sends `course-docs' OR 1=1 --` and confirms the lookup is parameterized and
   the raw string is not reflected. Regression:
   `test_collection_lookup_treats_sql_metacharacters_as_data`.

5. **Unbounded per-workspace growth.** An authenticated user could create
   artifacts, revisions, and conversations without a count ceiling. The store
   layer now caps each workspace at 500 artifacts, 500 revisions per artifact,
   and 500 conversations. Because the caps are in `ArtifactStore` and
   `ConversationStore`, they apply to API, worker, and batch paths, not just one
   route. Regressions:
   `test_artifact_count_and_revision_caps_are_enforced` and
   `test_conversation_creation_limit_is_enforced`.

6. **Oversized query text.** `QueryRequest.query` allowed 20,000 characters.
   It is now capped at 8,000; this remains far above a normal advising question
   and keeps a single request from crowding out retrieval context. Regression:
   `test_request_values_are_bounded`.

## Checked; no bypass found in this pass

- **SQL/CRUD injection.** App SQL is named in `backend/app/db/queries/*.sql`
  and parameterized. No f-string or `format()` SQL execution was found in
  `backend/app`.
- **Stored/reflected XSS.** Answers render through `react-markdown` without raw
  HTML. Document previews escape text. HTML artifacts run through the inert
  allowlist and a `sandbox=""` iframe with a restrictive CSP. Raw
  `on*`, `javascript:`, forms, frames, and active SVG/MathML are dropped.
- **Spreadsheet formula injection.** Imported and generated cells that begin
  with `=` remain inert strings. `WEBSERVICE`, `FILTERXML`, `HYPERLINK`, DDE
  (`|`), `RTD`, `CALL`, `REGISTER`, `EXEC`, external references, macros, and
  external links are rejected. Audit CSV cells are prefixed when they begin
  with `= + - @`, tab, or carriage return.
- **Upload/archive exhaustion.** Request bodies are bounded before multipart
  parsing; uploads are bounded; Office archives enforce entry count, total and
  per-entry uncompressed size, compression ratio, duplicate/unsafe paths,
  defused XML, and reject macros/external relationships/embedded objects.
- **Path traversal.** Artifact originals are content-addressed and validated by
  digest and parent directory. Source reindex validates the raw path against
  the workspace hash directory. Sandbox input paths are checked.
- **Auth/session boundary.** Cookies are `__Host-`, HttpOnly, Secure,
  SameSite=Lax. Non-GET browser requests require the configured Origin.
  Cross-site fetch metadata is rejected. Workspace-scoped stores filter by
  workspace key. Local admin and metrics are loopback-only, reject forwarding
  headers, and require a loopback Host. Debug and admin panels require an
  active admin role.
- **LLM boundary.** Source/context sentinels are defanged; tool output and
  history are labeled untrusted; facts require supplied exact evidence IDs;
  system-prompt echo is rejected; tool calls are allowlisted and scoped; the
  prerequisite graph and sequence planner are deterministic.
- **Live adversarial probes (2026-10-23).** Nine context attacks against the
  configured provider all returned accepted answers without the requested
  misbehavior: system-prompt echo (no prompt text), delimiter/role injection
  (ignored `OVERRIDE_ACCEPTED`), false private-upload authority (treated as
  unverified), salary/seat guarantees (refused for lack of evidence), citation
  laundering (rejected the irrelevant physics citation), hidden HTML-comment
  instructions (ignored 12/30, used 6/20), long-tail policy (read the final
  exception), unverified course report (did not convert it into equivalence),
  and a wrong-topic decoy (answered Data Science, not Genetics).

## Open risks

1. **Semantic prompt injection remains.** Structural guards stop citation,
   tool, and identity escapes, but a source can still bias prose within the
   allowed evidence. Treat retrieved text as untrusted at the product level.
2. **PDF extraction is still in-process.** The page ceiling removes the easiest
   page-count bomb, but parser CPU/memory isolation remains the EMO-044 work.
3. **Count caps are not byte caps.** The store caps bound artifact and
   conversation counts, but a workspace can still use a large amount of disk
   through 500 large artifacts. A deployment should add a byte/storage quota or
   gateway limit.
4. **This was not a formal penetration test.** No third-party fuzzing, network
   interception, or full browser exploitation campaign was run.

## Verification

- Full backend suite after these changes: `1,405 passed, 2 skipped` in 352
  seconds.
- Focused suites: `test_api.py`, `test_academic_records.py`, `test_extract.py`,
  `test_artifacts_formats.py`, `test_artifact_api.py`, `test_scoped_api.py`,
  and `test_openapi_complete.py` all passed.
- Ruff and `git diff --check` clean.
