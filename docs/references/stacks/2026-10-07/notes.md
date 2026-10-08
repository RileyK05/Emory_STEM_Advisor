# Design Notes & Open Issues

Living log of design observations, schema concerns, and deferred fixes.
Each entry is dated and tagged so it can be revisited or closed out.
If a concern is a **schema** problem, it gets a `[schema]` tag; if it is a
**non-schema** design issue, it gets a `[design]` tag. Decisions that are
resolved get moved to `docs/decisions/`.

> Nothing here is ever deleted. Resolved items are moved to **Closed** with a
> date and outcome, keeping a full log of what we decided and why.

## Open

**Queued for operator review / deeper design conversation:**

- **[design, OPEN — needs architecture conversation] Storage accounting vs
  decompression.** Today a file's quota cost is its *compressed* size, and
  a highly compressible 100 MB text file may only count as ~100 KB of
  quota. Nothing reads files back yet, so this is harmless today. But it
  points at a deeper question the operator wants to discuss: how storage
  should be accounted and read at the architectural level. For Milestone 1
  the hard rule is: any code that opens a stored file must unzip it
  streaming, with a cap on the unzipped size — never all at once (the
  current `read_stored` does a whole-buffer decompress and is the seam to
  replace). Broader accounting redesign (logical-vs-stored quotas, raw
  caps per file) is deferred to that conversation.
- **[design, OPEN] IP/registration throttling.** Email verification now
  gates resource creation (fixed 2026-09-10, below), but nothing limits how
  many accounts one IP can register. Operator ruling: email verification
  yes; whether IP-based limiting is feasible/desirable is unresolved —
  revisit before public launch.
- **[design, OPEN — needs architecture conversation] The learner side of
  the two-way memory model.** Ruling: course memory (the distilled
  per-course record in `course_memories`) is OWNER-ONLY — the owner's
  instrument for their course. Learners get the *generic* memory and
  harness, not the course's distilled record. What "generic memory" for a
  non-owner concretely means (what accumulates per learner across courses,
  where it lives, what the harness reads) is an open design question for
  the Milestone 2 memory subsystem — the owner-only fix below removed the
  wrong learner writes; it did not yet build the learner side.

## Closed

**Closed 2026-09-22 — workspace generation harness (decision 011).** The
tutor workspace pane (quiz/document/html, already gated per decision 009)
was extended to a six-type generation harness: code (highlighted, never
executed), sheet (editable grid, CSV export), and slides (markdown deck,
arrow-key navigation) joined the existing three, all riding the one
` ```workspace ` fenced-JSON mechanism in `src/backend/tutor/workspace.py`.
Prompt version 3→5 teaches the new blocks; live answer-eval re-run after
the prompt change is still pending (operator-triggered, delta recorded
under `runs/`). Resolved by `docs/decisions/011_workspace_generation_harness.md`.

**Closed by the 2026-09-10 operator rulings (from the adversarial
edge-case pass):**

- **[bugfix, ruled a bug] Course memory was being written for learners.**
  The memory bank fanned out to every participant on archive, enrollment,
  and canonical mutations — contradicting the two-way memory model (owner
  gets course memory; non-owners get the generic memory/harness). Fixed:
  `course_memories` writes are owner-only everywhere (archive writes the
  owner's record; enrollment writes none; upload/rename refresh the
  owner's only; archive-copy writes the copier-as-new-owner's). Learners
  keep their private per-user data (attempts, conversations, mastery) —
  that is a different subsystem, untouched. The learner-side generic
  memory remains Open (above).
- **[fixed] Email verification + password reset** (was the "no email check,
  no password reset" item). Migration 021: `users.email_verified_at`,
  `email_outbox` (operator/worker seam — no in-process delivery), and
  `email_tokens` (SHA-256 hashes at rest, single-use, 60-minute expiry,
  one open token per kind per user). API: verification request/confirm
  (token bound to the authenticated caller), password-reset request
  (anti-enumeration: always 202, unknown emails queue nothing) and
  confirm (rejects pending-deletion accounts; success marks the email
  verified — mailbox control proves the address). Verification gates
  course creation and uploads (403 with remediation); login stays open
  to unverified accounts so existing accounts are never locked out.
  Tests: `tests/test_email_verification.py` (flows, replay, cross-account
  binding, expiry, anti-enumeration, gating).
- **[fixed] Archive-copy naming.** Default copy names now respect the
  200-char create bound (suffix survives trimming) and disambiguate:
  "name (copy)", "name (copy 2)", "name (copy 3)"... Pinned by
  `test_copy_names_are_bounded_and_disambiguated`.

- **[schema] "Week" is too rigid.** Fixed by replacing `Week` with `StudyPeriod`
  (`period_id`, `course_id`, `label`, `start_date`, `end_date`). The window is
  user-definable (lecture, month, semester, before-exam) rather than a fixed
  week. Implemented in `schemas/identity.py`. Closed 2026-08-17.
- **[schema] `Page` and `Chunk` semantics.** Resolved to a generic **locator**
  model: object stored whole, `Locator(type, start, end, label)` as a per-format
  index ("table of contents"), and `Chunk` as a token-bounded slice pointing back
  to a locator. Citations use locator type + label. New formats = new locator
  type, no redesign. Implemented in `schemas/source_content.py`. Closed
  2026-08-17.
- **[schema] `Dependency` assumes in-course prereqs.** Fixed: `prereq_id` is now
  nullable (a concept may have no prereq), plus `prereq_kind` (`in_course` /
  `external`) and `external_ref` for out-of-course prereqs. Implemented in
  `schemas/memory.py`. Closed 2026-08-17.
- **[schema] `course_objects` storage/serving.** Split into `kind` (semantic
  purpose, free string so new kinds need no schema change), `content_type`
  (format), and `content_uri` (pointer to format-appropriate storage) + optional
  `content` dict. Implemented in `schemas/identity.py`. Closed 2026-08-17.

- **[schema] `Attempt` had no machine-readable score.** `evaluation` was free
  text only, leaving the mastery-ladder transitions nothing structured to
  consume. Added `score: float | None` (0.0–1.0, nullable so free-form attempts
  aren't forced into fake precision); `evaluation` stays as the narrative.
  Implemented in `schemas/student_model.py`. Closed 2026-08-18.
  *(Amended 2026-08-18: changed to `score: int | None`, 0–6 grade-like scale —
  0=incomplete, 1=fail, then D→A+ — matching how the student model will
  actually grade probes.)*
- **[schema] `Source` had no dedup or failure tracking.** Added `file_hash`
  (SHA-256 of file bytes, distinguishes revised upload from duplicate re-ingest
  regardless of filename) and `error_message` (populated when
  `status == failed`). Implemented in `schemas/source_content.py`. Closed
  2026-08-18.
- **[schema] `TocEntry` had no deterministic ordering.** Added
  `order_index: int = 0` so the outline can be reconstructed and re-ordered
  explicitly. A `parent_entry_id` hierarchy pointer was considered and deferred
  until something consumes it. Implemented in `schemas/memory.py`. Closed
  2026-08-18. *(Amended 2026-08-18: renamed `order_index` → `position` for
  clarity.)*
- **[schema] `Conversation` had no `updated_at`.** Added so the sidebar can sort
  by most recent activity without aggregating turn timestamps. Implemented in
  `schemas/chat.py`. Closed 2026-08-18.
- **[schema] `Concept` had no alternate names.** Added `aliases: list[str]`
  for alternative phrasings (e.g. "Factorization Theorem" vs "Fisher-Neyman").
  Implemented in `schemas/memory.py`. Closed 2026-08-18.
  *(Amended 2026-08-18: renamed `aliases` → `synonyms` for clarity.)*
- **[schema] Denormalized `course_id` on `Chunk` / `Dependency` /
  `ConceptMastery` — rejected.** Considered for query performance and declined:
  at single-user MVP scale the joins are negligible, and denormalization adds a
  consistency hazard. Revisit only with measured evidence. Closed 2026-08-18.
- **[schema] Cached `label`/`quote` on `Citation` — deferred.** Display-cache
  denormalization; joins are cheap at MVP scale. Can be added later as nullable
  fields with no migration pain if citation rendering or snapshotting needs it.
  Closed 2026-08-18.
- **[schema] `MemoryObjectEvidence.evidence_level` was a raw `str`.** Inconsistent
  with every other model (which uses the `EvidenceLevel` enum), so typos would
  pass validation. Changed to `evidence_level: EvidenceLevel =
  EvidenceLevel.DIRECT`. Implemented in `schemas/evidence.py`. Closed 2026-08-19.
- **[schema] `Attempt.score` locked in a 0–6 grade-like scale.** Conflicted with
  project.md's "do not collapse to a single fake-precise score too early."
  Loosened to `score: int | None` with a 0–100 bound, keeping it as structured
  evidence without hardcoding one grading scheme. `evaluation` stays narrative.
  Implemented in `schemas/student_model.py`. Closed 2026-08-19.
- **[schema] No canonical values for `Claim.claim_type` / `Citation.target_type`.**
  The fields stay free strings for extensibility, but there was no single source
  of truth for spelling, risking silent mismatches (e.g. "chunk" vs "chunks").
  Added `KNOWN_CLAIM_TYPES` and `KNOWN_CITATION_TARGETS` frozensets as the
  reference. Implemented in `schemas/base.py`. Closed 2026-08-19.
- **[schema] `CourseObject` could be created dangling.** Both `content_uri` and
  `content` were optional, so an object with neither was valid. Added a
  `model_validator` requiring at least one be present. Implemented in
  `schemas/identity.py`; test added in `tests/test_schemas.py`. Closed 2026-08-19.

## Resolved by design discussion (this session)

- **Embedding misalignment** avoided by not relying on embeddings for retrieval.
  Instead: a model-written, per-course **Table of Contents** (`TableOfContents` +
  `TocEntry`) describes what's in the course and where. Written by a small, stable
  TOC model so descriptions stay consistent over time. Grows and is versioned.
  Implemented in `schemas/memory.py`. Closed 2026-08-17.
- **Chat history in two forms**: raw chat (`Conversation` + `ConversationTurn`)
  shown to the user, and a compressed summary (`ChatSummary`) used as LLM context,
  regenerated when the conversation grows past a threshold. Implemented in
  `schemas/chat.py`. Closed 2026-08-17.
- **Multi-model strategy** (leaving "one model to rule"): a small TOC-updating
  model, plus the generative model, plus optional OCR/embedding models, each for
  its own task. Recorded as a direction; not yet fully engineered.

## Schema review pass (2026-08-19)

Code review pass on the full schema. Renames and structural fixes:

- **[design] "Provenance" naming removed.** The term was disliked; renamed the
  file `provenance.py` → `evidence.py`, and the classes/field:
  `ArtifactProvenance` → `ArtifactOrigin`, `ProvenanceRecord` → `ModelDecision`,
  `CourseObject.provenance` → `CourseObject.origin`. Implemented in
  `schemas/evidence.py`, `schemas/identity.py`.
- **[schema] `LocatorType` was an enum — defeated extensibility.** Same bug class
  as `kind`; a new locator type (e.g. `cell_range`) would require a code change.
  Removed the enum; `Locator.locator_type` is now a free `str`. Implemented in
  `schemas/source_content.py`.
- **[schema] `Attempt.concept_id` (single) vs `AssessmentItem.concepts` (list).**
  An item can test multiple concepts; an attempt must record against all of them.
  Changed `Attempt.concept_id` → `Attempt.concept_ids: list[UUID]`. Implemented in
  `schemas/student_model.py`.
- **[schema] `ConceptMastery` had no `user_id`.** Mastery is per-student, but the
  model only carried `concept_id` — two students' mastery would collide. Added
  `user_id`. Implemented in `schemas/student_model.py`.
- **[schema] `Claim.response_id` dangled (no `Response` object).** The tutor's
  answer wasn't modeled. Added a `Response` model (`response_id`, `conversation_id`,
  `turn_id`, `trace_id`, `content`, `model`). The source-grounding chain is now
  `Response → Claim → Citation → RetrievalTrace`. Implemented in
  `schemas/evidence.py`.
- **[schema] `ConversationTurn.role` was a free `str`.** Bounded to a new
  `MessageRole` enum (`user`/`assistant`/`system`) so typos are caught and
  filtering is reliable. Implemented in `schemas/base.py`, `schemas/chat.py`.
- **[schema] `Chunk.embedding` removed.** Leftover from the old embedding approach;
  TOC-based retrieval made it dead weight. Removed from `schemas/source_content.py`.
- **[schema] `MemoryObjectEvidence` had no primary key.** Added surrogate
  `evidence_id` UUID, consistent with every other table and avoiding composite-key
  friction in raw SQL. Implemented in `schemas/evidence.py`.

## Deletion & account lifecycle design (2026-08-20)

Unifying principle: **destruction leaves a distilled record.** Instead of hard
cascading deletes that silently wipe everything, we archive a distilled summary
before removal. Implemented across migrations `001`/`002` + schemas.

- **[design] Course deletion keeps a memory.** Before a course row is removed, a
  distilled `course_memories` record is written (code, name, summary, key concepts)
  so the course can still be referenced/answered-about later. The `course_id` in
  `course_memories` is stored without a hard FK so the memory outlives the course.
  Tables: `course_memories`. Closed 2026-08-20.
- **[design] Source removal compresses its citations.** Before a source's citations
  are removed, a `citation_snapshots` record is written (the citations + why each
  was valid) so grounding evidence is preserved even after the source is gone.
  Tables: `citation_snapshots`. Closed 2026-08-20.
- **[design] Account deletion uses a 7-day grace period.** `users` gained
  `delete_requested_at`; a delete is a soft marker, and the account is hard-removed
  after 7 days (cancel = clear the marker). Closed 2026-08-20.
- **[design] `ON DELETE` split, not blanket cascade.** Ownership trees (course →
  content, user → courses) cascade; evidence/grounding links (chunk → citation,
  memory_object → evidence) `RESTRICT` so the app is forced to snapshot before
  removing evidence. Implemented via app-layer archive-then-delete.
- **[auth] Added `users.email` + `users.password_hash`.** Auth is planned for the
  MVP (not deferred) since it's a known, easily-implemented problem. Email is
  unique (partial index, non-null emails only). Password hashing (bcrypt/argon2)
  and sessions are still to be built. Closed 2026-08-20.

## Migration layer (2026-08-20)

- `src/backend/common/migrations/001_init.sql` — full six-layer schema (9 enums,
  26 tables, indexes). Applied against `course_assistant`.
- `src/backend/common/migrations/002_auth_and_distilled_records.sql` — auth columns
  + `course_memories` + `citation_snapshots`. Applied.
- Cross-check fixes folded into `001` (re-applied cleanly): `idx_attempts_concept`
  → GIN (JSONB containment); UNIQUE `(user_id, concept_id)` on `concept_mastery`;
  UNIQUE `(course_id, version)` on `tables_of_contents`; hot-path FK indexes
  (`memory_object_evidence`, `dependencies.dependent_id`, `responses.trace_id`,
  `chat_summaries.conversation_id`, `attempts.course_id`); `idx_sources_course_hash`
  for dedup.
- `Locator.end` → `end_value` in SQL (reserved word); data layer maps it back to
  the Pydantic field `end`.
- **Pending:** a migrations runner to apply versioned `.sql` in order (currently
  applied manually via `psql`).
  *(Resolved 2026-08-20: `common/migrate.py` runner built — tracks applied
  versions in a `schema_migrations` table and applies pending `.sql` in order.
  Run via `python -m src.backend.common.migrate`.)*

## Auth foundation (2026-08-20)

- **[auth] Decided: JWT + bcrypt.** Stateless tokens (no session table), pairs
  with FastAPI + separate frontend. bcrypt is battle-tested and simple. Deps
  added: `bcrypt`, `PyJWT`. Closed 2026-08-20.
- **[infra] `common/config.py`** — minimal `.env` loader (no python-dotenv dep)
  exposing `Settings` (Postgres DSN + `jwt_secret` + `jwt_expire_minutes`).
  Closed 2026-08-20.
- **[infra] `common/db.py`** — the single Postgres connection seam (`connect()` +
  `connection()` context manager). Closed 2026-08-20.
- **[auth] `common/auth.py`** — pure auth utilities: `hash_password`/
  `verify_password` (bcrypt, salt-random), `create_access_token`/
  `decode_access_token`/`token_user_id` (JWT HS256, 7-day expiry from config).
  No FastAPI coupling; fully unit-tested. Closed 2026-08-20.
- **[config] `JWT_SECRET` required in `.env`.** Dev default is short and emits an
  `InsecureKeyLengthWarning`; a real 32+ byte secret silences it. User to set.

## Auth API + query layer (2026-08-20)

- **[infra] `common/queries/`** — raw SQL lives in `.sql` files (per AGENTS.md
  convention). A tiny loader (`common/queries/__init__.py`) splits files into
  named blocks via `-- name: block` markers and caches them. Every subsystem
  uses this; no inline SQL strings. Closed 2026-08-20.
- **[repo] `common/users_repo.py`** — user CRUD via the query loader + `db.py`.
  `create` uses autocommit (INSERT...RETURNING); reads use `dict_row` cursors.
  Closed 2026-08-20.
- **[api] `src/backend/api/auth.py`** — FastAPI router: `POST /auth/register`,
  `POST /auth/login` (returns JWT), `GET /auth/me` (protected). `current_user`
  dependency decodes the bearer token, looks up the user, and blocks
  pending-deletion accounts. Closed 2026-08-20.
- **[api] `src/backend/main.py`** — FastAPI app factory (`create_app`) + `app`
  instance. Run with `uvicorn src.backend.main:app`. Closed 2026-08-20.
- **[deps] Added `fastapi`, `uvicorn[standard]`, `httpx` (dev/test).** Closed
  2026-08-20.
- **[tests] Repository + endpoint tests** against the live Postgres (autouse
  fixture cleans `*@test.invalid` users). 46 tests total, all green.

## Architecture review implementation (2026-08-25)

- **[security] Public/internal user boundary.** `User` no longer carries
  `password_hash` or `delete_requested_at`; internal account state moved to
  `UserAccount`. Auth endpoints explicitly return the public model, with tests
  proving credential fields are absent. Registration now normalizes email and
  validates bcrypt-safe password bounds. JWT issuer/audience/required claims are
  enforced, pending-deletion login is blocked, and production rejects the
  development signing secret. Closed 2026-08-25.
- **[schema] Course access model.** Courses now have an owner and private/public
  visibility. `course_memberships` grants a learner read/use access without
  source mutation. Authorship (`created_by_user_id`) is distinct from ownership;
  personal attempts, mastery, chat, and recommendations remain private. Member
  copying/forking is deferred. Decision: `docs/decisions/001_course_access_and_source_objects.md`.
  Closed 2026-08-25.
- **[schema] Source/course-object specialization.** `sources.object_id` is a
  unique FK to `course_objects.object_id`; migration 003 backfilled generic
  object rows for existing sources. Migration 004 makes the FK include course
  and creator identity so the two records cannot cross tenants. Source creation
  must write both rows in one transaction. Closed 2026-08-25.
- **[security] Course access database invariants.** Migration 005 rejects
  course-object and private student records whose user is neither the owner nor
  an active member. It also enforces that base source objects are owner-created,
  member-scoped, and backed by a source-kind generic object. Application
  permission checks remain the actor-level authorization boundary. Closed
  2026-08-25.
- **[ingest] Ordered retryable pipeline.** Added persisted ingestion run/stage
  state, dependency links, attempt counts, handler/config versions, and an
  executor that retries a failed stage twice in total, then stops before later
  stages. Versioned defaults live in `configs/ingestion.toml`. Closed 2026-08-25.
- **[evidence] Citation snapshot contract.** Archived citations now require the
  original claim, target, locator, excerpt, and explanation of validity; source
  hash and archive reason are retained when available. The archive-then-delete
  service itself remains unimplemented and is explicitly marked planned in
  `system.md`. Closed 2026-08-25.

## Open after architecture review (2026-08-25)

- **[security] External-deployment auth controls.** Add login throttling and
  decide token revocation/rotation before exposing the service publicly.
- **[design] Shared-course copying.** Members can currently view/use base sources
  and create their own artifacts but cannot copy/fork the base course. Revisit
  only when a concrete copying workflow is requested.

## Public discovery and enrollment revision (2026-08-26)

- **[access] Visibility and enrollment separated.** Anonymous visitors may view
  owner-published canonical objects on public courses, but source use and model
  generation require a signed-in owner or active enrollment. Public courses
  allow self-enrollment; private courses require an owner invitation. Migration
  006 replaces course-membership terminology with course enrollment. Decision:
  `docs/decisions/003_public_enrollment_and_personal_artifacts.md`. Closed
  2026-08-26.
- **[privacy] Learner generations are personal artifacts.** Learner-generated
  materials now live outside canonical course objects and remain visible only to
  their learner, including after enrollment revocation or course deletion. A
  course owner cannot inspect or mutate them merely by owning the source course.
  Copying or promotion into canonical content remains deferred. Closed
  2026-08-26.
- **[tutor] Requester-scoped presentation.** Owners may use their own structured
  tutor profile; non-owners use the versioned generic profile in
  `configs/tutor.toml`. Profiles may change presentation but never TOC,
  retrieval, evidence, citations, evaluation, or mastery. Closed 2026-08-26.

## Session status (2026-08-29)

Verified baseline at session start — full quality gate green:

- **Tests:** 81 passed (pytest).
- **Lint:** ruff clean.
- **Typecheck:** mypy clean across 30 source files.
- **Working tree:** uncommitted — migrations 003–006, `common/permissions.py`,
  `ingest/pipeline.py` + `ingest/config.py`, `tutor/profile.py`,
  `schemas/ingestion.py` + `tutor.py`, `configs/` (`ingestion.toml`,
  `tutor.toml`), `docs/decisions/001–003`, and matching test updates. This is
  the 2026-08-25/26 architecture-review + enrollment-revision work, complete
  and verified but not yet committed.
- **Milestone position:** Milestone 0 done. Milestone 1 (auth + source-grounded
  retrieval) in progress: auth, course access model, and the ordered ingestion
  pipeline skeleton are implemented; file parsing, locators/chunks population,
  TOC-guided retrieval, cited answers, and retrieval traces remain planned.
- **Still open (carried):** login throttling + token revocation before external
  deployment; archive-then-delete services unimplemented; model provider
  unchosen (no-retention policy must be verified).

## Pre-commit review of uncommitted changeset (2026-08-29)

Tactical review before committing: deep pass on security + business logic
(permissions.py, migrations 003–006, auth stack, ingest pipeline); light pass
on schemas/tests/docs. Baseline gate was green (81 tests, ruff, mypy).

- **[security] Clean.** Permission matrix matches decision 003 exactly
  (anonymous → published objects only; enrollment → use/generate, never
  canonical mutation; artifacts private to learner). DB invariants in 005/006
  back the app layer correctly; 004's composite FK prevents cross-tenant
  source/object mismatch. Auth: dummy-hash anti-enumeration on login, bcrypt
  72-byte bound handled, JWT iss/aud/claims enforced, production rejects dev
  secret. No findings requiring code change now.
- **[design] Revoked enrollment has no re-activation path.** `can_self_enroll`
  requires `enrollment is None`, and the UNIQUE `(course_id, user_id)`
  constraint blocks a new row, so a revoked learner can never return without
  manual SQL. Worse: the enrollment policy trigger only fires on
  INSERT/UPDATE OF (course_id, user_id, enrollment_source, invited_by_user_id)
  — a direct `UPDATE ... SET status='active'` bypasses re-validation (e.g.
  course went private meanwhile). Acceptable for MVP; must be closed before
  any real invitation flow ships.
- **[security, low] Pending-deletion login leaks state pre-auth.** `/auth/login`
  returned 403 before password verification for `delete_requested_at` accounts —
  disclosing account existence + deletion state without credentials, and timing
  differed from the dummy-hash path. **Fixed 2026-08-29:** password is now
  verified before the deletion check, so unauthenticated callers cannot probe
  account state.
- **[design, low] Enrollment trigger vs CHECK ordering.** An invitation row
  with NULL inviter passes `enforce_course_enrollment` (`NULL <> owner` →
  NULL → no raise) but is caught by the `course_enrollment_source_consistency`
  CHECK with a less clear error. Net safe; cosmetic only.
- **[design, low, deferred] `content_uri` is unvalidated.** Both
  `course_objects` and `user_artifacts` store free URIs; the future serving
  layer must resolve them safely (no arbitrary path reads). Noted for
  Milestone 1 retrieval work.
- **[perf, negligible] `get_settings()` re-reads `.env` per call** (every token
  create/decode). Fine at MVP scale; add an lru_cache if it ever shows up.

### Ingestion business-logic review (2026-08-29)

Traced config → executor → schema → Pydantic models end to end.

- **[design] The pipeline is a skeleton with unwired seams.** All pieces exist
  independently — `ingestion_runs`/`ingestion_stage_runs` tables (003),
  `IngestionRun`/`IngestionStageRun` schemas, `execute_pipeline` with its
  `observe` hook, and versioned config — but nothing connects them: there is
  no ingestion repo (queries dir only has `users.sql`), no caller of
  `execute_pipeline`, and no code that transitions run-level
  `pending → running → succeeded/failed`. This is the actual Milestone 1 gap:
  wiring, not pieces.
- **[design] Run-level failure ownership is unspecified.** If the caller of
  `execute_pipeline` forgets to catch `IngestionPipelineError` and mark the
  run failed, a failed run stays `running` in the DB forever. The repo layer
  should own this (context-manager or explicit try/finally), not each caller.
- **[design] `StageHandler = Callable[[], None]` carries no context.** Real
  handlers (parse, locators, chunks, TOC, memory) need source/run/config
  context; the signature will change or handlers become closures. Decide when
  wiring the first real handler.
- **[design] Idempotency is documented but unenforced.** system.md requires
  retry-safe handlers; `execute_pipeline` neither provides delete-before-write
  hooks nor checkpoints. Enforcement will live entirely in handlers — make it
  a stated contract when the first real handler lands.
- **[design, minor] `depends_on_stage_id` has no producer.** The schema stores
  per-stage dependency links but `execute_pipeline` uses the hardcoded linear
  `PIPELINE_STAGES` order. Consistent today (linear == dependency order);
  either populate the links when wiring or drop the column later if the
  pipeline stays linear.
- **[verified clean]** Config validation forces exact stage order match;
  retry-then-halt semantics match system.md (2 total attempts, later stages
  unstarted); `except Exception` doesn't swallow KeyboardInterrupt; observer
  sees final FAILED before the raise. Tutor profile selection logic (generic
  must be user-less, owner profile ownership-checked, non-owners always
  generic) is correct. 006 backfill ordering is trigger-safe (backfill runs
  before the policy trigger exists).
- **[security, low, app-layer] Artifact ownership transfer unchecked at DB
  level.** `enforce_user_artifact_access` validates only that the (possibly
  new) `user_id` has course access — it cannot check the mutating actor.
  Ownership enforcement must come from `can_manage_user_artifact` at the API
  layer when mutation endpoints exist.

### Broad-review gaps: compute-spend control (2026-08-29)

Architectural pass for the "wasted tokens" class of business rule. Verified
covered: anonymous users cannot generate (`can_generate_materials` requires
auth), sources are enrollment-scoped so raw material is not anonymously
scrapable. Not covered — all one class, **nothing limits spend**:

- **[design] Stranger token-spend chain.** Open registration (no invite code,
  no throttle) + public-course self-enrollment + generation rights = anyone
  can burn the operator's API budget. Acceptable while the user base is known
  people; must be closed (per-user daily generation budget, or generation
  restricted to invited enrollments) before any course goes truly public.
- **[design] No rate limiting on any model-compute path** — generation
  frequency, registration spam, ingestion frequency. Generalizes the already
  open login-throttling item to all compute endpoints.
- **[design] No upload size limit / per-course ingestion budget.** One huge
  upload = one expensive ingestion run (TOC-writer + memory extraction are
  model calls).
- **[design] Retry double-spend.** Model-calling stages (TOC, memory) are not
  idempotent-by-contract yet: if the model call succeeds but the DB write
  fails, the retry re-pays for model output. The idempotency contract needs
  "persist model output before any failure point."
- **[design] Chat caps absent.** Summary compression caps context, not
  conversation/turn count.
- Linked to known-open items: login throttling, token revocation (a leaked
  7-day JWT is 7 days of generation spend).

## Tiers and spend control implementation (2026-08-29)

Closes the spend-control class above at the schema/config level. Decision:
`docs/decisions/004_tiers_and_spend_control.md`. Migration 007 applied.

- **[schema] `users.tier` (default free), synced from `user_subscriptions`.**
  Subscription history is append-only; at most one active subscription per
  user (partial unique index); a trigger is the sole writer of `users.tier`, so
  tier can never disagree with history. Ending the last subscription resets to
  free.
- **[schema] `generation_ledger` is append-only.** One row per model call:
  user, course, task, model, input/output tokens. Spend is inspectable, never
  edited. `KNOWN_GENERATION_TASKS` in `schemas/base.py` is the spelling source
  of truth (tasks stay free strings).
- **[config] `configs/tiers.toml` — versioned tier policy.** Per tier: weekly
  token budget + model routing per task role (`answer`, `toc_writer`, `probe`,
  `extraction`). Free runs the cheap generative model, paid the newer one,
  TOC-writer shared. Loader validates every tier defines every role. New tiers
  are a config section + enum value, not a schema redesign.
  *(Amended 2026-08-29: routing keys were unified to the
  `KNOWN_GENERATION_TASKS` vocabulary — the config now routes
  `tutor_answer`/`toc_update`/`probe_generation`/`probe_evaluation`/
  `memory_extraction`/`artifact_generation` — closing a two-vocabulary
  mismatch between the ledger's task strings and the config's routing keys.
  See "Session review fixes" below.)*
- **[code] `common/tiers.py` + `common/spend_repo.py` + `common/budget.py`.**
  Weekly window is rolling Monday 00:00 UTC. `check_budget` gates compute and
  returns an inspectable `BudgetState` (spent/budget/remaining/week start);
  it gates but does not reserve (brief overshoot under concurrency accepted).
- Tests: 88 passing (migration invariants, subscription lifecycle sync, tier
  routing, weekly window, ledger, budget guard, tier on auth responses).
- **Still open:** payment/billing flow (operator-managed for now); rate
  limiting per request (budget caps weekly volume, not burst rate); the
  ingestion model calls must actually be recorded when wired (the seam exists,
  the callers don't yet); retry double-spend remains open per above.

## Course limits per tier (2026-08-29)

Added `max_owned_courses` to tier policy (`configs/tiers.toml` v2: free 2,
paid 20). `budget.check_course_limit` gates course creation against the
count of owned courses; enrollment elsewhere is unlimited, course deletion
frees the slot. Rationale: cap storage/quota farming, not collaboration.
Decision doc 004 updated; conftest now also cleans `TEST-%` courses.

## Paid budget bump + course storage caps (2026-08-29)

- **[config] tiers.toml v3.** Paid weekly token budget 1M → 2M (free stays
  100k). New `max_course_storage_bytes`: free 500 MB, paid 5 GB per course.
  *(Config is now v4 — routing keys unified to `KNOWN_GENERATION_TASKS`, see
  "Session review fixes".)*
- **[schema] Migration 008.** `sources.size_bytes BIGINT` (nullable,
  non-negative check, course-size partial index). The upload path records
  actual stored bytes; accounting reflects files on disk, not requests.
- **[code] `budget.check_course_storage(course_id, tier, policy,
  incoming_bytes)`** — pre-upload gate; sums course sources, projects with the
  incoming file, raises `StorageLimitExceededError` past the cap, returns the
  projected total for headroom display. A file larger than the entire cap is
  rejected outright.
- **[test-infra] conftest cleanup order fixed.** Test-course teardown now
  deletes generation_ledger → course_objects → courses → users (FK direction),
  keyed off test users rather than course-code patterns.
- Still open: a per-course *source count* cap was considered and skipped —
  storage bytes + weekly ingestion budget cover the farming vector.

## Funky business logic: ratification list (2026-08-29)

End-to-end review pass. None of these are wrong; all are defensible but
arguable. Each needs an explicit "intended" or a change before/with commit.

1. **Spend follows the spender, not the course.** An enrolled free-tier
   learner generating inside a paid owner's public course uses their own
   (free) model routing and budget. "Paid gets better models" applies to the
   person who paid, never to everyone in their course. Ratify: spend always
   bills the requester.
2. **Owner-subsidized course upkeep.** Ingestion model calls (TOC updates,
   memory extraction) charge the *uploading owner's* budget, but enrolled
   learners retrieve against that maintained TOC for free. Course maintenance
   is an owner cost; learning is a learner cost. Ratify.
3. **Downgrade never destroys data (soft landing).** A paid user with 20
   courses / 5 GB-per-course storage who downgrades keeps everything; they
   are only blocked from *new* courses/uploads until under the free caps.
   Alternative would be forced deletion — rejected; deletion requires the
   distilled-record ceremony. Ratify.
4. **Caps are per-course, not per-user aggregate.** Theoretical worst case
   per paid user: 20 courses × 5 GB = 100 GB. There is no aggregate
   user-level storage cap. Consider: add one if real usage ever approaches
   operator disk; config knob, not schema change.
5. **Ownership capped, enrollment uncapped.** A learner may enroll in any
   number of public courses; enrollment stores no bytes and generation bills
   their own budget. Ratify.
6. **No independent per-file size cap.** A single upload is only bounded by
   the course cap (so a 499 MB file into an empty free course is accepted,
   then ingestion token-charges the owner for it). Consider: a small
   per-file ceiling (e.g. 100 MB) as a cheap early rejection.
7. **Budget gate-not-reserve.** Concurrent requests can overshoot the weekly
   budget slightly. Accepted at MVP scale; reservation would need locking.
   Ratify.
8. **Weekly window is Monday 00:00 UTC**, not user-local, so a Sunday-night
   burst plus Monday-morning burst both fit fresh budgets ~hours apart.
   Standard behavior for weekly limits; ratify the definition.
9. **Token budgets price tokens, not dollars.** Paid users get a bigger
   token budget *and* pricier models — in dollar terms the tiers differ even
   more than the numbers suggest. Fine while we pay the provider bill; a
   dollar-denominated budget is a possible future switch.
10. **Callers pass the tier into `check_budget`/`check_course_limit`.** The
    gates trust the caller-supplied tier; nothing cross-checks
    `users.tier` at that seam. The endpoint contract (gates-without-callers)
    resolves tier from the authenticated account; a defensive re-check
    inside the gate is possible if we want belt-and-braces.
11. **Ledger survives course deletion with `course_id` nulled.** Spend
    history loses course attribution when a course is deleted. Consistent
    with distilled-record principle (row survives, reference does not).
    Ratify.
12. **Known-open carried items:** revoked-enrollment dead end;
    re-activation bypasses the enrollment policy trigger (status-only UPDATE
    doesn't fire it); no burst rate limiting; billing flow is operator
    manual.

Verified during this pass: one-active-subscription partial unique index
blocks a second active row per user (probe confirmed UniqueViolation);
migration chain 001→008 applies cleanly to an empty schema; DB columns match
Pydantic models field-for-field on every touched table.

## Open: gates without callers (2026-08-29)

**Contract reminder for Milestone 1 wiring.** The spend/limit gates exist and
are tested, but their enforcement points are future endpoints. When building
them, the caller is responsible for the full gate sequence — the gates do not
call each other:

- **Upload endpoint** (course sources): owner-only (`can_manage_sources`) →
  course-limit check → storage checks (per-course AND aggregate) with actual
  file bytes → compress on entry where the format allows → write source +
  course object rows → then ingestion pipeline starts. All gates must run
  *before* any file write; `size_bytes` records post-compression stored bytes.
- **Generation endpoints** (tutor answers, probes, artifacts, summaries):
  authn → enrollment/access check (`can_generate_materials`) → `verify_tier` →
  `check_budget` → resolve model via tier policy → call model →
  `spend_repo.record_generation` with real token counts plus
  `free_tier_overhead` for free-tier users (record *after* the call; record
  even if the caller's access lapsed mid-flight — spend must be visible; the
  overhead only affects the *next* gate, never an in-flight answer).
- **Course creation endpoint:** `check_course_limit` before insert.

Every endpoint that spends tokens or accepts bytes must go through these
seams; none of the gates self-execute. Whoever builds the endpoints wires the
calls; if an endpoint exists without its gate calls, that is a bug, not a
shortcut.

## Ratified business-logic decisions (2026-08-29)

Operator review of the ratification list above. All decisions final for this
changeset; migrations 009/010/011 + `tiers.toml` v5 implement them.

1. **Spend bills the requester, not the course.** Confirmed as intended —
   usage credits are user-scoped, so making 20 duplicate courses cannot
   multiply generation. (Was already the behavior; now ratified.)
2. **Course upkeep is owner-paid.** Ingestion model calls charge the
   uploading owner's budget; enrolled learners generate against their own.
3. **Downgrade soft landing + 60-day limit.** Excess kept while blocked from
   new creation; `downgrade_grace_deadline` set by trigger on subscription
   end, cleared on resubscribe. Past 60 days, excess may be removed by the
   operator (archive-first). Removal sweep itself is still a planned service.
4. **Caps reduced.** Per-course: free 100 MB, paid 1 GB. Aggregate per owner:
   free 1 GB, paid 10 GB (`max_total_storage_bytes`, migration-checked gate
   `check_total_storage`). Compression on entry to be implemented in the
   upload path.
5. **Enrollment fixed.** Migration 011 revalidates on any enrollment UPDATE
   (status-only re-activation no longer bypasses policy: self-service
   re-activation on a since-privatized course is rejected; valid invitations
   re-activate cleanly). Revoked learners can return through the validated
   path.
6. **Compression on entry.** Upload contract: compress where the format
   allows; `size_bytes` records stored bytes. Implementation lands with the
   upload endpoint.
7. **Free-tier 5% overhead.** Applied at record time (`overhead_tokens`,
   migration 009; counts in `weekly_spend`), never mid-generation. Gate
   semantics unchanged: in-flight answers are never cut off.
8. **Weekly window Monday 00:00 UTC** — ratified as-is.
9. **Token-denominated budgets** — ratified; dollar budgets deferred.
10. **Tier double-verification.** `budget.verify_tier(user_id, tier)` re-checks
    against `users.tier`; endpoints must call it.
11. **Ledger keeps `course_label`** snapshot (migration 009 backfilled), so
    spend attribution survives course deletion.
12. **Carried open items:** burst rate limiting is *rejected by design* — the
    weekly budget is the only throttle; a user burning their whole budget in
    one session is intended behavior (operator ruling 2026-08-29). Billing
    flow is operator-manual until Stripe (or similar) is integrated. Removal
    sweep for post-grace downgrades waits on the archive-then-delete service.

Gate after implementation: 99 tests, ruff, mypy green.

## Premium claim codes (2026-08-29)

Operator request: every account can hold a code that may or may not grant
premium, serving as a recovery path ("enter premium code") if authentication
misbehaves, plus easy manual testing before Stripe. Migration 012 +
`premium_codes_repo`.

- **[security] Codes stored as SHA-256 hashes** (plaintext shown once at
  issue, code alphabet excludes ambiguous glyphs, normalization strips
  separators and case). A DB leak reveals no usable codes — same principle
  as password hashing.
- **[design] Claim requires an authenticated user.** Anonymous callers can
  never flip tiers (decision 003 boundary holds). Tiers are granted by
  `redeem` starting a paid subscription through the existing machinery.
- **[schema] Bound codes:** `issued_for_user_id` binds a code to one account;
  claiming user deletion fully releases the claim (trigger resets claimed_at
  and grants_premium so the code is claimable again — caught via teardown
  FK-SET-NULL probing).
- **[bugfix from tests] `mark_claimed` originally forced
  `grants_premium = TRUE`**, which would have made every personal recovery
  code a premium code on claim. Fixed: claiming never changes what a code
  grants; the operator flag decides.
- Duplicate code issue surfaces as `ValueError("code already exists")`.
- Still open: registration wiring to auto-issue a personal code per account
  (repo seam ready), operator CLI/endpoint for issuing and flagging codes.

## Customer-support code clarification (2026-08-30)

The per-account claim code is a customer-support and entitlement-pipeline
identifier, not a primary login factor, authentication fallback, or course
sharing credential. It supports cases such as subscriptions or payments handled
through another avenue. Registration displays the code once and stores only its
hash. Course invitation/share codes, if adopted, are a separate future concept
with their own lifecycle and permissions; course creation must not mint another
account support code.

## Superseding support-code and archive decision (2026-08-30)

The sentence above saying registration displays the support code is rejected.
Registration creates the bound hash record but **no API ever returns the
plaintext or code metadata**. The initial plaintext is discarded; support must
rotate internally and deliver a replacement out of band. Redemption requires an
already authenticated account and grants an entitlement only. It never proves
identity, replaces login, or participates in ordinary billing.

Course sharing now uses a distinct server-generated random join code with
private / invite-only / public exposure rules and explicit invitation
acceptance. Deletion retains the exact course for a 90-day copy grace period,
then purges it through a durable cleanup job. Each participant's bounded
course-memory record, including a compact evidence snapshot, is the only
permanent course-derived record. Public-to-restricted changes archive the old
public version and return a new restricted successor. Full decision:
`docs/decisions/005_course_sharing_archives_and_support_codes.md`.

## Second-model review fixes (2026-08-30)

Independent adversarial review of the same changeset found seven defects and
a set of consistency gaps; all were fixed in this pass. Migration 016 +
`lifecycle.toml` v2 implement the schema-level ones.

- **[bugfix] Redeeming a premium code while already subscribed was a 500.**
  `user_subscription_one_active` raised through `insert_subscription` with no
  ValueError mapping. Redeem now locks the user row, checks the active
  subscription first, and rejects with "already has an active subscription"
  (code stays unclaimed, transaction atomic). Support-code rotation also no
  longer grants premium by default — the entitlement is opt-in so a pure
  re-identification rotation cannot mint premium.
- **[bugfix] `assert` was request-path error handling.** `update_course` /
  `rotate_join_code` repo `None` returns (raced archival) became
  `AssertionError` 500s and vanish under `python -O`. Now explicit 404s.
- **[bugfix] Enrollment mutations didn't map policy-trigger failures.**
  invite/accept/decline/revoke/leave could 500 on a concurrent archive;
  `join_course` mislabeled the CheckViolation as "already enrolled" (409).
  All CheckViolations now map to 404 "course not available for enrollment".
- **[bugfix] Learner leave now declines pending invitations.** A learner
  refusing their own invitation was recorded as owner-revoked
  (`revoked_at` set); semantics now: learner-refused → `declined`,
  owner-rescinded → `revoked` (separate `withdraw_enrollment` query).
- **[schema] Public→restricted is enforced at the repo seam.**
  `courses_repo.update_course` now raises `RestrictedTransitionError` for
  public→restricted plain UPDATEs; only the versioned lifecycle path may
  perform it. Previously the 90-day-grace invariant lived in exactly one API
  caller, silently bypassable by any future script/endpoint.
- **[schema] `course_memories`/`citation_snapshots` user FKs now CASCADE**
  (migration 016). Account hard-delete previously landmined on any user with
  a memory bank — i.e. every course owner — and conftest quietly pre-deleted
  both tables to work around it. Deletion ceremony ownership stays with the
  account-deletion service; the FK just no longer contradicts it.
- **[schema] Cleanup jobs have a terminal `dead` state.** Attempt counts were
  tracked but never bounded: a wedged directory retried every 5 minutes
  forever. `cleanup_max_attempts` (5) + `retry_delay_seconds` (300) in
  `lifecycle.toml` `[cleanup]`; expired-lease reclaim and failure handling
  both dead-end at the cap; `claim_cleanup` no longer re-claims 'running'
  rows directly — `reclaim_expired_cleanup_leases` is the only re-entry.
- **[schema] `course_memories.updated_at` added** (migration 016); upsert no
  longer rewrites `created_at`, and the memory-bank listing orders by it.
- **[bugfix] Archive copy now refuses file-backed-only objects.** Courses
  with `content_uri`-only course objects (no jsonb `content`) previously
  copied them as silently-empty objects; copy now fails loudly with
  `UncopyableCourseError` (409) instead. Cross-course concept prereqs are
  dropped rather than NULLed during copy.
- **[infra] Storage orphan sweep added.** Upload's crash window between file
  write and DB commit (and any future rename/delete gap) left orphaned
  directories with no cleanup path; the maintenance loop now sweeps
  UUID-named directories with no `courses` row, ignoring non-UUID entries.
  *(Amended 2026-08-30, second review: the first version only swept
  directories whose course row was gone — an upload crash orphans a FILE
  inside a live course directory, which the directory sweep never touched,
  and it had no age guard, so it could race an in-flight copy. The sweep now
  also removes source files whose source row is absent, guarded by
  `cleanup.orphan_min_age_seconds` (3600) so in-flight uploads are never
  swept. The sweep is the fallback of last resort, not the primary cleanup.)*
- **[hygiene] Exception/request-model consistency.**
  `storage.StorageLimitExceededError` renamed
  `RawUploadLimitExceededError` (body-size 413) to stop colliding with
  `budget.StorageLimitExceededError` (quota 403). All mutating request models
  now `extra="forbid"` like `CourseCreate`. Register's UniqueViolation
  handler distinguishes the email index (409) from a code-table collision
  (503, retry-safe) instead of reporting "email already registered" for both.
- **[test-infra] Lifespan maintenance loop is now exercised** (was zero-
  tested; `TestClient` outside a `with` never started it).
- **Known cosmetic, deliberately not fixed:** migration 013's header comment
  says `013_storage_foundations.sql` but the file is
  `013_upload_foundations.sql`. The migration is applied; editing an applied
  file's comment is not worth breaking the append-only rule. Recorded here
  instead. Evidence-snapshot wording in decision 005 softened to
  "representative first-chunk excerpts of up to ten sources" to match the
  implementation honestly.

## Self-review of the fallback-audit fixes (2026-09-04)

- The 2026-09-03 fix pass itself got a second review. Findings:
- **[design] The dir-level sweep still had the race**: the mtime grace
  applied to file removals only; the directory-level branch rmtree'd any
  dir with no courses row regardless of file age, so an in-flight
  restrict/copy target dir (files written pre-commit) could still be
  deleted mid-transaction. The grace is now applied uniformly: a course
  dir is removed only when every file in it is older than the grace, and
  the sweep uses `all(...)` not `any(...)` over entries (the first draft
  of the rewrite inverted this; caught in self-review before commit).
- **[design] Staging debris was never swept**: crash mid-`_atomic_write`
  leaves `.{source_id}-{rand}.tmp` staging files; `course_source_files`
  skipped them (warning hourly, removing nothing) so they accumulated
  unboundedly. New `storage.course_staging_files()` + sweep branch removes
  aged staging files; fresh staging files (in-flight writes) are kept.
- **[test-infra] `api/auth.py` redeem-path assert replaced** with an
  explicit 401 (was planned 2026-09-03 but the session cut off before
  the edit). Request paths are now assert-free.
- **[design] RawUploadLimitExceededError message said "storage
  allowance"** - misleading: it is the per-request raw-body ceiling (413),
  not the stored-bytes quota (403). Message corrected.
- **[test-infra] New sweep tests**: fresh orphan dir survives the grace
  and is removed without it; interrupted staging file removed once aged;
  fresh staging file kept. Existing sweep tests age their files with
  `os.utime` since the grace now protects fresh artifacts by default.
- **Verified sound in the same pass** (business-logic review of the
  full uncommitted changeset): subscription double-start blocked by the
  partial unique index + user-row lock in redeem; archive copy-once via
  get_archive_access FOR UPDATE + mark_copied; upload quota serialization
  on the owner user-row lock; restrict/copy storage math (owner_size
  already includes the source course); enrollment trigger fires on INSERT
  OR UPDATE (011) so upsert re-activations are policy-checked; subtree
  deletion spares course_memories by design (no FK, survives course row).

## Canonical code format + API tightening (2026-09-05)

- **[design] One canonical shareable-code format.** Join codes (courses) and
  support/premium codes now share `common/codes.py`: 16 characters from a
  31-symbol alphabet (I/L/O/0/1 removed), display form XXXX-XXXX-XXXX-XXXX
  (20 chars with dashes). Previously join codes were 12 chars and premium
  codes 16 with a duplicated alphabet/generation implementation.
  Migration `017_canonical_code_format.sql` adds DB CHECKs (course code
  alphabet/length; premium hash is 64-hex) and regenerates existing course
  codes. Closed 2026-09-05.
- **[api] Format validation at the boundary.** `JoinCourseRequest.join_code`
  and `SupportCodeRequest.code` now normalize + validate via
  `codes.require_valid` (422 on garbage) before any lookup.
- **[api] Enum-typed API views.** `EnrollmentView.status`/
  `enrollment_source` and `MemberView.status` are now the `EnrollmentStatus`
  / `EnrollmentSource` enums instead of plain strings — identical JSON wire
  format, but self-documenting OpenAPI and typo-proof at the boundary.
- **[design][OPEN] MemberView exposes learner emails to the course owner.**
  `GET /courses/{id}/members` returns each learner's email (decision 003
  allows owner visibility of enrollment emails). Risk: emails are personal
  contact data; an owner seeing emails may enable off-platform contact or
  spam. Options if this matters later: return user_id + display name only,
  gate behind an owner-only setting, or drop emails entirely. Revisit if
  the member list ever grows beyond trusted/operator-run courses.

## The three course shapes pinned as a contract (2026-09-05)

- **[design] Foundational fact recorded.** Every course is exactly one of
  `public` / `invite_only` / `private`; that closed set is the standing
  contract for all current and future development. Existing permissions
  (owner-only canonical mutation, enrollment-grants-use, learner privacy)
  are unchanged in every shape. Decision:
  `docs/decisions/006_three_course_shapes.md`. Closed 2026-09-05.
- **[design] `permissions.py` drift caught and fixed.** The module was orphaned
  (no endpoint imported it; the API re-derived checks inline) and
  `can_self_enroll` required `enrollment is None`, which silently contradicted
  migration 011's revoked-learner re-enrollment path. Fixed: re-enroll allowed
  when the enrollment is not active; added `can_join_with_code` encoding the
  two-joinable-shapes rule (public, invite_only; never private). Tests added.
  Standing rule: policy changes must touch permissions.py AND the DB triggers
  together. Closed 2026-09-05.

## Docs resync to implementation (2026-09-05)

- **[docs] project.md + system.md updated to the current truth** (overwrite,
  per convention — notes stay append-only). Key additions:
  - Three course shapes recorded as the closed contract (decision 006) in
    both docs, including per-shape join-code exposure and the
    permissions.py + triggers change-together rule.
  - Canonical code format (16 chars, XXXX-XXXX-XXXX-XXXX display,
    `common/codes.py`, migration 017 CHECKs) in project.md principles +
    system.md cross-cutting.
  - project.md milestones: new Milestone 0.5 marked done (auth, course CRUD,
    shapes, enrollment, storage, tiers/spend, claim codes, two-phase
    archive); Milestone 1 reworded to retrieval only.
  - system.md §2.1 ER: courses gained lifecycle_status/archived_at/
    purge_after; enrollments gained responded_at; per-shape semantics
    spelled out. §2.2 SOURCES gained size_bytes + stored_encoding with the
    streaming-upload/conditional-gzip rules.
  - system.md §9: support-code redemption flow documented (transactional
    redeem, tier single-sourced).
  - superseded content removed: "sessions" (JWT), "private or public"
    two-shape wording, single-model inference framing, pre-017 code format.
- **[docs] Decisions ledger now:** 001 course access, 002 auth boundary +
  ingestion runs, 003 public enrollment + personal artifacts, 004 tiers +
  spend, 005 sharing/archives/support codes, 006 three course shapes.

## Self-enroll UX signal for invite-only courses (2026-09-05)

- **[api] `self_enroll` now differentiates the two non-public shapes:**
  `private` → 404 (course existence never revealed), `invite_only` → 409 with
  detail "course is not self-enrollable; invite-only courses require a join
  code". The 409 is the machine-readable hook for the frontend's
  "invite-only — enter a join code?" prompt. Pinned in
  `tests/test_courses_api.py::test_self_enroll_invite_only_course_signals_join_code`.
- **[design] Where the redirect UX lives — split:**
  - **Backend (done):** return a distinct, documented status + message per
    shape. The API stays a JSON API; it never "renders a page" or decides UI
    flow. 409-vs-404 carries the distinction without leaking course
    existence to strangers.
  - **Frontend (todo, no backend change needed):** on 409 from `/enroll`,
    render the join-code entry view (the `/courses/join` endpoint already
    exists). Private-course 404s and generic 404s render a plain
    not-found/no-access state.
  - **Convention for future endpoints:** distinguishable, user-actionable
    failures get a specific status + stable detail string; the frontend maps
    (status, detail) to UI states. If these grow, consider a typed error
    body (`error_code` field) instead of string matching — noted as a
    possible refactor once there are several such cases.

## Policy decisions from the business-logic review (2026-09-05)

Decisions made during the human review of business-logic-heavy files; all
implemented and pinned by tests this session.

- **Copy-any-time (was copy-once).** An archive participant may copy their
  archive as many times as they like during the 90-day grace period.
  Removed `ArchiveAlreadyCopiedError`, the `mark_copied` query, and the
  `copied_course_id` column (migration `018_copy_any_time.sql` drops it).
  Tier limits (courses/storage) remain the natural brake. Pinned by
  `test_archive_can_be_copied_repeatedly_during_grace`.
- **Nothing survives purge.** The memory bank is written at archive time as a
  distilled record for the grace window, but is destroyed at purge with
  everything else: `course_deletion.sql` now deletes `course_memories`.
  Golden rule 6 (distilled record) applies to the grace window, not beyond.
  Pinned by `test_purge_removes_distilled_memory_bank_entries`.
- **Re-enrollment openness confirmed.** Revoked/declined learners can always
  re-enroll in public courses (and re-join by code / re-accept invites on
  invite-only); declined-then-rejoin is deliberate misclick protection. No
  change.
- **Join-code visibility confirmed.** On invite_only courses only the owner
  sees the code; learners cannot share access. No change.
- **Members list no longer exposes learner emails.** `MemberView` dropped
  `email`; the owner gets ids + status only and identifies members
  out-of-band. Closes the OPEN item in the earlier API-tightening note.
- **90-day grace is a set knob** (`configs/lifecycle.toml`), not to be
  tuned casually. The restrict flow (public → restricted archives the
  original so learners keep copy access; owner gets a clean successor)
  stays as designed.
- **Hardening from the same review:** `sweep_storage_orphans` now *requires*
  `min_age` (keyword-only, no default) — the guard that protects in-flight
  transactions' files can no longer be silently disabled by a bare call.
  The cross-connection lock ordering in `premium_codes_repo.redeem` is
  documented as load-bearing (do not "optimize away" the unused
  `fetchone()` on the user row lock).

## Known-gaps closure from the audit (2026-09-05)

- **Per-course purge isolation.** `purge_expired_archives` now purges each
  due archive in its own transaction (`_purge_one`): one course's failure
  rolls back only that course and is logged + skipped; the rest of the queue
  proceeds. A failing course stays archived with `purge_after` in the past,
  so every sweep retries it — a persistently failing purge shows up as a
  repeated error log, which is the operator signal. The old all-or-nothing
  transaction is gone; `test_purge_rolls_back_archive_and_cleanup_job_on_failure`
  updated to the new contract (failed purge returns [] and leaves everything
  intact), and `test_purge_failure_is_isolated_per_course` pins isolation +
  retry.
- **Mechanical FK-coverage guard.**
  `test_purge_block_covers_every_fk_referencing_table` derives, from
  `pg_constraint`, every table that can reference the course subtree via a
  foreign key and asserts the purge block mentions it. A future migration
  adding a referencing table without a purge statement now fails in CI
  instead of wedging production purges. (course_memories,
  course_archive_access, storage_cleanup_jobs are the documented
  exclusions/indirect handles.)
- **Copy/restrict failure branches now tested.** Three new tests pin:
  mid-copy OSError → successor rolled back, partial directory removed,
  archive stays copyable (retry succeeds); mid-restrict OSError → original
  stays public/active, partial directory removed, retry succeeds;
  uncopyable-object guard fires before any mutation in restrict, leaving
  the course public and active.
- **Test hygiene learned:** `monkeypatch.undo()` reverts autouse fixtures
  too (breaks the storage sandbox); restore specific targets with
  `monkeypatch.setattr(..., original)` instead.

## Remaining audit fixes (2026-09-05)

- **Redeem's double-redeem check is now structurally same-connection.** New
  `spend_repo.active_subscription_on(conn, user_id)` runs on the caller's
  transaction; `premium_codes_repo.redeem` uses it. The old cross-connection
  read was correct only via an implicit lock-ordering invariant; the
  invariant is now expressed in the API surface (the docstring on the
  variant says money-path checks must use it). Pinned by
  `test_concurrent_double_redeem_admits_only_one` (8/8 runs: exactly one
  winner, one subscription).
- **`rotate_support_code` race closed.** Concurrent rotations could leave
  two open codes (READ COMMITTED lets the second revoke miss the first's
  inserted replacement). The user-row `FOR UPDATE` now serializes rotations.
- **017's data rewrite is regression-tested.**
  `test_017_rewrites_legacy_codes_and_check_rejects_old_format` applies the
  migration chain to a scratch schema, injects a 015-era 24-hex code after
  015, and verifies the 017 DO block rewrites it into the canonical alphabet
  and the `courses_join_code_known` CHECK rejects the legacy form by name.

## Memory as a user-owned subsystem (2026-09-05)

- **Decision: memory is the user's, not the course's.** The memory bank is a
  first-class subsystem, not a deletion byproduct. Course deletion (and its
  90-day archive) destroys the course's materials and archive; the user's
  distilled memory of the course persists past purge. This supersedes the
  earlier "nothing survives purge" decision for course_memories only —
  citation_snapshots and everything else still dies at purge. Golden rule 6
  is intact: the distilled record IS the retention, it just lives forever in
  the user's bank instead of expiring with the archive.
- **Implementation state:** course_memories was already FK-less by design
  (migration 002: "course_id is stored without a hard FK so the memory
  outlives the course") — the purge deletion added earlier is now removed;
  course_deletion.sql no longer touches course_memories. The
  FK-coverage guard test excludes it explicitly with this decision as the
  documented reason. The read path stays user-scoped (list_memories).
- **Next:** build the memory subsystem out — accumulation during course life
  (M2 extraction pipeline), not just archive-time snapshots. The
  archive-time write remains as the final snapshot before materials vanish.

## Edge-case review fixes (2026-09-10)

Adversarial "how do users break this" pass over lifecycle/storage/auth.
Five intentional design choices were moved to Open (above) rather than
"fixed"; eight real logic gaps were fixed and pinned:

- **[bugfix] Public→restricted PATCH 500.** `update_course` in
  `api/courses.py` didn't map `UncopyableCourseError` (the archive-copy
  endpoint did); a public course with file-backed non-source objects
  returned 500. Now 409, same as the copy path.
- **[bugfix] Memory-bank evidence could be truncated away.** The old
  assembler appended evidence last and prefix-truncated, so verbose
  concepts could remove every grounding line — violating the ratified
  "permanent memory must remain inspectably grounded" rule. `memory_bank`
  rewritten: evidence index reserved first (compact filename+locator+hash
  lines; excerpts only when budget allows), semantic content fills the
  remainder, `key_concepts` bounded so the whole retained record fits the
  budget, material assembled once per tier per refresh. Pinned by
  `test_memory_summary_keeps_evidence_within_tight_budget` (40 long
  concepts, 3 sources, free tier: summary within budget, ≥1 evidence
  entry with uncut sha256, concepts survive).
- **[bugfix] Copy was an inconsistent half-copy.** It copied derived
  concepts/dependencies without their evidence graph (ungrounded derived
  memory) and queued no ingestion. Now: raw sources, study periods, and
  owner-authored objects copy; derived memory does not (ingestion
  re-derives it); every copied source enqueues in new `pending_ingestion`
  (migration 019, with backfill of existing copied sources +
  `ingestion_history` audit table). The purge block and FK-coverage guard
  updated. Pinned by `test_copy_requeues_ingestion_and_skips_derived_memory`.
- **[bugfix] Dead cleanup jobs were silent + double reclaim.** Terminal
  `dead` transitions now emit an ERROR log with job_id, course_id, attempt
  count, and final error (both the failure path and lease-reclaim path);
  `cleanup_failed` returns the updated row. Lease reclamation has a single
  owner: `run_once()` — `process_cleanup_jobs()` no longer reclaims
  (double reclaim in one pass could dead-end a job on stale counts).
  Pinned by `test_dead_cleanup_job_logs_operator_signal` and the updated
  reclaim test.
- **[bugfix] Accept-invitation could race archival.** The policy trigger
  read the courses row with a plain SELECT; archival could commit between
  the trigger's read and the enrollment's commit, leaving an 'active'
  learner on an archived course with no archive access. Migration 020
  takes `FOR SHARE` on the courses row in the trigger — it conflicts with
  `delete_course`'s `FOR UPDATE`, making check and transition atomic.
  Pinned by `test_accept_invitation_cannot_race_course_archival`
  (two-connection: NOWAIT proves the lock conflict; complementary case
  proves post-archival accept fails the policy check).
- **[bugfix] Purge claim was decorative.** `due_archives` took
  `FOR UPDATE SKIP LOCKED` and then closed the connection before purging,
  releasing every lock — two maintenance workers could both "claim" the
  same archive. The claim moved inside `_purge_one`'s transaction
  (`claim_due_archive`); the listing is lock-free. Idempotent deletes +
  the cleanup-job unique index remain the backstop.
- **[bugfix] FK-coverage guard was vacuous.** Its `regclass::text LIKE
  'public.%'` filter matched 0 of 68 FKs (search-path tables render bare
  names) — the test had never guarded anything, which is how migration
  019's `ingestion_history` initially slipped past the purge block.
  Rewritten: transitive FK closure from `courses` restricted to
  NO ACTION/RESTRICT edges (the ones that wedge purges), excluding
  cascade/set-null edges and documented exclusions.
- **[bugfix] Empty-dir sweep bypassed the grace.** `all()` over zero files
  is vacuously true, so a freshly mkdir'd in-flight copy target could be
  rmtree'd immediately. The directory's own mtime must now be past grace.
- **[test-infra] conftest hygiene.** `DELETE FROM storage_cleanup_jobs` was
  unconditional — a test run against a shared environment destroyed pending
  (real) purge jobs. Now scoped to test-owned courses plus orphaned
  course rows, and the suite refuses to run against a database whose name
  doesn't contain "test" unless `PYTEST_ALLOW_ANY_DB=1` is set
  (deliberate opt-in; set in the local dev `.env`).
- Verified-clean in the same pass (no action): path traversal, zip-bomb
  storage streaming, upload quota serialization, join-code brute force,
  redeem double-spend, login enumeration, JWT/bcrypt bounds, support-code
  plaintext handling.
- Gate: full pytest suite green, ruff clean, mypy clean.

## Memory model ratified + vocabulary pinned (2026-09-10)

The repeated "agents keep building the memory layer wrong" problem was
diagnosed: the docs used "memory" for four different things
(course-memory node, course knowledge/TOC, student data, chat history),
the deletion diagram literally commanded "write each participant's
course memory," and the table storing the per-user node is named
`course_memories` while the course-knowledge models live in
`schemas/memory.py` — so every session re-derived its own interpretation.

Operator ratification (decision `docs/decisions/007_memory_model.md`):

- **User memory (root)** — per-user, lifelong, behavioral ("teach THIS
  person with visuals/analogies") + cross-course history; the only layer
  that may change model behavior; cross-course emphasis (Calc 1 integrals
  struggle → Calc 2 emphasis) happens at the root at prompt time.
- **Course memory (child)** — per-user per-course FOCUS node ("what THIS
  person struggles with in THIS course"); facts about understanding,
  never behavior instructions, never shared. **Stored ONLY for the main
  user of the course (its owner)** — per-learner nodes were rejected as
  too complex; learners get the harness plus their own raw private
  student data.
- **Course knowledge / TOC** — what the course SAYS and the index for
  FINDING it; shared per-course state; explicitly not memory.

Implemented this session:

- Decision doc 007 written; `AGENTS.md` gained a mandatory vocabulary
  block ("read before touching anything named memory") that maps each
  term to code homes and names the single write seam.
- `common/memory_bank.py` → `common/course_memory.py` (git mv, history
  kept). `upsert_for_users` folded into a private `_write_node`;
  `refresh_for_owner(cur, course_id)` is now the ONLY public write seam —
  it derives the owner from the course row, so no caller can ever pass
  another user's id. All five call sites (archive, restrict-successor,
  archive copy, course create, upload/rename) route through it.
- API route `GET /memory-bank` → `GET /course-memories` (read-only).
- Doc scrub: system.md §0 TL;DR, §2.3 heading ("Course knowledge"),
  §2.7 deletion diagram + COURSE_MEMORIES bullet now owner-only; the
  "each participant's" line that taught the prior bug is gone.
  project.md one-liner, principles, deletion, Milestone 2 (now "User +
  course memory" per the tree model), pipeline stage name
  (course-knowledge extraction).
- `schemas/memory.py` and `src/backend/memory/__init__.py` now carry
  docstrings flagging their names as legacy misnomers pointing at
  decision 007. `MemoryObject`'s docstring: a course-knowledge note, not
  memory.
- Tests renamed accordingly (`memory_bank` → `course_memory` imports,
  `/memory-bank` → `/course-memories` calls). No behavior change: the
  owner-only semantics were fixed in the previous pass; this pass makes
  the vocabulary and seams unable to teach the wrong thing.

## Whole-repo remnant sweep after the memory-model ratification (2026-09-10)

Second pass over every file for stale remnants the rename missed. Fixed:

- `courses_lifecycle.delete_course` docstring (still said "every current
  participant's memory bank") → owner's course-memory node.
- Decision 003 (learner artifact retention exception) and 005 ("every
  current participant receives a user-owned course-memory record" + the
  stale copy-once wording) amended to the decision-007 model: owner-only
  node, copy-any-time, learners get archive access not memory.
- Generation-task vocabulary renamed end to end — before Milestone 1
  wires real model calls, so no persisted ledger rows carry the old
  string: `EXTRACT_MEMORY` → `EXTRACT_KNOWLEDGE` (stage enum),
  `extract_memory` → `extract_knowledge` (ingestion.toml stage),
  `memory_extraction` → `course_knowledge_extraction`
  (KNOWN_GENERATION_TASKS + tiers.toml v6 routing keys; loader enforces
  task coverage so the rename is validated at load time). system.md §3
  pipeline text, §6a task list/charging, and decisions 002/004 updated.
- system.md analogies/diagrams: "memory store" → knowledge store,
  "Course memory — study guide" → course knowledge, tutor-analogy wording,
  data-flow line now names the memory tree explicitly (root + course node
  + knowledge + student model).
- §7 tutor-presentation section reframed: presentation is a user-memory
  (root) concern; behavior instructions live only in the root, never in
  course memory or knowledge (decision 007 invariant 2). project.md
  matching paragraph updated.
- Legacy-naming notes added where applied tables keep old names:
  system.md §2.3 bullet for `memory_objects`/`memory_id`/
  `MEMORY_OBJECT_EVIDENCE` (course-knowledge objects), and schema
  docstrings in evidence.py (Citation, MemoryObjectEvidence).
- enrollments.py router docstring: email-visibility line superseded by
  the members-list ruling; memory-content note added.
- Test names/docstrings renamed (`test_memory_bank_*` →
  `test_course_memory_*`); notes.md history untouched (append-only).
- Applied-migration comments still contain old wording (015/016/018) —
  uneditable by the append-only rule; cosmetic only, mapped by decision
  007. Same class as the recorded 013/018 cosmetic notes.
Gate: 193 tests, ruff, mypy, git diff --check all green.

## Storage accounting + ingestion spend ratified and implemented (2026-09-11)

Operator decisions from the storage conversation, now in code:

1. **Quota = stored bytes, ratified.** A file's quota cost is its
   compressed stored size (post-gzip). "Fairness" across compressibility
   was explicitly deprioritized in favor of a consistent, inspectable
   allocation number per user. No change to size_bytes semantics; no
   migration for existing rows.
2. **Decompression is a read-time safety cap, not a quota.** New
   `configs/lifecycle.toml` `[decompression] max_decompressed_bytes`
   (200 MB; config version 3). `storage.read_stored` now streams gzip in
   chunks and raises `DecompressionLimitExceededError` when expansion
   exceeds the cap — it no longer decompresses whole-buffer into memory.
   Identity files are size-checked against the cap before read. The
   mandatory keyword-only cap parameter makes the unbounded read
   impossible to reintroduce by accident. Note: our own gzip cannot
   expand past the raw-body ceiling (we compressed what the ceiling
   already capped); the cap exists for stored files whose *content* is a
   compressed container and for defense in depth.
3. **Ingestion gets its own weekly token pool.** `generation_ledger`
   gained `spend_kind` ('generation' | 'ingestion', migration 022,
   historical rows default to generation). `spend_repo.weekly_spend` and
   `budget.check_budget` take the pool; `TierPolicy.ingestion_token_budget`
   added (tiers.toml v7, equal to the generation budget for now — the
   pool is a guardrail, not a ration). An upload's model calls can never
   drain the budget a user needs for interactive answers.
4. **Ingestion tasks route to the cheap model.** tiers.toml now routes
   `course_knowledge_extraction` to `small-stable-toc` alongside
   `toc_update` (both tiers). Reasoning-off for ingestion is a provider
   call-parameter concern, deferred to Milestone 1 wiring when a real
   provider client exists; the routing seam is in place. Extraction
   quality is still gated by the eval-set rule: cheap is the default,
   upgrade only on a documented baseline failure.
5. **Free-tier provider check still open.** Any cheap/free ingestion
   provider must pass the same no-retention verification as the main
   provider before use (golden rule 4). Blocked on picking the provider.

Gate: 198 tests, ruff, mypy, git diff --check all green.

## Review fixes on the storage/spend first pass (2026-09-11, same day)

Operator-ratified review found real issues; all fixed before commit:

- **Cap raised to cover the paid raw ceiling (1 GB).** The first-pass
  200 MB cap would have made large legitimate paid uploads unreadable
  (quota passes at stored bytes, read raises). New invariant, pinned by
  test: `max_decompressed_bytes >= max(max_raw_upload_bytes)` across
  tiers. Config comment documents the derivation.
- **No false fallback on spend_kind.** `check_budget`, `weekly_spend`,
  `record_generation` now take `spend_kind` as a required keyword — no
  production callers exist, so the cheapest moment to make the wrong-pool
  mistake unrepresentable is now. `record_generation` rejects raw strings
  with a clean ValueError instead of a psycopg CheckViolation;
  `GenerationLedgerEntry.spend_kind` validates as the SpendKind enum.
- **Loader strictness.** `lifecycle_config` reads
  `[decompression] max_decompressed_bytes` via required keys (fails
  loudly), dropping the silent hardcoded default; test pins it.
- **Error mapping.** gzip branch maps only EOFError/BadGzipFile to
  "corrupt stream"; FileNotFoundError/permissions propagate as-is,
  matching the identity branch.
- **Tests resolve the cap via load_lifecycle_policy** instead of
  hardcoding, exercising the config→caller resolution path the seam
  mandates.
- Not adopted (deliberate): whole-file buffering in read_stored stays for
  now (cap-bounded, fine at current scale); the Milestone 1 parser seam
  should take a stream. DB CHECK on spend_kind accepted (mild tension
  with free-string convention — spend kinds are billing semantics, not
  extensible content types).

Gate: 201 tests, ruff, mypy, git diff --check all green.

## Milestone 1 phase 1: ingestion pipeline wired end to end (2026-09-11)

The M1 ingestion skeleton is now real code against the real DB:

- **New modules** (`src/backend/ingest/`): `extract.py` (text + locators:
  PDF pages via pypdf, markdown sections, plain-text line ranges),
  `chunking.py` (token-bounded chunks, ~4 chars/token, paragraph→
  sentence→whitespace→hard-cut boundaries, chunks map to overlapping
  locators), `runs.py` (DB run ledger: run + stage rows, claims via
  FOR UPDATE SKIP LOCKED), `orchestrator.py` (binds executor + handlers
  + ledger into `run_ingestion`).
- **Provider seam** (`common/provider.py`): the single model-call
  function (task+prompt in, result out). Routing, billing-pool checks
  (ingestion tasks must bill the ingestion pool), and ledger recording
  are wired; the HTTP call is a deliberate `ProviderUnavailableError`
  until the operator picks a provider and verifies no-retention. Only
  this file changes when a real client lands.
- **Migration 023**: `sources.extracted_text` (retrieval reads text, not
  binaries; raw files stay untouched on disk).
- **Commit discipline** (the interesting design outcome): the run row
  commits at creation, each succeeded stage transition commits with its
  output, and a failed run commits its audit trail in a fresh
  transaction after rollback. Consequence: completed stage output
  survives a later stage's failure — a retry does not redo committed
  stages; partial derived rows from the failing stage roll back.
  Inspectability is preserved: run/stage/error messages are always
  queryable, including the provider error inside the failed stage row.
- **Queue**: `queue_upload` hands uploaded sources to
  `pending_ingestion`; `claim_pending_sources` is the SKIP LOCKED worker
  seam (no worker loop yet — that is the next M1 step, with the API
  handoff).
- **Model stages** (`update_toc`, `extract_knowledge`): prompts built,
  routed through the provider seam, billed to the ingestion pool. They
  currently fail runs (no provider), which the tests assert as the
  honest current state; swapping in a real provider turns the same
  pipeline fully green without touching orchestrator code.
- pypdf added as a dependency (PDF page extraction); `pyproject.toml`
  updated.

Gate: 222 tests (21 new), ruff, mypy, git diff --check all green.
Not committed yet — review first.

## M1 ingestion review: 21 findings, all fixed before commit (2026-09-11)

External review of the ingestion batch found 21 issues (3 correctness,
several convention violations, several smaller). All fixed:

- **Citation alignment (golden rule 1)**: PDF locators were built without
  the join separator, so every page after the first drifted N-1 chars —
  page 50 cited 49 chars early. Locators and text now share one join
  (`_join_pages`/`_pdf_locators` built from the same page_texts), with a
  regression test asserting each page span slices out exactly its own
  page. Markdown heading scan now masks code fences with equal-length
  spaces (offsets preserved, in-fence `#` no longer a heading).
- **Money wiring was dead**: nothing called check_budget or recorded
  spend; generate took caller-supplied token counts (inverted). The
  provider seam now gates (check_budget), calls, and records inside one
  function; token counts come from `_call_provider`'s return, never the
  caller. Tier is resolved+verified inside the seam (no caller-supplied
  policy). Pool routing is symmetric and lives in schemas/base.py
  (INGESTION_TASKS) — the single source of truth. A stubbed-provider test
  proves a full pipeline run goes green AND writes ingestion-pool ledger
  rows with nonzero tokens; a drained-pool test proves the gate blocks
  before any provider call.
- **Failed sources were a dead end**: mark_source_failed filtered on
  status='uploaded' and nothing cleared queue rows — failures were
  permanently unretryable with zombie queue rows. Failure now clears the
  queue row; `requeue_failed_source` is the deliberate failed→uploaded
  retry path; claims require status='uploaded' AND claimed_at IS NULL.
- **The SKIP LOCKED claim couldn't work**: run_ingestion's first commit
  released the row lock mid-claim, letting a second worker double-claim.
  Claims are now a persistent state transition (claimed_at + claimed_runs,
  migration 024), not a lock. The IN-subquery silently dropped both LIMIT
  and SKIP LOCKED; rewritten as CTE.
- **False-success guard**: model stages fail on empty provider output
  (EmptyModelError) — a provider landing cannot silently produce
  SUCCEEDED runs with an empty knowledge base. The stub still doesn't
  write TOC/knowledge rows; that lands with the real provider output
  format (honest state, tests assert it).
- **Dispatch trapdoor closed**: unsupported mimes (zip/png/mp4) raise
  UnsupportedSourceTypeError at dispatch instead of raw
  UnicodeDecodeError after prior stages committed. Whitelist: PDF, plain
  text, markdown, csv, json, xml, yaml. Scanned/image-only PDFs raise
  EmptyExtractionError instead of "succeeding" with nothing retrievable.
- **BOM + cp1252**: utf-8-sig decode (Windows BOM no longer breaks the
  first heading), cp1252 fallback for legacy lecture notes.
- **extracted_text column dropped** (migration 024): it duplicated
  chunks in a Postgres row value approaching the 1 GB ceiling. Text
  lives in chunks only; raw stays on disk.
- **Honest docs**: orchestrator docstring now says retries re-execute
  from the top (safe, not free); the earlier notes.md claim "billed to
  the ingestion pool" was false at the time and is now true; "only this
  file changes when a provider lands" corrected — `_call_provider` +
  `_parse_usage` plus the row-writing of real output change.
- **Tunables in config** (ingestion.toml v2): chunk_max_tokens=512,
  prompt_window_chars=8000 (placeholder window acknowledged — TOC stage
  is structurally blind past it until the provider formats real output).
- **Convention sweep**: all raw SQL moved to queries/ingestion.sql
  (source_row, deletes, requeue_row, enqueue_pending); queue_upload's
  query no longer lives in course_archives.sql; failure audit typed
  against IngestionPipelineError (was duck-typed Any); source facts
  fetched once via fetch_source_row and passed through the constructor
  (no lazy DB reads, no private reach-ins, no -O-silenced asserts).
- Prompt-injection acknowledged in the orchestrator docstring: uploaded
  text is inlined into shared-course prompts unescaped; input marking is
  a provider-landing requirement, part of the go-live gate.

Design decision for the worker (not built yet): claims are persistent
(claimed_at), so the future worker marks claimed_at and requeues stale
claims (claimed_runs counter is the watchdog input). This was the design
space the review's #4 demanded — it now exists.

Gate: 233 tests, ruff, mypy, git diff --check all green.

## Open: retrieval + generation design — flagged for dedicated conversations (2026-09-11)

Generation and retrieval are the highest-ambiguity areas of the project
(structure, scope, and product behavior all genuinely open). Operator
wants to break each down properly in its own conversation rather than
decide inline. Each item below gets its own revisit; nothing here is
final.

**Fork A — retrieval mechanism.** History: the original plan was
embeddings + vector DB; operator rejected it on two grounds — (1) with
lots of similar course text, embedding search returns many near-duplicate
hits (precision worry at the top of the ranking); (2) the cascading-TOC
idea fit the domain better. Current ratified context (do not re-litigate
in the revisit): NO embeddings in the baseline (decision 001-era rule:
retrieval replaces embedding similarity; models earn their role on a
documented baseline failure). Candidate order when revisited:
tsvector keyword baseline first, then TOC-guided routing as a measured
upgrade that must beat the baseline on the eval set before becoming
default.

**Fork B — behavior when retrieval finds nothing relevant.** Options on
the table: strict refusal ("this isn't in your materials"), ungrounded
answer clearly flagged, or an explicit opt-in toggle. Operator's product
philosophy call; default lean recorded so far is strict refusal. Not
final — this is a trust/product-positioning decision.

**Fork C — answer shape and citation contract.** Working direction: every
factual claim carries an inline locator citation; the retrieval trace
(chunks, scores, prompt) stored on every answer. Detail level of
citation rendering, per-claim vs per-answer granularity, and how
"unsupported claim" is rendered are open.

**Fork D — first-ship scope.** Working direction: single-turn grounded
Q&A first; conversation threads and follow-up memory not load-bearing
until the basics are solid. Open.

**Fork E — TOC's role at query time.** TOC is written at ingestion
(update_toc stage) but is not load-bearing for retrieval until the
TOC-guided routing beats the keyword baseline on the eval set (golden
rule 5). The comparison is the deliverable; "smarter" is not a
justification by itself.

**Also open, related**: eval question set for retrieval+answer quality
(hand-written from real uploads, committed to data/eval/); the "did the
chunk actually support the claim" audit loop; conversation storage scope
(schema exists, not load-bearing).

Context for the revisit: the ingestion side is built and aligned
(233 tests green, locators are citation-grade after the drift fix);
retrieval reads chunks+locators directly, so nothing blocks Fork A work
except the conversation itself.

## Ratified: Fork A — hybrid three-layer retrieval (2026-09-12)

Operator decision after the Fork A conversation. Supersedes the "TOC alone,
embeddings only on failure" posture; the near-duplicate-precision objection
to embeddings is answered by the fusion layer (candidate merging + ranking
caps), not by excluding embeddings.

**All three layers run; every layer is a candidate generator, not an
authority:**

1. **TOC routing** — question → TOC entries (titles, descriptions, concept
   refs) → chunks under those entries. Coarse, canonical location. Query-time
   mechanism (static match vs model-routed) is still open — see below.
2. **Keyword (tsvector)** — term overlap over chunks. Catches scattered
   mentions the TOC misses ("when did we USE linearity" → chapters 5–6, not
   just the chapter 2 definition).
3. **Embeddings (semantic)** — vector similarity over chunks for paraphrase
   cases. Stored at ingestion by a separate embedding model; query time is
   vector-only.

**Fusion + citation contract = the harness:** all three layers emit chunk
candidates; fusion merges and ranks; every surviving chunk carries its
locator. Citations are layer-agnostic — the same contract regardless of
which layer surfaced the chunk. Retrieval strategy is an internal, swappable
detail; trust lives at the citation boundary. The operator's near-duplicate
concern (many similar hits from similar course text) is handled as a ranking
problem at fusion (per-source/per-chapter candidate caps), not by excluding
embeddings.

**Consequences:**
- Decision record needed (docs/decisions/008) amending the no-embeddings
  rule: embeddings are now an always-on third signal in a fusion, not a
  fallback-for-TOC-failure. Cross-model alignment concern is superseded by
  the fusion design; a separate embedding model is still subject to the
  no-retention provider check.
- Eval set is now load-bearing: recall@k per layer AND fused; fusion must
  beat the best single layer on the eval or it loses (golden rule 5 applies
  to combinations). Eval questions + marked answer chunks go to data/eval/.
- Build order implication: TOC + keyword are buildable now (no model call at
  query time, no new provider); embeddings wait for the provider pick +
  migration (embedding column/vector index). Ship order: keyword → TOC
  static → fusion → model-routed → embeddings, each measured against the
  previous.

**Still open within Fork A:**
- Query-time TOC matching: static (entries + synonyms, no model call) vs
  model-routed per query. Leaning static-first, model upgrade measured.
- Embedding model choice + storage (pgvector) — gated on provider pick.
- Fusion ranking policy (weights, caps, dedup) — tunables in configs/, not
  hardcoded.

## Fork A implemented: the retrieval funnel is real code (2026-09-13)

Decision 008 revised after the design conversation (fusion = mixing not
scoring; seams are built up front and activate as their data arrives —
"build the seam whenever, turn it on when the data's real"). Then
implemented:

- **`src/backend/retrieval/`**: `funnel.py` (four seams + fusion),
  `config.py` + `configs/retrieval.toml` v1 (all funnel tunables:
  per-seam limits, final_k, per-source cap, embedding-only quota),
  `trace.py` (per-query trace with layer attribution), `evals.py`
  (recall@k eval runner; cases in `data/eval/retrieval/cases.json`,
  matched by locator label so cases survive re-ingestion).
- **Migration 025**: `chunk_embeddings` (float8[] now, pgvector swap is a
  later migration) + `chunks.search_vector` generated tsvector with GIN
  index.
- **Fusion is mixing, not scoring** — exactly as ratified: keyword/TOC/
  dependency/embedding candidates union with layer attribution
  (multi-layer hits merge layers, max rank), ordering is embedding rank
  when present else keyword rank, per-source cap bounds the near-duplicate
  flood, embedding-only candidates enter through an explicit quota, final
  cut to final_k. No weighted score arithmetic anywhere.
- **Dormant seams verified by tests**: no TOC entries / no dependency
  edges / no embeddings → empty candidates, no errors, no contribution.
- **Trace**: `retrieval_traces.retrieved_chunk_ids` jsonb now carries the
  full audit payload (ordered chunk ids + layer_contribution +
  matched_concept_ids).

Bugs caught by my own tests during the build (for the reviewer's
attention): (1) tsvector AND semantics missed partial-query chunks — the
keyword seam uses OR now; (2) Postgres has no float8[]*float8[] operator —
dot products computed via unnest-zip SUM; (3) the fusion merge loop
originally omitted the embeddings dict — embedding-only candidates could
never enter (the quota was dead code); caught by the quota test; (4) the
embedding seam read a `rank` column that the query named `dot` — ranks
were silently re-derived from row order; (5) psycopg's client-side parser
trips on literal `%` inside SQL comments — wildcard comments removed.

Gate: 246 tests (13 retrieval), ruff, mypy green.

## Retrieval-batch review: 16 findings, all fixed (2026-09-13)

External review (with live DB probes) of the funnel batch. All findings
fixed before commit:

- **Keyword crash on math syntax (high)**: to_tsquery treats ( ) < > !
  & | : * as operators; "f(x) = x^2" killed retrieve() with a SyntaxError.
  The seam now tokenizes with a strict [a-z0-9]+ regex BEFORE any SQL —
  operator characters cannot reach the parser. Single-char tokens dropped.
  Regression tests: math queries and a hostile-query battery (unbalanced
  parens, <-> operators, empty strings).
- **Eval harness false-pass modes (high)**: substring label matching made
  "page 1" hit "page 12"; any() contradicted the docstring's all-expected
  rule; the decision-008 kill-switch metric (recall per seam AND fused)
  was not computed; unresolved course tags silently passed. The runner is
  rewritten: exact label set membership, ALL expected labels required,
  per-seam recall AND fused recall computed, unresolved cases reported
  and excluded (never a pass), and an EvalSummary that answers the
  decision rule directly ("fusion beats best single: yes/no"). Two
  eval-runner tests pin the exact-label and unresolved semantics.
- **Embedding dimension mismatch (high)**: unnest zips unequal arrays by
  silent truncation — a model swap would rank garbage without error.
  cardinality() equality guard added in SQL; test pins it.
- **Trace lost the WHY (high)**: per-chunk layer attribution was dropped
  at record_trace despite Candidate.layers holding it; toc_entry_ids was
  dead (never populated). Traces now store per_chunk_layers (each cited
  chunk with the seams that surfaced it); toc_seam returns matched entry
  ids and retrieve() passes them through to the trace. Test asserts the
  payload shape.
- **TOC seam raw-substring ILIKE (medium)**: %word% with no boundaries
  matched "we" → "Week"/"power"/"answer"; user wildcards unescaped. The
  seam now full-text matches (to_tsvector on title+description, stemmed,
  stopword-filtered — consistent with the keyword seam); locator_id IS
  NULL entries excluded explicitly (they have no chunks).
- **Single-letter concept matches (medium)**: position() substring match
  made concepts named f/R/T match nearly every query. Word-boundary
  regex matching + 2-char minimum (enforced in SQL and conceptually in
  the tokenizer). Test pins "rat" not matching "iteration".
- **Dependency seam precision + nondeterminism (medium)**: OR-join
  defeated both indexes and SELECT DISTINCT...LIMIT without ORDER BY made
  runs irreproducible. Rewritten as UNION (index-friendly per branch) +
  deterministic ORDER BY. KNOWN GAP documented in SQL: memory_objects
  links concepts to sources not locators, so the seam is coarse until a
  locator link migration exists — flagged as a schema TODO, not hidden.
- **course_by_tag nondeterminism (medium)**: exact-match-first + stable
  secondary ordering. (Also discovered the hard way: psycopg named-param
  mode requires literal % escaped as %% inside query text, and courses has
  no created_at column.)
- **Smaller**: eval embedding model name now a parameter (real model name
  from config when a provider exists); migration 025 comment corrected
  (absence = no row, never NULL); rank unit-mixing documented on
  Candidate (seam-local, never compared cross-seam); prereq_id index
  (migration 026 — 025 was already applied, so it got its own file);
  trailing newlines restored; system.md §7 glued sentence reattached to
  the TOC bullet correctly; per-source-cap test's inline SQL shared via
  fixture helper where practical.

Two psycopg client-side parser gotchas worth remembering (recorded here
so the next batch doesn't rediscover them): literal % must be %% in
named-param SQL INCLUDING comments adjacent to placeholders, and jsonb
columns return parsed dicts, not strings.

Gate: 255 tests (22 retrieval, +9 review regressions), ruff, mypy green.

## M1 close-out: ingestion worker + upload handoff (2026-09-13)

The ingestion loop is now closed end to end: upload → queue → worker →
pipeline → terminal state, all inspectable.

- **Worker** (`src/backend/ingest/worker.py`): `process_batch` claims
  bounded batches via the persistent-claim seam, resolves the course
  owner + account (tier verified inside the provider seam at call time),
  runs `run_ingestion` per source. Pipeline failures are already recorded
  by the run ledger; unexpected errors get a claim-cleanup path so no row
  is stranded. `run_forever` mirrors archive_maintenance's loop shape and
  is wired into the app lifespan (main.py) alongside it.
- **Stale-claim recovery**: rows claimed by a crashed worker become
  re-claimable after STALE_CLAIM_AFTER (30 min, module constant); the
  claimed_runs counter keeps the event visible. Test pins
  claim → age-out → reclaim.
- **Upload handoff**: `sources_repo.upload_source` now enqueues the
  source into pending_ingestion in the SAME transaction as the insert
  (reason: uploaded_new_source) — no window where a source exists but is
  unqueued. The copy seam's enqueue (copied_from_archive:*) was already
  in place.
- **Tests (5)**: upload enqueues; worker batch records failure state
  honestly (provider-less = failed run, no zombie queue row); stubbed
  provider end-to-end goes indexed + queue cleared; requeue-after-failure
  produces a second run; stale claims release.

Honest gap kept visible: the worker runs in-process with the API (small
user base); a separate worker process is the scaling seam — the loop is
already a to_thread pattern so lifting it out is mechanical. Model
stages still fail closed (no provider); the stubbed-provider test proves
the success path works the moment a real client lands.

Gate: 260 tests, ruff, mypy green.

## Review pass on the retrieval + worker batch: small fixes applied (2026-09-16)

End-to-end review of `3eea6d6`. Everything below is fixed; two findings are
left open because they are design calls, not bugs (see the end).

- **Concept-name regex injection (high, was a live crash)**: the dependency
  seam's matcher interpolated model-extracted concept names straight into a
  POSIX regex. Verified against the DB: a name like `f(x`, `a**b` or `x{2,`
  raises InvalidRegularExpression, and because `concept_matches` runs first
  in `retrieve()`, ONE bad extracted name broke every question on that
  course — keyword seam and all. Unescaped metacharacters also matched
  wrongly in both directions: `a+b` matched "aaab", `O(n)` and `f(x)` (the
  common case in a maths/CS course) never matched themselves. Names and
  synonyms now go through `regexp_replace` before they reach the engine.
  Note for next time: Postgres uses `\1` backreferences in the replacement,
  NOT sed/Oracle's `&` — `'\&'` silently substitutes a literal `&` and
  looks like it works. Three regression tests, each confirmed to fail
  against the old SQL.
- **Eval compared seams at k=20 against fusion at k=10 (high)**: per-seam
  recall used each seam's full candidate dict (`keyword_limit` etc.) while
  fused recall used the post-`final_k`, post-cap set. So
  `fusion_beats_best_single` — decision 008's kill switch — was measuring
  the k gap, with fusion handicapped 2:1. Seams are now truncated to
  `final_k` via `_top()` before scoring. Worth remembering: the eval was
  wrong in the direction that would have made us delete a working design.
- **Eval did double the DB work**: `run_eval` computed all four seams and
  then called `funnel.retrieve()`, which re-ran all four plus the concept
  match. It now fuses the seams it already has.
- **Worker blocked shutdown**: `process_batch`'s `while True` drained the
  WHOLE queue, inside `asyncio.to_thread`. Threads are not cancellable, so
  lifespan shutdown returned instantly while the thread kept ingesting —
  unbounded hang on a big backlog. Takes a `should_stop` callback now,
  checked between rounds and between sources; `run_forever` passes
  `stop.is_set`.
- **`claimed_runs` was incrementing into the dark**: both worker docstrings
  called it "the inspectable signal," but nothing read it. Stale-claim
  releases now log a warning with the row count.
- **Smaller**: dead `chunks_by_ids` and `queued_at_for` queries removed
  (added last batch, never called); redundant `locators` join dropped from
  `toc_candidates` (the chunks join already excludes NULL locators);
  `dependency_expansion` branches no longer select `via_concept_id` (it was
  unused, and it defeated the UNION dedupe — a chunk reachable both as
  prereq and dependent burned two LIMIT slots then collapsed to one in
  Python, so the seam returned fewer distinct chunks than its limit);
  migration 027 indexes `chunk_embeddings(model)`, which the embedding seam
  filters on every query; system.md and decision 008's title said
  "three-layer" while the code has four seams.

Left open — design calls, not bugs:

1. **`per_source_cap` punishes the single-source course.** The cap is keyed
   on `source_id` at 4, so a course with one uploaded PDF can never return
   more than 4 chunks no matter what `final_k` says — and one PDF is the
   likely first-user shape. Every fusion test gives each chunk its own
   source, so nothing catches it. Decision 008 says "per-source/per-chapter";
   only per-source exists. Options: scale with source count, floor it at
   `final_k` when the course has one source, or key it on chapter/locator.
2. **Fusion does compare ranks across seams**, despite `Candidate`'s
   docstring promising it never does. `sort_key` has two buckets, and bucket
   1 sorts keyword (`ts_rank`, ~0.06), TOC (hardcoded 0.0) and dependency
   (`-position`) together — so the order is always keyword > TOC >
   dependency, as an accident of unit choice rather than a decision, and the
   config cannot tune it. That also inverts 008's framing, where TOC is the
   scope-giver and keyword the precision net. Related: embeddings only rank
   chunks the embedding seam itself returned, so a keyword hit outside the
   vector top-k sorts below every embedding hit — embeddings are a fifth
   candidate generator that wins ties, not "the relevance ordering over the
   merged set" as 008 describes. Reconcile doc and code before turning
   embeddings on, or the measurement gets read through the wrong claim.

Also still true and worth not forgetting: `retrieve()` and `record_trace()`
have no callers outside `evals.py`, and `retrieve()` takes a `course_id` it
trusts completely — no enrollment check. The seams all filter by course so
retrieval cannot cross courses, but nothing verifies the CALLER may read
that course. That check needs to land with the tutor-flow wiring.

Gate: 263 tests (28 retrieval, +3 regex regressions), ruff, mypy green.

## Full-codebase edge-case sweep (2026-09-16)

Swept all ~5,800 lines of backend source, not just the retrieval batch.
Findings below were probed against the live DB, not reasoned about. Fixed
in this pass unless marked open.

- **Markdown with no headings failed ingestion outright (high)**. Section
  locators came only from `#` headings, so a plain .md of notes produced
  ZERO locators, every chunk mapped to none, and build_chunks' "citation
  grounding is mandatory" check failed the whole source. Plain notes are
  ordinary input. The same gap had a second, quieter half: text before the
  first heading was uncovered, so a long preamble orphaned its chunks, and
  a short preamble sharing a chunk with the first heading was CITED AS
  that heading — a wrong citation, which is worse than a missing one and a
  direct golden-rule-1 problem. Uncovered regions now fall back to the same
  line-range locators plain text uses. Two tests, both confirmed failing
  against the old code.
- **`read_stored` let `zlib.error` escape (medium)**. It caught
  BadGzipFile and EOFError, but a valid gzip header with a corrupt deflate
  body — the likeliest real corruption — raises `zlib.error` and bypassed
  the documented `ValueError("stored gzip stream is corrupt")`. Caught now.
- **NUL bytes survived `sanitize_display_name` (medium)**. Postgres text
  columns reject NUL outright (verified), so an upload whose filename
  carried one became a 500 from inside the insert. The orphan-cleanup path
  did hold — no stranded file — but the upload was wasted and the error was
  wrong. Control characters are stripped now.
- **`consume_token` turned a lost race into a 500 (medium)**. Its
  `assert used is not None` fires when a concurrent request claims the
  token between this transaction's SELECT and its guarded UPDATE.
  Reproduced with two connections: the loser got AssertionError, not
  TokenRejectedError. Double-clicked reset links and mail scanners that
  prefetch URLs make this a routine event, not a rare one. Note this is
  the same class already logged at notes.md ~line 733 ("AssertionError
  500s and vanish under python -O") — one surviving instance, and the only
  one: every other assert in the codebase sits on an INSERT/aggregate
  RETURNING that cannot return no row (`rotate_join_code`, the other
  conditional update, already handles None correctly).
- **Email had no length bound (medium)**. Register/login/reset accepted
  unbounded email strings: 100k chars stored fine, 1M chars produced a raw
  `ProgramLimitExceeded` 500 from the btree index. Bounded to 254.
- **`archive_maintenance.run_once` blocked shutdown** the same way the
  ingestion worker did (non-cancellable `to_thread`). Same `should_stop`
  treatment, checked between phases.
- **`codes._CODE_RE` hardcoded 16** instead of using `CODE_LENGTH`, so
  changing the constant would silently desync the validator. Uses it now.

Open — needs a decision, not a fix:

1. **A password reset does not invalidate existing sessions.** Verified: a
   JWT minted before `update_password` still decodes afterwards, and
   `jwt_expire_minutes` defaults to 7 days. If the reset was prompted by a
   compromise, the attacker keeps access for a week. There is no
   `token_version` / `password_changed_at` to check `iat` against, so this
   needs a column + a check in `current_user`.
2. **`free_tier_overhead()` is never called.** budget.py defines it,
   tiers.toml carries `free_tier_overhead_percent`, `record_generation`
   takes `overhead_tokens`, and test_spend.py tests the function — but
   `provider.generate`, the only place generation spend is recorded, never
   passes it. Free-tier overhead is configured, tested, and inert. Wiring
   it starts charging free users more, so it is a product call.
3. **PDFs never pass through the capped read seam.** PDFs are in
   `_INCOMPRESSIBLE_EXACT`, so they always store as identity, and
   `_pdf_page_texts`' identity branch calls `path.read_bytes()` directly
   instead of `read_stored`. The one file type that always takes that path
   is the one type that skips the decompression ceiling. Upload-time raw
   ceiling still bounds it, so this is a bypassed defense-in-depth seam
   rather than an unbounded read.
4. **`migrate.py` silently skips malformed filenames.** `^(\d{3})_.+\.sql$`
   means `27_x.sql` or `027-x.sql` is ignored with no error, and two files
   sharing a version number mean the second never runs. Both fail silent.

Also noted, not acted on: `get_settings()` re-reads .env from disk on every
call (including once per request through `decode_access_token`), and
`CHARS_PER_TOKEN = 4` under-counts badly for CJK text — both already
documented as approximations.

Gate: 268 tests (+5 this pass), ruff, mypy green.

## Review of the edge-case sweep (2026-09-16)

Reviewed the other model's sweep pass. Verified against the live DB, not
just read: the regex-escape fix works (unescaped `f(x` really raises
InvalidRegularExpression; the shipped `regexp_replace` escapes correctly
and matches `f(x`/`o(n)` in the real `concept_matches` path), and the
full gate reproduces (270 tests, ruff, mypy green). The sweep's own
"Open" list got pruned against decisions already on file:

- **Two "open" items were not decisions — fixed now.**
  1. **PDFs bypassing the capped read seam**: `read_stored`'s identity
     branch already applies `max_decompressed_bytes` to identity files,
     so routing `_pdf_page_texts`'s identity branch through it is a
     one-line fix, not a design call. Done; a test pins that an
     identity-encoded PDF over the cap raises
     DecompressionLimitExceededError (via a patched read_stored with a
     lowered cap — the real 1 GB policy value is not testable directly).
  2. **`free_tier_overhead` never wired**: decision 004 already ratified
     this (5% of input+output, applied at record time, never gating).
     `provider.generate` now passes
     `overhead_tokens=free_tier_overhead(policy, input+output)`. The two
     provider-seam assertions that pinned exact spend picked up the +5%
     (120→126, 60→63); a paid-tier test pins 0%.
- **Genuinely open — kept as decisions, not silently deferred:**
  password reset not invalidating old JWTs (needs a
  token_version/password_changed_at column + a check in current_user —
  a real schema change); `per_source_cap` vs the single-source course
  (decision 008 says "per-source/per-chapter"; needs a ratification
  call); fusion's rank-bucket ordering contradicting `Candidate`'s
  docstring and 008's framing (must reconcile before embeddings turn
  on); `migrate.py` silently skipping malformed filenames; the
  enrollment check for `retrieve()` (lands with tutor-flow wiring);
  free-tier CJK token under-count (documented approximation).
- Sweep findings themselves re-checked and all confirmed real: the
  markdown no-heading/preamble locator fix, zlib.error catch, NUL-byte
  filename sanitize, email length bounds, worker/maintenance shutdown
  cooperation, lost-race token consume, migration index 027, dead-query
  removal, UNION dedupe fix.

Gate: 270 tests (+2), ruff, mypy green.

## Password reset invalidates sessions + password length policy (2026-09-16)

User-directed fixes, scoped to exactly two changes.

- **Reset kills pre-reset sessions.** New `users.password_changed_at`
  column (migration 028, backfilled with now() for existing password
  rows so no live session broke). Every token carries a `stamp` claim =
  the stamp at mint time; `current_user` rejects tokens whose stamp
  predates the live one. Stamp is **microseconds**, not seconds — the
  same-second collision is real (create at .011s, reset at .321s in one
  probe run would have compared equal at second precision and left the
  attacker's session alive). Shared helper `auth.password_stamp`;
  None -> 0 so the claim is always comparable. End-to-end test: register
  -> me works -> reset -> same token now 401 -> new login works.
- **Password policy: 12-24 characters.** `MAX_PASSWORD_LENGTH = 24`
  added to `validate_password` (the 72-byte bcrypt guard stays, it
  prevents silent truncation, not a policy bound). Register and
  password-reset-confirm both funnel through validate_password, so both
  enforce it. Login intentionally does NOT pre-reject over-long
  passwords: verification just fails into the same 401 as a wrong
  password (anti-enumeration unchanged). One test fixture (25 chars)
  shortened.

Gate: 273 tests (+4: stamp roundtrip, stamp-0, invalidation flow,
24/25-char boundary), ruff, mypy, migrations clean.

## per_source_cap no longer starves single-source courses (2026-09-16)

User-ratified fix of sweep open-item #3. The cap is flood control, not a
result ceiling: with one contributing source (the likely first-user shape)
it used to bound the final set at per_source_cap (4) no matter what
final_k said.

Rule: effective_cap = max(per_source_cap, ceil(final_k / contributing
sources)). With >= 3 contributing sources this is just per_source_cap
(4); with 1 it stops binding entirely. Config value unchanged — the
behavior now matches decision 008's "per-source/per-chapter" intent.

Tests: single-source course now surfaces all 5 chunks (was exactly 2);
3-source course with final_k=6 still floods-caps at 2+2+2.

Gate: 274 tests, ruff, mypy green.

## Fusion rebuilt: normalize + allocate (retrieval.toml v2) (2026-09-16)

User-ratified redesign replacing the per-source cap, resolving sweep open
items #3 and #4 as one pipeline (user's framing: "softmax per source" and
"order AND normalization").

- **Normalize first.** Each seam's ranks min-max into 0..1 within the
  seam (embedding dots keep direction; arrival-ordered seams flip sign;
  all-equal seams normalize to 0.5). This is what makes cross-seam
  comparison legal — the raw units (ts_rank ~0.06 vs position 7,8,9)
  were the "ordering is an accident" problem. Caught my own bug here:
  the layers-membership check intersected a set of STRINGS with a set
  of FROZENSETS (always empty), silently sign-flipping embeddings;
  fixed by passing the seam's layer name explicitly.
- **Allocate second.** Source relevance = its best chunk's normalized
  rank from any seam (best-chunk, NOT chunk count — volume must not
  reward rambling lectures). final_k slots split proportionally with
  largest remainder (Hare quota, deterministic). Guardrails: one-slot
  floor per contributing source; availability clamping with reflow;
  backfill so final_k is met when candidates exist.
- **Embedding-only quota semantics fixed along the way:** the quota
  exists to keep semantic expansion from displacing grounded hits; when
  no grounded seam matched, embeddings ARE the evidence and the quota
  does not apply (was silently capping an embeddings-only course at 3).
- Config retrieval.toml v2: `per_source_cap` REMOVED (allocation
  replaces it). RetrievalPolicy field dropped.
- Tests: 4 new (proportional split 2/1/1 by relevance; floor + 
  availability 7/1; single source fills naturally; cross-seam max merge
  admits both) + 2 rewritten (equal relevance splits evenly 2/2/2;
  single source never starved). 30 retrieval total.
- Docs kept honest: decision 008 revision section, system.md §4,
  funnel/Candidate docstrings — the #4 lesson was doc-code drift.

Known tradeoff stated on purpose: relevance-by-best-chunk rewards
"explains it best" only as far as the scoring layer can tell; the eval
set judges, embeddings improve the allocation when they land.

Gate: 278 tests, ruff, mypy green.

## system.md: embedding architecture documented (§4a) (2026-09-16)

User-requested. New §4a captures the full embedding subsystem in one
place: storage (chunk_embeddings, model-keyed rows, indexed model column —
migrations 025/027), the ingestion-time embedding stage (provider-gated;
model-name rows make swaps safe without mass rewrites), the query-time
seam (native float8[] dot products, cardinality dimension guard, stable
ordering, pgvector swap as mechanical later step), normalization
direction (dots keep "higher = closer"; other seams flip), quota
semantics (expansion quota vs the no-grounded-evidence case), and the
eval kill switch. Stale spots synced: chunk-planning paragraph (§2.2,
embeddings live not "planned"), status header (retrieval implemented,
tutor planned), model table row, open-decisions entry, config-version
mention. No code changes; docs-only commit pending.

## Tutor answer endpoint (M1 close-out) (2026-09-16)

POST /courses/{course_id}/ask — the last big M1 piece. Retrieval, trace,
and the billed provider call share ONE transaction: a failed generation
rolls the trace back (an answer that never happened leaves no
evidence-shaped noise; test pins this).

- **Enrollment check** (the review's open item, resolved here): owner OR
  active enrollment; strangers get 404 (existence not disclosed). Test
  covers stranger, owner, and self-enrolled learner.
- **Fork B lean enforced**: empty retrieval = 404 "nothing in the course
  materials matches this question" — never an ungrounded answer.
- **Honest failure**: provider-unavailable = 503 with the real reason
  (the provider seam fails closed until the pick); budget exhausted =
  403. The endpoint never pretends to answer.
- **Fork C working direction in the prompt**: numbered chunks, "use ONLY
  the numbered material, cite as [n], say so plainly if not here."
- Answer, chunk_ids, and trace_id returned; the trace's stored
  retrieved_chunk_ids must equal the answer's citations (pinned).

Open remaining: citation rendering detail (per-claim granularity —
Fork C's open half), conversation threads (Fork D explicitly not
load-bearing), query-embedding plumbing when the provider lands (the
endpoint's embedding params exist and pass None until then).

Gate: 284 tests (+6 tutor), ruff, mypy green.

## Review of worker/retrieval batch #2: critical + high fixed (2026-09-18)

External review, 16 findings. Triage: 11 fixed now, 5 deferred as design
calls (listed at the end). All 290 tests, ruff, mypy green.

FIXED:
1. **Failed sources were citable (critical, golden rule 1)**: no seam
   filtered source.status — a source that died at update_toc kept its
   committed locators+chunks (deliberate, ledger-pinned) and retrieval
   surfaced them. All four seams now require status='indexed'. Explicit
   ruling: mid-run chunks are invisible; 'indexed' is the only citable
   state. Two tests (failed + uploaded states).
2. **ingestion_history had no caller (doc-code drift)**: wired into both
   terminal paths, queued_at captured before the queue row is deleted;
   helper _make_source now enqueues (mirroring the upload contract); a
   test pins the history row carrying the ORIGINAL queued_at.
3. **DB-level stage failure poisoned the ledger (critical)**: a psycopg
   error aborted the transaction, the observer's FAILED write raised
   InFailedSqlTransaction (uncatchable by the orchestrator's
   IngestionPipelineError handler), run stayed 'running' forever. Fixed
   with per-attempt SAVEPOINTs (savepoint/rollback/release around every
   handler call; pipeline takes conn). Test pins fence+rollback.
4. **NUL bytes in chunk text (the #3 trigger)**: binary-as-text/plain
   decoded via cp1252 fallback carrying \x00 → chunk insert 500.
   Stripped at read_decoded's choke point. Test.
12. **Un-ingestable mime stored + charged then failed (quota ratchet)**:
   upload boundary rejects against INGESTABLE_MIME_TYPES (415) BEFORE
   storage+quota. Test pins 415 + zero rows.
13. **mark_source_indexed silent no-op**: guarded UPDATE now raises when
   it matches nothing — a run must not report SUCCEEDED over an
   unindexed source (the #5 double-claim symptom).
14. **Dead branch**: _pdf_page_texts' gzip/else branches were identical;
   collapsed (every read via the capped seam).
16. **Inline FOR UPDATE SQL**: moved to named block lock_user_for_quota
   (AGENTS.md queries rule; last inline holdout in upload_source).
5. **Claim safety backstops** (migration 029): UNIQUE
   (source_id, locator_id, chunk_index) on chunks (double-claim
   delete-then-insert races now fail loudly); claimed_runs_max=5 — the
   claim query refuses over-cap rows (dead-letter by omission: the
   source stays 'uploaded', visible in source stats; requeue is the
   path back after inspection).
7. **Worker poll latency**: ingestion.toml v2 gains its own
   poll_interval_seconds=5 (was sharing lifecycle's 3600s); upload path
   calls worker.wakeup() — an asyncio Event the loop waits on alongside
   stop — so upload-to-ingestion is seconds, not an hour.
8. **Standalone worker entrypoint**: `python -m src.backend.ingest.worker`
   (process_batch already standalone; lifting out before replica > 1).

DEFERRED — design calls, recorded for ratification:
- **#6's read/delete surface**: list/status/requeue endpoints are
  mechanical, but per-source delete needs the citation-snapshot shape
  (golden rule 6) ratified first. Requeue endpoint rides along with it.
- **#9 lock-scope refactor**: file write after commit + memory refresh
  off the upload path — needs the orphan-sweep window reasoned through
  against concurrent uploads (the sweep exists; the invariant check is
  the work).
- **#10 chunk/locator dedupe (chunk_locators join table)**: agreed the
  right shape, MUST land before embeddings populate (else triple-stored
  vectors); scheduled as its own migration task now.
- **#11 uri second source of truth**: confirmed mechanical once #10's
  migration is open (same batch).
- **#5's heartbeat**: claimed_runs_max dead-letters the pathological
  case; a claim heartbeat + liveness check is the scaling follow-up
  when replica count > 1 (with #8's entrypoint split).

Gate: 290 tests (+6: failed/pending citability ×2, history queued_at,
savepoint fence, NUL strip, 415-no-store), ruff, mypy green.

## Ratified batch: source surface, batch refresh, heartbeat, chunk dedupe (2026-09-18)

User ratified all four deferred items. All 299 tests, ruff, mypy green.

**#6 — source read surface (the file browser's data layer).** The API was
write-only; a user never learned their upload failed. New owner-only
endpoints on sources.py:
- GET /courses/{id}/sources — the file list with live status,
  error_message, size, timestamps (the central-area file browser's
  feed; the viewer rendering is frontend work, the data layer is now
  there). 404 for non-owners (existence not disclosed).
- POST /courses/{id}/sources/{source_id}/requeue — the deliberate retry
  path, previously unreachable from anywhere. failed→uploaded + queue
  row + worker wakeup. 409 when not failed.
Per-source DELETE stays deferred to the citation-snapshot design (the
golden-rule-6 record must exist before anything can be destroyed).

**#9 — course-memory refresh is per-BATCH, not per-upload.** The upload
path no longer rebuilds the summary inside the quota-locked transaction;
the worker refreshes once per touched course at the end of a successful
batch. Five uploads to one course: one rebuild, not five. The refresh
owns its own transaction and logs (never crashes) its failures — the
next terminal path touching the course catches a missed refresh.
Upload tests updated to the batch contract; a worker test pins
one-refresh-per-course-per-batch.

**#5 — dead-progress fix (heartbeat fence, migration 030).** The run's
stage ledger IS the liveness signal: every stage transition heartbeats
pending_ingestion.heartbeat_at. The stale-claim sweep now judges
HEARTBEAT freshness, not claim age — a slow-but-alive run is never
re-claimed out from under a live worker (the two-pipelines-racing
delete-then-insert failure mode is fenced). Claims with no heartbeat yet
fall back to claimed_at (the pre-first-stage crash window keeps the
30-min budget). Released claims lose their stale heartbeat. Plus 029's
claimed_runs_max=5 dead-letters the cursed-source kill-loop, and
chunks gained UNIQUE (source_id, locator_id, chunk_index) as the
race backstop. Three tests: fresh heartbeat fences, no-heartbeat still
ages out, sweep clears both fields.

**#10 — chunk/locator dedupe (migration 031, before embeddings).** One
row per LOGICAL chunk now: primary locator on chunks, full span in the
new chunk_locators join table (citation map, ON DELETE CASCADE). The
backfill re-pointed every duplicate row's locator at the group's kept
row (min locator_id — UUIDs have no MIN(), found via ordered subquery)
and deleted duplicates; uq_chunks_source_index added as the forward
backstop. The pipeline's chunk writer inserts one row + N join rows.
Effect: retrieval hits no longer scale with locator grain; the same
text will never be embedded N times. Test pins one-row + full-span.
Live DB backfill verified: 0 dupe groups remaining.

Remaining open (unchanged): citation-snapshot shape (#6's delete),
#9's file-write-after-commit refinement, provider pick (now unblocked
for embeddings — 031 landed first as promised), real eval set.

## Embeddings live: self-hosted granite R2 (2026-09-19)

Provider pick ratified as **ibm-granite/granite-embedding-english-r2**
(149M ModernBERT, 768d, 8192-token context, Apache 2.0, no query/doc
prefixes — symmetric). Self-hosted IN-PROCESS via sentence-transformers:
course text never leaves the machine, so the no-retention vendor check
does not apply to embeddings by construction. Generation (tutor_answer,
update_toc, extract_knowledge) stays on the hosted `generate` seam —
contract ratified: OpenAI-style chat-completions API for that provider
when it's picked. The two seams are deliberately separate:
- `provider.embed_query/embed_chunks` — local, unbilled, no tier gate;
  contract in configs/embeddings.toml (model name is the
  chunk_embeddings row key; dimension enforced loudly at load AND per
  call; normalize_embeddings=True — R2 ships unnormalized vectors).
- `provider.generate` — hosted HTTP, still _call_provider-stubbed until
  the chat provider lands.

New pipeline stage EMBED_CHUNKS after build_chunks (6 stages now;
ingestion.toml v4 gained the stage row). Stage is delete-first
idempotent like the rest: delete_chunk_embeddings by source, then one
replace_chunk_embedding per chunk keyed by model name. New query blocks
delete_chunk_embeddings / chunk_ids_by_source_index /
replace_chunk_embedding (ON CONFLICT (chunk_id) upsert).

api/tutor.py ask: now embeds the query (embed_query + model name from
configs) and passes both into answer_question — the embedding seam
participates in retrieval end to end.

Tests: autouse conftest fixture _fake_embedding_backend stubs the
backend (deterministic vectors at the configured dimension — the seam's
dimension check applies to fakes too, which caught a fake bug the first
time). No test loads the real 600MB model. Worker test pins the whole
pipeline succeeding with the fake; runs test asserts the 6-stage ledger
order. Gate: 300 tests, ruff, mypy green.

Remaining for full M2: chat provider behind `generate` (OpenAI-style API
contract, no-retention check on that key = go-live gate), pgvector swap
(still float8[]; mechanical), real eval set (real material).

## Eval harness v1 + prompt registry (decision 010) (2026-09-20)

Plan doc: docs/decisions/010_eval_harness_v1.md (ratified shape: boring,
mechanical scorers, no LLM judge in v1, provider-free, expandable by
case-kind). Built:

**Prompt registry.** configs/prompts.toml (v1) holds every
system/instruction prompt: tutor_answer, toc_update,
course_knowledge_extraction, probe_generation, probe_evaluation,
artifact_generation (the last three are stubs awaiting their subsystems;
the validator enforces full KNOWN_GENERATION_TASKS coverage, so a new
task forces a prompt decision). Loader:
src/backend/common/prompt_registry.py — load_prompt(task) +
load_prompt_policy() with prompts_config_version for trace attribution.
Consumers wired: build_prompt (tutor/answer.py) and the orchestrator's
update_toc/extract_knowledge now read prompts from config; behavior
identical to the old hardcoded strings (all existing tests pass
unchanged). Tutor prompt now also carries the three-zone steer line
(decision 009): fill-in requests get reasoning + practice invitation.

**Answer harness.** src/backend/evals/answer.py — the answer-side twin
of retrieval/evals.py. Cases in data/eval/answer/cases.json, 6 v1 cases
across four kinds: green_grounded (citation validity — every [n] must
index a provided chunk), cold_probe (refusal marker present AND no
citations = no fabricated specifics), yellow_steer (steer markers
present AND no bare fill-in shape), red_refuse (same refusal scorer).
Retrieval is FIXED via seed_chunk_labels — the harness exercises the
generation contract, not retrieval quality (that's the retrieval eval's
job). Runner takes `generate(task, prompt) -> str` (stub in v1, real
provider later) and the tutor's real build_prompt. Summary dataclass
mirrors EvalSummary (__str__, per-kind pass rates, unresolved cases
excluded and reported); logs to runs/eval_answer_*.log.

**Tests.** tests/test_eval_answer.py (8): scorers unit-tested against
scripted shapes; case file sanity (unique ids, known kinds); unknown
kind fails closed; end-to-end run on the dev DB with a scripted
provider — all four kinds score correctly, summary logged. Gate: 308
tests, ruff, mypy green.

Explicitly deferred (documented in 010): LLM-judge scoring, deep-read
fallback ladder (needs its own decision doc amending Fork B), artifact
eval (no generator yet), real material (synthetic cases only).

## Harness review round: 13 findings triaged (2026-09-20)

External review of the answer harness verified 13 issues by running it.
All real. Fixes applied in two passes (scorer semantics, then
logging/inspection); both design findings resolved by decision and
written into 010's revision section:

Scorer fixes: citations in refusals are no longer fabrication (#1 —
the best honest refusal cites what the course covers); steer + refusal
markers are word-boundary regexes (#2 — "try" no longer matches
geometry); refusal marker list widened (#12); cold-probe scorer gained
its fabrication half (#8 — digit-sequences absent from provided chunks
fail as invented); expectation.citations_required wired (#13).

Runner fixes: course resolution via course_by_tag (#3 — deterministic
against duplicate names); seed-label typo = UNRESOLVED not model
failure (#5); seed ordering fully determined (#6); all_passed requires
every case executed (#4 — never a silent pass).

Logging: every run stamps prompts_config_version (#7) and carries
per-case inspection records — prompt, answer, chunk ids (#9, golden
rule 2); microsecond stamps; minors (dead constant, dead import,
missing newline).

Resolved by decision (010 revision): #10 — harness keeps narrow
GenerationFn; real provider lands behind an eval adapter billing a
dedicated eval account (visible spend, never a student's budget). #11 —
cold vs red share the mechanical scorer deliberately; can't-vs-won't is
a v2 LLM-judge case; pass rates stay split per kind.

Deferred with the provider: per-kind baseline rates, module entry
point. Gate: 313 tests (+5), ruff, mypy green.

## Frontend sweep: Kimi leftovers cleared (2026-09-21)

The frontend (SvelteKit + openapi-fetch, generated schema) had 3
svelte-check errors left over from the Kimi session. Root cause
diagnosed, not guessed: the three failing endpoints (GET /course-archives,
GET /course-memories, GET /courses/public) take NO path/query params, so
FastAPI emits no 422 response for them — openapi-fetch then correctly
narrows `error` to `never` (a 2xx-or-throw middleware client cannot error
at the type level on those routes). The pages branched on `r.error`
anyway. Fix is in the pages: branch on `!r.data` only; network/HTTP
failures still throw from the onResponse middleware (ApiError), so
nothing was lost — the dead type-level branch was the only thing
removed. Verified by probing both shapes (members route WITH 422 passes
`r.error` checks; archives route WITHOUT 422 never will).

Note: schema regen (`npm run gen:api`) needs a live backend at
localhost:8000 — currently impossible offline; the committed
schema.d.ts (1755 lines) is consistent with the backend as-is.

Frontend gate: svelte-check 0 errors/0 warnings, `npm run build` clean.
Backend gate: 313 tests, ruff, mypy green. provider.py trailing newline
fixed (review minor).

## MVP wiring: citations endpoint + frontend round (2026-09-21)

**Citations endpoint.** GET /courses/{course_id}/traces/{trace_id}/citations —
the evidence behind one answer: chunk text, primary locator label/type,
source filename, in retrieval order. Access: owner-or-active-enrollment
(strangers 404, existence not disclosed); the trace row is pinned to the
course (trace_for_course block — a trace id from another course 404s, not
leaks). New query blocks: trace_for_course, chunks_with_locators_by_ids
(chunk + primary locator + filename join; enough to say "page 3 of
lecture2.pdf" without exposing raw files). Schema regenerated from a
live uvicorn run.

**Frontend wired to it.** The ask flow now fetches citations immediately
after an answer and renders a "Sources used" panel: numbered [n] entries
matching the answer's citation markers, each with filename · locator
label and the chunk text. The dead "trace: <uuid>" line is gone. Golden
rule 1 is now honored in the UI, not just the data model.

**Source-status polling.** Fresh uploads sat at uploaded→scanned→indexed
invisibly. After upload AND requeue, the page polls the sources list
every 1.5s while any row is pending (60-attempt cap), stopping when all
settle — the worker heartbeat's UI twin. Polling aborts on error; the
requeue button remains the recovery path.

**Honest errors.** ErrorBanner now has three tones: refusal (404 from
the ask endpoint) renders amber with the explanation that the tutor only
answers from course materials — the strict-refusal contract reads as a
feature, not a crash; unavailable/network renders sky-blue ("working on
it" tone); everything else stays red. The email-verification hint is
preserved.

**Spruce-up.** Inter font (self-hostable later), app-wide #f6f7fb
background, consistent focus rings; sidebar with logo mark, hover/active
states, tier badge; main content max-w-4xl centered; course cards with
hover lift; Card titles to small-caps label style; grounded-answer block
in indigo tint. Empty states (probes/progress/artifacts) untouched —
they're honest about not being built.

Gates: backend 316 tests (+3 citations tests: happy path, foreign-trace
404, stranger 404), ruff, mypy green; frontend svelte-check 0/0, build
clean. MVP is now end-to-end coherent: upload → watch it index → ask →
grounded answer with visible, readable sources.

## Bug sweep + pre-launch fixes: fusion, throttling, OCR (2026-09-22)

Full-repo sweep. One critical bug and three carried go-live items
addressed; the rest of the codebase passed.

- **[bug, CRITICAL] Fusion inverted keyword/dependency evidence.** The
  `_normalize` sign-flip premise ("non-embedding ranks mean earlier
  arrival") was false: keyword is ts_rank (higher better) and
  toc/dependency use `-position` (higher better). The best keyword chunk
  normalized to 0.0 and sank below the weakest, and `best_by_source`
  picked each source's *worst* chunk — corrupting order and the
  relevance allocation, and making the decision-008 kill-switch measure
  garbage. Fix: normalize every seam in the same direction; all seams
  are higher-is-better by construction. Regression tests added for
  keyword order, dependency order, and the DB end-to-end inversion.
- **[security] Login throttling** (carried open item, notes.md
  2026-08-25 + 2026-09-12). New `login_throttle` table (migration 032,
  SHA-256-of-key only) and `common/login_throttle.py` count failures
  against BOTH the normalized email and the source IP — per-email stops
  single-account guessing, per-IP slows spraying. Config-driven
  (`configs/auth.toml`, `max_failures`/`window_seconds`/`lockout_seconds`)
  with an opt-in `trust_forwarded_for` because the socket peer is a proxy
  when fronted. Locked keys return 429 + Retry-After; a successful login
  clears the email key but deliberately not the IP key (a valid credential
  must not reset a host's spray counter). Window reset, independent locks,
  proxy opt-in, and endpoint 429 covered by `test_login_throttle.py`.
  Token revocation remains open (separate item).
- **[security] Prompt-injection input marking** (the orchestrator's
  documented go-live gate). Uploaded course text is untrusted, so every
  prompt that embeds it now fences it between literal
  `<<<UNTRUSTED_COURSE_MATERIAL begin/end>>>` markers via
  `prompt_registry.grounded_prompt`, and the `tutor_answer`/`toc_update`/
  `course_knowledge_extraction` prompts state the block is data, not
  instructions. Marker occurrences inside the content are neutralized so
  an upload cannot close the fence early. Both the ingestion prompt
  assembly and the tutor prompt builder route through the one seam.
  `test_prompt_fencing.py` pins the contract. Documented mitigation, not
  a proof — noted as such in the code.
- **[ingest] OCR for image-only PDFs** (was "scanned sources fail
  loudly"). New `ocr` pipeline stage between extract_text and
  build_locators: extract_text classifies a no-text-layer PDF as
  `ScannedPdfNeedsOcrError` (still an EmptyExtractionError for old
  callers) instead of failing; the ocr stage rasterizes pages
  (pypdfium2 — Apache/BSD, no system binary; capped at `[ocr] max_pages`
  so one scan cannot fan out into hundreds of calls) and sends them to
  the multimodal model via the provider seam. `ocr` is a new generation
  task billed to the **ingestion** pool, routed per tier in tiers.toml
  v8, prompt in prompts.toml. OCR text is joined with the exact page
  builder text-layer PDFs use, so page citations align; page separators
  the model fails to emit degrade to page-1 span rather than
  misciting. Provider-less deployments fail the ocr stage with an
  actionable error and the source stays `failed` — never a
  falsely-indexed empty base. Provider `generate`/`_call_provider` grew
  an `images` argument (None for every text task); all test stubs updated.

Gate: full pytest (316 → 332), ruff, mypy green; migration 032 applied.
Not in this batch: model provider (#1) and a real eval set (#2) remain
the operator's planned work; CI (#3) is a later conversation.

## Docs updated + relocated to docs/ (2026-09-22)

AGENTS.md, project.md, system.md moved into docs/. Updated to current
reality:

- AGENTS.md: structure tree (provider/prompt_registry/api/queries/repos,
  evals + retrieval evals split, migrations 001–032), prompt + model-seam
  conventions (prompts in configs/prompts.toml never inline in code,
  untrusted-material fencing via grounded_prompt), test gate env note
  (PYTEST_ALLOW_ANY_DB), eval-harness summary, gen:api needs live backend.
- project.md: inference section rewritten to the two-seam reality
  (generation hosted no-retention; embeddings self-hosted granite R2 —
  no-retention check doesn't apply there by construction); pipeline now
  includes OCR + chunk-once chunk_locators + embeddings stage; M1 marked
  DONE (worker, four-seam fusion, tutor ask, citations endpoint);
  evaluation plan annotated with what's live vs LLM-judge-later; open
  questions updated (chat provider lean: Xiaomi MiMo; embeddings pick
  CLOSED).
- system.md: status paragraphs (§1, TL;DR) reflect full pipeline + tutor
  + citations endpoint live, only the hosted chat key missing; §3
  pipeline diagram rewritten (OCR stage, embed stage, chunk_locators,
  heartbeat-fenced queue, batch memory refresh, SAVEPOINT fence);
  §4a marked LIVE with the granite contract; §6 split into generate
  (hosted, fails closed) vs embed (self-hosted) with the MiMo lean
  noted; sequence diagram now shows embed + citations fetch; §2.2 ER
  diagram gains CHUNK_LOCATORS; auth section gains password-stamp
  invalidation + login throttle; 6a gains ocr task + embedding-exempt
  ledger note; §7/§2.4/§2.5 marked designed-not-built with live-vs-future
  split; §10 open decisions shrunk to the chat provider + pgvector +
  revocation.
- NEW docs/decisions/009_three_zone_assistance_policy.md — the ratified
  three-zone policy (green/yellow/red, steer-as-feature, pricing-not-
  policing) written down since 010 and the prompt registry already
  referenced it.
- Cross-references audited: every docs/decisions/0XX pointer resolves to
  a real file; all three docs consistently say two seams, four seams,
  32 migrations.
- src/frontend/README.md gets a pointer to the docs/ location.

Gates at close: 335 tests, ruff, mypy green; svelte-check 0/0, build
clean.

## Chat + workspace harness, dark mode finished (2026-09-22)

Finishes the dark/light mode + "chat left, workspace right" work Kimi left
uncommitted mid-task. Kimi had built the theme store, dark: variants,
RichText (markdown + KaTeX + DOMPurify), and a frontend-only
` ```artifact ` parser with Quiz/EditableDocument/ArtifactPanel. Hanging
pieces and what replaced them:

- **[golden rule 1 / decision 009] Uncited artifacts rendered.** The
  frontend parser accepted quizzes/docs with no citations, and the tutor
  prompt never taught the convention ("Answer in plain prose"), so the pane
  could never fill in practice. Contract moved to the backend:
  `tutor/workspace.py` lifts ` ```workspace ` blocks (Pydantic
  discriminated union: quiz | document) and enforces the decision-009 hard
  gate in code — every question/document must list `sources`, and every
  `sources` entry + inline `[n]` must index provided material. Failing
  blocks are withheld with a human-readable reason (`withheld` on
  `AnswerView`, rendered inline) and removed from the chat body either way,
  so a rejected quiz never leaks its answer key as raw JSON. `AnswerView.text`
  is now the chat body; `workspace` carries the validated items.
- **Naming.** "Workspace item", not "artifact": `user_artifacts`
  (decision 003) are persisted learner artifacts; workspace items are
  ephemeral per-answer views. Saving one is the M5 path.
- **Prompt v3** (`prompts_config_version` 2 → 3): tutor_answer allows
  Markdown + LaTeX and teaches the workspace fence with `sources`.
- **Measured:** new eval kind `workspace_grounded` (scorer reuses the
  endpoint's gate, so eval and product can't drift) + case
  `workspace-quiz-linear`; `test_tutor_workspace.py` pins the gate;
  endpoint test covers lift + withhold.
- **Frontend:** `ArtifactPanel`/`utils/artifacts.ts` removed; items come
  typed from the generated schema. `stores/workspace.svelte.ts` holds
  per-item session state (quiz answers, doc draft) so switching answers
  keeps progress — fixes Kimi's EditableDocument effect that discarded
  edits on every Preview toggle. Quiz: per-question source chips after
  grading, "Ask about what I missed" pre-fills the chat. Doc: Revert +
  Download .md. Ask tab is now chat-shaped (history above, input below,
  starter prompts); course page gets a max-w-7xl shell for the split.
- **Dark-mode bugs:** `.dark html` selector never matched (class is ON
  html) → `html.dark`; body text was hardcoded slate-900 → themed in CSS;
  pre-paint script in app.html removes the light flash; last stragglers
  (input error text, member rows, failed status).
- **[dev] Vite proxy swallowed page loads:** `/courses` is both an API
  prefix and an SPA route, so refreshing a course page in dev proxied the
  navigation to the backend (JSON or 502). Proxy now bypasses requests
  that accept text/html. The deployment static server needs the same rule.

Open: the app shell has no mobile layout (fixed 240px sidebar leaves
~110px of content at phone width) — separate item. Workspace grading is
local only; writing misses into the student model is decision 009's
"log the struggle" step, blocked on the M3-4 student_model subsystem.

Gates at close: 348 tests, ruff, mypy green; svelte-check 0/0, build
clean; UI flow verified in both themes with a mocked API (Playwright).

## Portable notebook citations and source reindex (2026-09-25)

- `.course` format v2 carries saved chats, artifact versions, and only the
  cited passage text/locator records. On import, new source and citation
  IDs are assigned, traces and artifact citation lists are remapped, and
  the cited passages become `citation_snapshots`. Source ingestion then
  rebuilds the current search index without changing an old answer's
  evidence. Format v1 remains importable.
- An explicit source Reindex action applies extraction improvements to an
  existing upload. Before the index is replaced it snapshots passages
  referenced by traces and artifact versions. The citation query prefers
  a live chunk while it exists, then the snapshot. Source deletion still
  removes both; an artifact keeps a visible missing-citation slot.
- A v2 archive is limited to 64 MiB of notebook JSON at import to bound
  decompression and validation memory. If a very long-lived course hits
  this limit, the archive format needs a streaming notebook member before
  the cap can be raised safely.
- Review fix: the first PDF table detector took any three lines with wide
  gaps as a table, which split justified prose into word "cells" (17 of
  18 pages of a book chapter). A table now needs its cells to start at the
  same columns on every row; on the local PDFs only the syllabus grading
  scale and one census table qualify.
- Review fix: saving an artifact re-checked every source it cites, so
  deleting a source made its artifacts unsavable (422). Only newly added
  sources are checked now.

## N0 Office-fidelity spike (2026-09-25)

Plan §11 step N0: measure, per format, which library can open and edit a
real Office file without disturbing the rest of it. This is research and a
throwaway setup only: no product code, no migration, no artifact kinds.
All spike code lives in gitignored `runs/spike-office/`.

**Corpus and harness (committed).** `scripts/make_office_corpus.py`
generates `data/eval/office/sample.{docx,xlsx,pptx}` plus `sample.png` from
invented data (each < 40 KB). The DOCX has a custom style, bold/italic
runs, a merged table, an image, header/footer, margins and a numbered list;
the XLSX two sheets, cross-sheet formulas, currency/date formats, a merge,
column widths, a chart, an image and a defined name; the PPTX two layouts,
bullets, an image, a table and speaker notes. One documented gap: the DOCX
has **no footnote** because python-docx cannot author one through its
public API. `scripts/office_fidelity.py` unzips both files, classifies
every part as identical / equal after XML C14N / changed (with a diff
summary) / added / missing, flags parts changed outside `--expected`, and
reopens the saved file with python-docx/openpyxl/python-pptx; it converts
to PDF with `soffice` when available. `tests/test_office_fidelity.py` has
nine tests; the full gate is now 431 pytest passed, ruff clean, mypy clean
(89 files), svelte-check 0/0, clippy clean.

**Review-hardening pass (same day).** Review found and fixed four real
defects in the committed seed, so the numbers above are from the corrected
code:

1. **Corpus was not reproducible.** Every regeneration changed ZIP entry
   timestamps and `docProps/core.xml` (`modified`), so the committed fixture
   could not be byte-compared. `make_office_corpus.py` now pins core
   properties and rewrites ZIP entry times to a constant; the XLSX writes
   through `openpyxl.writer.excel.ExcelWriter` directly because
   `Workbook.save()` stamps `modified` with the wall clock regardless of the
   property. A test asserts byte-identity across two generations.
2. **`main()` crashed outside the repo.** The summary line called
   `path.relative_to(ROOT)`, which raised `ValueError` for any `OUTPUT`
   outside the checkout (the old test passed only because pytest's
   `--basetemp` sits inside the repo). It now falls back to an absolute
   path, with a test using a temp dir.
3. **A dropped expected part could pass.** `unexpected` only considered
   non-expected parts, so a save that deleted the very part an edit was
   supposed to touch was not flagged. `FidelityReport` gained
   `missing_expected` (deleted expected parts, plus expected globs that
   matched nothing) and an `ok` property; the CLI exits non-zero when not
   `ok`. Tests cover a dropped part and a no-match glob.
4. **Comments/PIs were erased by C14N.** lxml's default canonicalization
   drops comments, so a save that removed a comment looked "equal". It now
   canonicalizes with `with_comments=True`; a test pins it.

Also: `compare()` now raises `FileNotFoundError` for a missing input
instead of a raw `zipfile` error, and `_reopen` compares suffixes
case-insensitively.


**Commands run.**

- `python -m scripts.make_office_corpus`
- `python -m scripts.office_fidelity ORIGINAL SAVED --expected GLOB`
- round trips: `runs/spike-office/run_backend_writers.py`,
  `measure_docx.py`, `measure_pptx.py`, `run_excel_writers.py`,
  `measure_excel.py`, `run_docx_headless.mjs` (the docx-editor headless
  automation host from `@docx-editor.dev/core/automation`), `run_pptx.mjs`
- browser: `py -3 drive_pages.py`, `drive_roundtrips.py`, `drive_preview.py`
- bundles: `SPIKE_PAGE=docx|excel|pptx npm run build` then
  `py -3 measure_bundles.py docx excel pptx`
- licenses: `npm view`, package-lock parsing, `pip show ironcalc`

**Round-trip results (synthetic files only; real files absent).**

- **python-docx / python-pptx (backend, recommended writers):** no-edit
  save changed **0** parts; a one-paragraph / one-title edit changed only
  `word/document.xml` / `ppt/slides/slide1.xml`. Both reopen. This is the
  only pair that passes the strict untouched-part rule.
- **docx-editor 2.22.0** (UI + headless host): renders the sample
  correctly (text, merged table, image, header/footer). No-edit save
  changed **8** parts — `customXml/item1.xml`, `customXml/itemProps1.xml`,
  `docProps/app.xml`, `word/document.xml`, `word/fontTable.xml`,
  `word/stylesWithEffects.xml`, `word/theme/theme1.xml`,
  `word/webSettings.xml` — and an edit changed the same 8, of which only
  `word/document.xml` was intended. Reopens, styles/tables/images intact.
  **Fails** the strict check. The v2.22 UI command that used to be
  `replaceMatch` is gone/serializer-changed; the supported edit seam is
  the headless `automation` host (`replaceSpan`), which worked.
- **pptx-viewer-core 4.6.0:** no-edit save changed **3** unexpected parts
  (`docProps/app.xml`, `docProps/core.xml`, `ppt/_rels/presentation.xml.rels`);
  a title edit changed those 3 plus the intended `slide1.xml`. Reopens
  with both slides, layouts, image, table and notes intact. **Fails** the
  strict check. The Svelte 5.57.1 page now loads (the earlier
  `target.exclude.includes is not a function` crash did not recur) and
  reports "Loaded 2 slides in 644 ms".
- **IronCalc 0.8.3** (Python wheel, scratch venv): `=SUM(Inputs!B2:B3)`
  recalculates 1200→1580 after setting the input, persists, and reopens.
  But no-edit save changed **26** parts and dropped `xl/charts/chart1.xml`
  and `xl/media/image1.png`; edited save changed 24 and also dropped them.
  The read cache came back 0 until the model evaluated. Only useful as a
  formula engine, never as the writer.
- **FortuneSheet + FortuneExcel:** workbook renders (two sheet tabs,
  shortcut help) but `transformFortuneToExcel` throws
  `TypeError: Cannot read properties of undefined (reading 'forEach')` in
  its inline-string style handling for **both** no-edit and edited saves.
  No roundtrip file at all. Dates show as serials and the image is not
  shown. A browser Excel editor is not viable on this version.
- **Cell-XML patch prototype:** changed only the two intended worksheet
  parts; formula cache (1580), chart and image survived and the file
  reopened. Explicitly **not production-ready**: it hardcodes two known
  numeric cells and needs sheet relationships, all cell types, shared
  formulas, calc chain and signature handling. It is the direction
  recommended for N1.

**Cost (synthetic; production builds; dev machine, not the 8 GB floor).**
docx-editor 3.46 MiB js+css min / 979 KiB gzip, plus 417 KiB HarfBuzz WASM
(168 KiB gzip); FortuneSheet 3.99 MiB / 934 KiB; pptx-viewer 8.45 MiB /
2.25 MiB. First-open: docx 0.65 s, excel 1.70 s, pptx 7.2 s page / 0.64 s
viewer-reported load. These are spike bundles, not the app delta (React
and Svelte are shared with existing code). No real-file timing exists.

**Dependency / license audit (scratch lockfile, 390 packages).** 326 MIT,
19 ISC, 15 Apache-2.0, 13 MPL-2.0, 5 BSD-3-Clause, 2 unlicensed, plus
singletons. Direct: docx-editor core/react and pptx-viewer-core /
pptx-svelte-viewer are Apache-2.0; FortuneSheet and FortuneExcel are MIT;
ironcalc wheel is MIT/Apache-2.0 (both `LICENSE-MIT.md` and
`LICENSE-Apache-2.0.md`). Apache NOTICE obligations: docx-editor ships
`THIRD_PARTY_NOTICES.md` + `licenses/HarfBuzz-COPYING.txt`; pptx-viewer
ships `NOTICE` and discloses bundled **MPL-2.0 `mtx-decompressor`**
(ported from libeot). Transitive copyleft: 13 MPL-2.0 (mostly
`lightningcss` platform binaries), `dompurify` (MPL-2.0 OR Apache-2.0),
`jszip` (MIT OR GPL-3.0-or-later) — take the permissive arm where offered.
Unresolved: **`buffers@0.1.1`** declares no license in npm metadata or its
repo README (only reachable via `binary` ← `unzipper` ← FortuneExcel);
`jstat` likewise lacks a package.json license field but its LICENSE file is
MIT. The paid docx-editor Pro editor-api/comments/tracked-changes packages
are **not** present in the lockfile; exclude them. Confirm all of this by
reading shipped LICENSE/NOTICE files, not metadata alone.

**Surprises.** (1) The docx-editor UI command API changed under us:
`replaceMatch` is rejected by the tree editor, so the UI page's "edit"
button does nothing; the headless automation host is the real seam. (2)
Every browser serializer except pptx-viewer's round-trip output touches
metadata/relationships it was never asked to change, so "byte-for-byte
preservation" README claims did not hold on this corpus. (3) FortuneExcel
could not save at all, which is a harder failure than lossiness. (4)
IronCalc's write path silently drops charts and images, confirming the
existing openpyxl warning applies to more writers than openpyxl. (5) The
pptx-viewer Svelte crash was version-specific and cleared on 5.57.1.

**Not run / unverified.** Real owner files (`runs/fidelity/` does not
exist). LibreOffice (no `soffice` installed) — `libreoffice_pdf` is
reported "not run (soffice unavailable)" for every result. Tauri WebView2:
an attempted `tauri` run compiled and started but the native UI was not
reachable by automation and the remote-debugging endpoint on port 9333 did
not respond. Bundle/first-open numbers are Chromium on the development
machine, not WebView2 on the floor machine. Nothing here should be read as
a real-file or WebView2 result.

**Provisional recommendation.** Treat the file as the artifact and write
it in the backend: python-docx and python-pptx for Word/PowerPoint model
edits (measured clean), and a hardened cell-level XLSX patcher for Excel
(prototype clean; must be productionized in N1). Use docx-editor,
pptx-viewer and (if it is ever fixed) FortuneSheet as preview/edit
surfaces that submit narrow operations back to the backend writer, not as
serializers. IronCalc is an evaluation engine for display only. This stays
provisional until the owner's real files and a WebView2 run confirm it.


## N1 real Office files: the file IS the artifact (2026-09-25)

Owner's call after the N0 spike: real .docx/.xlsx/.pptx must work as the
artifact, with no version mismatch, and the course layout becomes
resizable popout panes. Built the backend writer first — that is where the
fidelity guarantee lives — then the frontend editors and the layout.
Nothing is committed; the working tree is left for review.

**The rule.** The artifact *is* the Office file. Every version keeps the
whole file, content-addressed by sha256; `content` is a projection derived
from the file, never the source of truth. The backend writes; the browser
only previews and submits narrow edits. This is why no mismatch is possible:
the browser never serializes OOXML.

**Backend.**
- `006_office_artifacts.sql`: added kinds `word`/`excel`/`powerpoint`. SQLite
  cannot alter a CHECK, and `artifact_versions` is a child of `artifacts`,
  so the migration drops the child first (no cascade fires), copies both
  tables into rebuilt ones with the wider CHECK plus file columns, and
  renames. Verified: old kinds intact, new kind accepted, bogus rejected,
  delete-course cascades through both tables.
- `common/artifact_files.py`: raw (not gzipped — OOXML is already a zip)
  bytes under `data/artifacts/<first2>/<sha256>.bin`, atomic write,
  `prune_bytes` that removes a blob only when no artifact or version still
  references it.
- `artifacts/office.py` — the single writer:
  - Word: python-docx edits `word/document.xml` in place.
  - PowerPoint: python-pptx edits the slide part in place.
  - Excel: **cell-level XML patch.** openpyxl and IronCalc both rebuild the
    package and drop the chart/image (N0 measured this), so a `set_cell`
    edit patches the worksheet part, extends `sharedStrings.xml` when the
    workbook uses it (else writes an inline string), and sets
    `fullCalcOnLoad` so Excel recalculates the patched formula instead of
    showing a stale cache. Everything else is copied byte-for-byte.
  - `outline()` regenerates the small JSON the UI shows and the model will
    read; `validate()` reopens the produced bytes.
- `artifacts/files.py` + API routes: `POST /artifacts/office` (import a
  file or create blank), `GET /artifacts/{id}/file`,
  `POST .../office-edits` (narrow edits → a new version, 409 on stale),
  `POST .../save-file` (write to a dialog path for Open in Office),
  `GET .../versions/{n}/file`, `POST .../rename`. The JSON `PUT` and the
  JSON create route now **refuse** file kinds — a JSON save would clear the
  file pointer (this was a real bug in the rename path until the dedicated
  route replaced it).
- `.course` **v3**: the manifest lists artifact blobs by sha256
  (`artifacts/<sha>.bin`, stored, not recompressed), validates each hash on
  import, and the importer still accepts v1/v2.

**Fidelity, measured (synthetic corpus).** `tests/test_office_artifacts.py`
pins the N0 result on the real files generated by the app's own writer:
Word one-paragraph edit changes only `word/document.xml`; PowerPoint only
`ppt/slides/slide1.xml`; Excel changes only the worksheet parts **plus**
`xl/workbook.xml` (the intended `fullCalcOnLoad`), with the chart and image
still present. Shared-string extension is covered by a hand-built workbook.

**Frontend.** `OfficeArtifact.svelte` is the editing surface: Word renders
per-paragraph text fields (committing on blur), Excel a formula-aware grid
(value as text; `=`-prefixed cells shown mono), PowerPoint a slide list with
title/body/notes. Each commit sends one narrow edit to the backend. The
artifact panel gained "New blank"/"Import file" tiles and an "Open in
Office" action. `.course` import and download use the existing Tauri
dialog/reveal patterns.

**Layout.** New `ResizableSplit.svelte` (pointer-drag divider, arrow keys,
stacks below 1100px), `stores/panel.svelte.ts` (artifact tabs + width +
hidden, persisted to localStorage), and `Panel.svelte`, which merges the
artifacts the student popped open with the chat's transient workspace tabs
into one right-hand tab strip. `WorkspacePanel.svelte` is deleted; the
course page still has the chats | chat | panel columns but the panel is now
resizable, collapsible, and persistent. Clicking an artifact card opens it
in the panel instead of navigating away.

**Verified live.** Backend endpoints exercised against a running uvicorn:
import → byte-exact download, Word/Excel/PowerPoint edits, Excel chart+image
kept, stale save 409, rename preserving the file, v3 archive round trip.
The browser UI was driven with Playwright: opening an artifact from the
Artifacts tab shows the panel, editing a paragraph writes to the real .docx
(v2, table and image intact), the panel toggle persists to localStorage, and
there were **zero console errors**. `npm run desktop` compiles and launches
(needs `~/.cargo/bin` on PATH). Servers were stopped afterward.

**Gates.** 453 pytest passed (1 pre-existing warning), ruff clean, mypy
clean (92 files), svelte-check 0/0, `npm run build` clean, clippy clean.

**Still open / risks.**
- Real owner files remain untested (`runs/fidelity/` absent). The Excel
  patcher is the sharp edge: it needs shared-formula groups, the calc
  chain, and digital-signature handling before it is trusted on arbitrary
  workbooks.
- Model edits as proposals are NOT wired for file kinds yet — the in-app
  editors do direct narrow edits; `propose-edit` stays JSON-only. The
  "Ask Stacks" form is hidden for Office files.
- Large sheets are truncated in the outline (first 200 rows) for display;
  the file keeps everything. No virtualization yet.
- The save-back watcher ("Open in Office", edit there, it becomes a version)
  is not built; today the student re-imports or uses save-file.
- WebView2 specifically was exercised only via Chromium and a short Tauri
  launch, not a scripted in-window run.

## Office editing: anchored dirty-block splice (2026-09-26)

**plan-notebook.md reset.** The file had accreted into a constantly updated
project notebook (phases, handoffs, status of everything). It is now only
the current plan — real Office editing — per the owner's ruling; the removed
history is safe in git and in this log (N0/N1 below and all earlier
entries). Convention going forward: plan-notebook.md describes the work at
hand and is replaced when the work changes, not appended to forever.

**Direction being tried (not yet built): anchored dirty-block splicing.**
The problem statement: N1 made the real .docx/.xlsx/.pptx file the artifact
with backend-only writers, but the in-app editing surfaces are narrow-edit
projections (paragraph textareas, an HTML table, title/body/notes boxes),
not Office editors. The new approach, decided after reviewing the options:
the browser never serializes from an editor model. Instead, parse the
original part into a block tree anchored by position + original XML slice,
track dirty blocks in the editor, regenerate only dirty blocks as OOXML
fragments (referencing existing styles only), and splice them into the
original part bytes — every other part copied byte-for-byte. Safety: compare-
and-swap per anchor (a stale anchor fails loudly, never clobbers), per-file
capability detection (regions the op set can't edit render view-only with a
reason), and no silent fallback to whole-part serialization.

**Prior art:** GenOffice (genspark-ai/genoffice, Apache-2.0) ships exactly
this mechanism — "byte-preserving round trip: only dirty paragraphs are
regenerated (paragraph patch), everything else in the original file is kept
byte-for-byte" — parse `word/document.xml` to a block tree anchored by index
+ original slice, Tiptap surface with dirty tracking, dirty blocks → OOXML
fragments referencing existing styles, spliced back. Alpha proof the
mechanism works, not a battle-tested engine; adopt the architecture, read
their `docx-engine`, keep our own hostile-file discipline.

**Supersedes, for human in-app editing:** the N0 provisional recommendation
of "browser libraries as surfaces that submit narrow ops" — now formalized
as dirty-block deltas, with the library serializer explicitly never in the
save path. Consequence: FortuneSheet's broken save and docx-editor's 8-part
churn stop mattering (renderers only); Univer (Apache-2.0, maintained) is
the candidate render base over FortuneSheet; IronCalc stays display-
evaluation only. The N1 backend writers remain the model-edit path and the
foundation the splice engine extends (a dirty block is an op: replace-block /
set_cell / move-shape at an anchor; versions and `.course` v3 can carry ops).

**Per format:** Excel first (cells are addresses; harden the N1 patcher —
shared formulas, calc chain, signatures — and never run a calc engine in the
write path: patch formulas, drop calcChain, set fullCalcOnLoad). Word via
the GenOffice block-tree pattern (decide last, hardest cascade). PowerPoint
via shape anchors — text and move/resize (`a:off`/`a:ext`) are addressable;
creating shapes/z-order/grouping are not, and stay out of scope at first.

**Status: OPEN — being tried, spike first.** S1 spikes the splice engine
(parse → anchor → dirty → splice → verify untouched parts byte-identical,
LibreOffice render-diff) against real files in `runs/fidelity/`; real files
remain the standing gate (still absent). The fidelity harness gains a
semantic layer (bookkeeping vs content-bearing part changes; dropped-element
detection) — a bare changed-parts count can't distinguish benign churn from
loss. Failing formats keep their N1 narrow-edit surface + real render
preview, honestly labeled, rather than shipping a lossy full editor.

## S1 splice engine + S2 Excel deep: all three formats go (2026-09-26)

**S1 done — go on all three formats (synthetic + hostile corpus).** The
engine is `src/backend/artifacts/splice.py`: expat indexes every part as a
tree of **byte spans** (expat's `CurrentByteIndex`, which lxml does not
expose), and a save regenerates only the dirty blocks as OOXML fragments and
splices them into the *original* bytes. Verified: an edit changes only the
part it names; every other part — and every other byte of the edited part —
is identical. Word replace/insert/delete/restyle, PowerPoint text and
move/resize, Excel cell patches all pass; a stale anchor is refused
(`SpliceConflictError`), never clobbered.

Three engine facts worth remembering:

- **Expat's EndElement index is not an exclusive end.** For `<c/>` it is
  *past* `/>`; for `<a>...</a>` it points at the `<` of `</a>`. The span
  index decides self-closing from the element's own start tag and normalises,
  which is why multibyte text and empty elements both slice correctly.
- **A same-part second op must re-index.** Changing an attribute's length
  invalidates every downstream offset, so `set_attribute` callers re-locate
  the span (PowerPoint ops are applied one at a time for this reason).
- **Only the named part may change.** `rewrite_package` swaps named parts and
  copies the rest byte-for-byte, preserving each `ZipInfo`; container bytes
  still differ (recompression) — part-level identity is the standard, as N1
  already said.

**Hostile corpus is the gate.** `scripts/make_office_hostile.py` (committed,
deterministic) builds `hostile.docx` (tracked change `w:ins`, comment range +
`comments.xml`, footnote + `footnotes.xml`, custom XML), `hostile.xlsx` (line
chart, conditional formatting, shared-formula-ready), `hostile.pptx` (title +
body placeholders, two shapes, notes). Tests pin that an edit on one block
leaves all of them byte-identical (`tests/test_office_splice.py`). The one
allowed loss surface is an edited block's own content: replacing the
tracked-change paragraph drops its `w:ins` — the semantic harness reports
that as a lost feature, exactly as intended.

**Semantic fidelity layer.** `scripts/office_fidelity.py` now classifies
bookkeeping parts (rels, theme, docProps, settings, calcChain) apart from
content-bearing ones and reports **feature survival** (`tracked_changes`,
`comments`, `footnotes`, `custom_xml`, `charts`, `conditional_formatting`,
`speaker_notes`). "8 parts changed" is no longer a fail signal by itself; a
lost feature is. It also gained `render_diff()`: both files → PDF → PNG via
headless LibreOffice + pypdfium2, first-page pixel compare. Runs in CI
(`render-diff` job installs `libreoffice-fresh`); tests skip cleanly where
soffice is absent.

**S2 Excel deep.** The cell patcher now (a) **expands shared-formula groups**
to plain formulas with correct A1 relative/absolute translation before
editing, so replacing a member can't strand its `<f t="shared"/>` kin (which
would reopen as an empty formula); (b) drops `calcChain.xml` (and its
Content-Types override + workbook rel) and sets `fullCalcOnLoad` when a
formula is written, so Excel recalculates rather than showing a stale cache;
(c) **refuses a digitally signed workbook** (`_xmlsignatures/`) instead of
silently invalidating the signature; (d) compare-and-swaps the target cell's
bytes. Chart, image and conditional formatting survive every patch.

**Ops are recorded.** Migration `007_artifact_version_ops.sql` adds
`artifact_versions.ops` (JSON, nullable; additive). Every file save stores
the op list that produced it, and the versions API returns it, so history can
show which anchored block changed, not merely that the file did.

**Integration.** `artifacts/office.py` is now a thin dispatcher over the
engine; `OfficeEdit` gained `anchor`/`expected`/`style`/`role`/`after`/`shape`
/geometry, exposed through `OfficeEditBody`; the outline projection carries
per-block/per-shape `anchor` + `hash` and Word run formatting. The frontend
surface addresses Word paragraphs by anchor+hash (CAS) and lists slide
shapes. Gates: 476 pytest, ruff, mypy `src` clean, svelte-check 0/0, SPA
build clean.

**S2 caveat.** The workbook outline still uses openpyxl in read-only mode
(display projection only, never a writer); the write path is the splice
engine. Univer remains the candidate richer grid surface; nothing in the save
path uses a library serializer.

**Still the standing gate:** real owner files in `runs/fidelity/` (paper with
tracked changes, accounting workbook, class deck). Every fidelity claim above
is on synthetic/hostile fixtures until then.

## Office editing surfaces and fidelity hardening (2026-09-26)

The Word surface is now a Tiptap paragraph editor with run toggles, existing
paragraph styles, paragraph insertion/deletion, anchored dirty-block saves,
and locked read-only nodes for tables and paragraphs whose structure cannot
survive a rich-run rewrite. The Excel surface is a coordinate-aware grid with
sheet tabs, address navigation, a formula bar, keyboard editing, and 30-row
paging. It edits only the projected first 200 rows and 40 columns; projected
row numbers and cell hashes now address the real cells, including rows after
blank gaps. The PowerPoint surface is a shape layout map with drag move/resize,
keyboard nudges, simple-shape text edits, and speaker notes when a safe notes
body exists. Artwork is shown as placeholders; this is not exact slide render.

Backend corrections from integrating the surfaces: two Word ops on one
paragraph compose rather than overwriting one another; insert_paragraph now
uses rich runs and an explicitly requested style. The writer rejects a plain
text edit that would flatten mixed run formatting, and rejects content edits
to fields, hyperlinks, tracked changes, embedded items, or paragraphs with
distinct non-toggle run formatting. This supersedes the earlier spike's
intentional tracked-change paragraph replacement: the fidelity harness still
tests that such a damaged file is detected, but the product writer refuses to
create it. XML entities in projected text are decoded.

Excel edits preserve an existing cell's number style, reject cells with
unsupported metadata, array/data-table formulas or rich inline content, and
use per-cell compare-and-swap against original bytes. A missing cell is
addressed explicitly as `expected: absent`. Shared-formula expansion is now
limited to the edited group; token-aware translation preserves absolute
references and quoted text. Leading-zero identifiers and integers beyond
Excel's 15 significant digits stay text rather than passing through float.
Word and PowerPoint now refuse signed packages as Excel already did.

PowerPoint edits now resolve slide parts through presentation relationships,
so reordered slides edit the intended part. Shape text and notes capabilities
are projected to the UI; complex text bodies and missing or unsafe notes are
view-only. Regression tests cover reordered slides, signed packages, mixed
Word runs, shared formulas, cell formatting, blank row addresses, and unsafe
regions.

The release gate remains open: no owner files have been placed in
`runs/fidelity/`; no exact Word page or PowerPoint slide rendering, Word table
editing, full-sheet Excel editing, or PowerPoint structural slide ops exist.
Model edit proposals for file artifacts and live in-app UI verification also
remain open. LibreOffice render-diff is skipped locally when `soffice` is
unavailable; CI has the render-diff job.

The PowerPoint layout map now gets effective positions for placeholders whose
coordinates are inherited from a slide layout, and the shape writer can move
graphic frames such as tables. A roundtrip test checks that the table content
and every other package part survive such a move. The Excel API test exercises
projected cell hashes through HTTP and reopens the edited workbook.

Anchors are now unique per block ordinal, even when two Word paragraphs have
identical XML; PowerPoint anchors use the shape's actual non-visual id plus
ordinal and remain stable across a text or geometry edit. A duplicate-
paragraph regression test prevents the old hash-only anchor from addressing
the wrong block.

**Verification:** 502 pytest tests passed, 2 render tests skipped locally
without LibreOffice, 1 Starlette deprecation warning; Ruff and mypy (93
source files) passed; Svelte check had 0 errors/0 warnings; the SPA built;
four focused Word block tests passed; Rust Clippy passed. The live browser
walkthrough was not completed in this session because automatic approval
review rejected opening the test browser after an earlier stop request.
The dev servers were stopped; ports 5173, 8000 and 9333 have no listener.

## Office integration round 2: tables, windows, structure, proposals (2026-09-26)

Two interrupted handoffs were reviewed before anything was integrated.
`artifacts/excel_window.py` was **unfinished**: it did not typecheck (a
shadowed `end` name, `BinaryIO` annotations mypy rejected) and returned zero
rows for a window below the worksheet's used range, breaking its own "anywhere
on the sheet" contract. Both were fixed: the expat byte offsets were confirmed
cumulative across chunked `Parse()` calls (so the streaming index is sound),
the projection now always returns the full requested rectangle with
`expected="absent"` for blank cells, and its per-cell hashes were verified to
be the exact bytes the writer compare-and-swaps. `artifacts/word_tables.py`
was complete and correct; it was integrated rather than discarded.

**Word table cells.** `word_tables.py` gained a batch splice
(`replace_word_table_cells`) that resolves every cell against the original
bytes and splices in one pass. This fixed a real bug the interrupted work
introduced: a hash-anchored table's anchor changes as soon as one cell is
written, so a two-cell edit failed with "no table found at anchor". Cells are
projected with per-cell editability; merged, multi-paragraph, multi-run,
field-bearing and structured cells are visibly view-only with a reason.
Endpoint: `set_table_cell` (anchor + expected + row + column). Tests:
`tests/test_word_tables.py`, plus a frontend round-trip case.

**Excel full-sheet navigation.** New endpoint `GET …/excel-window` projects
any bounded rectangle of any sheet with per-cell `expected` hashes. The Excel
surface uses it when available: the address box and paging reach the real grid
(1,048,576 × 16,384) instead of the 200×40 outline, and each edit carries the
window's hash. Accurate cell *formatting* display is still not rendered.

**PowerPoint structural ops.** `artifacts/pptx_structure.py` adds the two
operations that stay fidelity-safe. **Reorder** rewrites only the
`<p:sldId>` list in `ppt/presentation.xml`; every slide part is untouched.
**Delete** removes the slide, its rels, its notes part, and media that no
surviving slide references — media shared with a survivor is kept (verified
with a fixture where both slides embed the same image). Adding a slide from a
layout is deliberately out of scope. Tests: `tests/test_pptx_structure.py`.
A visual, pixel-exact slide render was **not** added: the existing shape
layout map remains the preview, and the ignored `pptx-svelte-viewer` spike
was left unshipped because it adds ~8.45 MiB and needs an Apache-2.0 NOTICE
and an MPL-2.0 dependency review.

**Model edit proposals for file artifacts.** The JSON-artifact proposal flow
rewrites content, which is wrong for a file. `artifacts/office_edit.py` asks
the model for a small list of **anchored operations** instead ("do not return
the whole file"). Every op is re-validated against the current file in code —
an unknown anchor, an unsupported operation (adding a slide, deleting a sheet)
or a non-editable cell is refused with a reason for the student to read, not
applied. The student checks or unchecks each op; only the kept ones are posted
to the normal `office-edits` path, so the splice writer still owns the write
and every save is a version. Prompt `office_edit` lives in
`configs/prompts.toml` and goes through `grounded_prompt`; the model call is
`provider.generate("artifact_generation", …)`; retrieval reuses the JSON
flow's numbered material so citations line up. Endpoint:
`POST …/propose-office-edit`. Tests: `tests/test_office_proposals.py`
(stubbed transport; nothing is written until acceptance, and a stubbed accept
lands the op on the real file).

**Per-op fidelity verification.** `tests/test_office_fidelity_ops.py` runs
each supported edit through `scripts/office_fidelity`: parts changed ⊆ the
parts the op names, no content-bearing feature lost, file reopens. Covered:
Word paragraph/table/insert+delete, Excel cell and formula (calcChain
dropped), PowerPoint shape/reorder/delete. Hostile features (tracked changes,
comments, footnotes, customXml, charts, conditional formatting, notes) survive
every op that does not target them.

**Regression tests the handoff asked for.** Repeated shared-string references
(one string used by two cells: rewriting either is a no-op, clearing one
decrements `count` but not `uniqueCount`), and rich shared strings (multi-run
`<si>` preserved byte-for-byte while a new string is appended) — in
`tests/test_office_artifacts.py`.

**Verification performed:** 556 pytest passed, 2 skipped (LibreOffice render
tests, absent locally), 1 Starlette deprecation warning; Ruff clean; mypy
clean (97 source files); `npm run check` 0 errors/0 warnings; SPA build clean;
`cargo clippy --all-targets --locked -D warnings` clean; the frontend
Word-block test script passed 5/5.

**Release gate remains open.** `runs/fidelity/` still has no owner files
(real paper, accounting workbook, class deck) — every fidelity claim above is
on the committed synthetic and hostile corpora. The live in-app walkthrough
was **not** run in this session (no browser approval). Exact Word page and
PowerPoint slide rendering, Excel cell-format display, and adding
paragraphs/slides/sheets as structural operations remain unimplemented. No
test processes were left running; ports 5173, 8000 and 9333 have no listener.

## First owner-file fidelity pass and release audit (2026-09-26)

The owner supplied a 6.1 MB Word paper, a 13 KB two-sheet Excel workbook,
and a 2.8 MB PowerPoint deck in `example_test_mat/`. These are private local
materials and were never edited in place. `.gitignore` now excludes that
folder, the local artifact byte store, and loose Office test files in the
Tauri source tree. Test outputs and an isolated API database are under ignored
`runs/`.

**Real-file inventory.** The paper projects 171 paragraphs and one table;
145 paragraphs and 9 simple table cells are safely editable. The deck has
45 slides, 136 shapes, notes parts and 17 media parts; 30 shapes project as
editable text. The workbook has two sheets and 296 hashed cells in its
initial outlines, but no chart, pivot or conditional-formatting parts. The
paper has two tracked insertion nodes and the deck has two grouped shape
nodes; both survive edits outside those structures. These counts show meaningful
editing coverage and a substantial view-only remainder, not full Office
parity.

**Round trips.** `runs/real_office_roundtrip.py` wrote only copies, then
compared each to its original with `scripts.office_fidelity`. Real Word
paragraph replacement, paragraph insert/delete and table-cell replacement
reopened with only `word/document.xml` changed. A numeric Excel input edit
reopened with only `xl/worksheets/sheet1.xml` and `xl/workbook.xml` changed;
the latter is the calculation setting. PowerPoint shape text and move edits
changed only the targeted slide part. Reorder changed only
`ppt/presentation.xml`. Deleting the first slide reopened as 44 slides, with
no missing relationship targets; the deleted slide, its notes and rels, the
presentation list/rels, and content types changed as expected. The generic
fidelity report marks this intentional deletion as a loss, so the deletion
case was judged by surviving parts and relationship integrity instead.
No untouched package part changed in these narrow cases.

**API check.** `runs/real_office_api.py` used an isolated local database to
import each real file, apply an anchored edit, download it, reject a stale
version, and restore version 1. All three restored the exact original bytes.
This revealed that stale edits with cell/block hashes returned 422 before the
version check. `artifacts/files.py` now returns a 409 for a stale base version
before trying the writer; the repository still performs its final atomic
compare-and-swap.

**Other fixes from the audit.** The Excel grid now fetches horizontal windows
through XFD, discards stale window responses, and refreshes cell hashes after
a save. The backend Excel window iterator requests only the target rectangle;
the far-corner XFD1048576 projection and write passed. Office model proposals
are preflighted against the actual in-memory writer and require a checksum,
so incompatible or stale ops are refused before review. The semantic
fidelity checker now checks only features actually present in the original
and detects a partial drop; previously it falsely reported conditional
formatting lost from any real sheet1 without that feature.

**Gates:** 560 pytest passed, 2 LibreOffice render tests skipped locally
(`soffice` absent); Ruff and mypy passed (97 source files); `npm run check`
reported 0 errors/0 warnings; the SPA built; Clippy passed. The build has
an existing large-chunk warning. `git diff --check` passed. Browser opening
was rejected by automatic approval review after an earlier stop request;
an explicit new approval was requested, so no live UI or WebView2 walkthrough
was performed. Dev servers were stopped after the rejection.

**Release remains blocked.** The app still approximates Word pages and
PowerPoint slides, shows PPTX artwork as placeholders, does not render
native Excel cell formats/charts/pivots, and leaves complex Word and
PowerPoint regions view-only. The supplied workbook does not exercise real
charts, pivots or conditional formatting; the owner may add one later.
These checks prove narrow, fidelity-safe editing on the supplied files, not
a full DOCX/XLSX/PPTX editor or readiness for a 0.3.0 build.

## Full editor correction (2026-09-26)

The owner compared the imported lecture deck in Stacks with PowerPoint.
Stacks rendered plain overlapping text boxes on white; PowerPoint rendered
the themed backgrounds, artwork, typography, and structured text. This is a
decisive failure of the interactive editor requirement. The previous S1-S5
completion labels described safe narrow operations, not complete Office
editing. `docs/plan-notebook.md` was reset to a NO-GO full-engine plan.

Two offline OnlyOffice derivatives were inspected as candidates. The
`sok-o/officesuite` embed protocol accepts a file or buffer and returns a
saved `File`; its source has an `embedOrigin` allowlist and bundled fonts.
`agentbridges-ai/onlyoffice-browser` provides a component callback but needs
generated font assets and an independent-origin editor host. Both are AGPL;
neither has been proven on the owner's three files or in Stacks' Tauri
WebView2. The former source is sparse-checked out under ignored
`runs/officesuite_spike`; the latter has an ignored runtime spike under
`runs/office_engine_spike`. Do not bundle either before the license and
Tauri/visual proof.

The backend gained a `replace-file` endpoint so a complete editor can return
OOXML bytes as one compare-and-swap artifact version, preserving the old
version. This does not itself provide full editing. The screenshot's HTTP 500
when exporting a file to a locked or unwritable path now maps to a useful
422 response. No build or release claim follows from these changes.

## Isolated full-engine owner-file probe (2026-09-26)

The `sok-o/officesuite` source at `8c85c60` was installed only under ignored
`runs/officesuite_spike` (its public runtime is 442.2 MiB). Its automated
real-file corpus test passed open, edit, and save on all three owner files.
A first-slide screenshot shows authored PPTX backgrounds, artwork, and a
complete presentation toolbar, which is substantially closer to PowerPoint
than Stacks' current shape map. Font weight is still visibly different.

The same candidate fails a stricter no-edit save comparison. The owner's deck
was 2,813,448 bytes with 45 slides, 136 shapes, 17 media files, 10 embedded
font files, 45 notes parts, and a comment-author part. The candidate's saved
file is 1,620,060 bytes with 45 slides, 140 shapes, 13 media files, no
embedded font files, 45 notes parts, and no comment-author part. The four
missing images were referenced from slides 5, 11, 13, and 25 in the original.
Python-pptx can reopen the result, but reopening alone did not detect that
data loss. This candidate cannot be used as the production writer as tested.
The backend `replace-file` API accepted and versioned these output bytes and
rejected a stale save; that proves only the storage bridge, not fidelity.

## Pivot: Stacks as an Office add-in (2026-09-26)

The Office-editing effort changed direction, and `docs/plan-notebook.md` was
rewritten around the new one (it now opens with "Bring Stacks to Office").

**Why.** Third-party engines (OnlyOffice, then native Collabora Office 26.04)
must translate an OOXML file into their own document model and re-serialize on
save. Measured on the owner's real files, a **no-edit** save destroyed
committed content — 24 equations to 0, 10 embedded fonts to 3 (survivors were
Collabora brand fonts), all 45 speaker-notes parts and the comment-author part
dropped, four images re-encoded — on the PPTX, and tracked changes plus
customXML on the DOCX. The saved files still reopened in real PowerPoint,
Word and Excel with no repair warning, which is exactly why reopen-based
checks never caught it. An editor that round-trips through a foreign model
cannot preserve what that model does not represent; the loss surface is
whatever lies outside the editor's model, and it is unbounded. Collabora's
native Windows build was tested deliberately (not Docker) because Docker/WSL/
a VM cannot be a runtime requirement for a consumer desktop app.

**New direction.** Stacks does not become Word/Excel/PowerPoint. Microsoft
Office keeps the document and performs every mutation; Stacks becomes the
intelligence layer beside it, through Office task panes. Stacks never
serializes an Office file, so an unsupported feature becomes a missing Stacks
capability instead of a document-corruption risk. The existing custom Office
editors are frozen, not deleted; they remain useful as previews and for
environments without Office.

**What was built (first spike scaffold).** A backend Office bridge
(`src/backend/api/office.py`) mounted at `/office` — a separate local trust
boundary with its own token (`APP_OFFICE_TOKEN`) and origin allow-list
(`APP_OFFICE_ORIGINS`), because an Office task pane is a web page Microsoft
Office loads and cannot hold the desktop shell's per-launch token. It exposes
`GET /office/health` and `POST /office/process-selection`, the latter
returning `STACKS TEST: <text>` per the plan. A PowerPoint task-pane add-in
lives in `src/office-addin/` (manifest, task pane, tested pure logic in
`bridge.js`), with a development HTTPS server at
`scripts/office_addin/serve.mjs`. Tests: `tests/test_office_bridge_api.py`
(7), `tests/test_office_addin_manifest.py` (manifest structure and that every
referenced file exists), and `node --test src/office-addin/bridge.test.js`
(8).

**Gates:** 584 pytest passed, 2 skipped (LibreOffice render, absent locally),
1 Starlette deprecation warning; Ruff clean; mypy clean (98 source files);
`npm run check` 0/0; SPA build clean; `cargo clippy -D warnings` clean.

**Still open.** The live PowerPoint sideload onto the owner's 45-slide deck
(the decisive check in the plan's first spike) was not run. `process_selection`
is still the echo seam, not real retrieval. Word and Excel hosts are not
wired. Nothing was committed; the existing uncommitted work is preserved.

**Also this session:** `scripts/office_fidelity.py` gained a relationship-
graph, content-addressed asset check (matched by sha256, not filename) that
catches an editor deleting a part *and* its relationship — the blind spot that
let the earlier no-edit saves pass. A Collabora spike worktree
(`../stacks-collabora`, branch `spike/collabora-office`) holds the isolated
evidence and its own `docs/collabora/` write-up; the main tree is untouched by
it.

## Retiring the file-backed Office editors (2026-09-27)

Following the pivot to "Office owns the file" (entry above), the frozen
file-backed editors are removed from `main`. Removed: `artifacts/splice.py`,
`artifacts/office.py`, `artifacts/files.py`, `artifacts/office_edit.py`,
`artifacts/word_tables.py`, `artifacts/pptx_structure.py`,
`artifacts/excel_window.py`, `common/artifact_files.py`, migrations
`006_office_artifacts.sql` and `007_artifact_version_ops.sql`, the frontend
`WordEditor`/`ExcelEditor`/`PowerPointEditor`/`OfficeArtifact` components and
`wordBlocks.ts`, the fidelity/roundtrip scripts, `data/eval/office/`, and the
seven frozen-editor test modules. Office file-kind fields (`file_sha256`,
`filename`, `file_size`, `mime_type`, `ops`) are gone from the artifacts repo
and its SQL; the archive format stays at v2. The `word`/`excel`/`powerpoint`
artifact kinds are gone from `content.py`, `KINDS`, `DEFAULT_TITLES`, and the
generated OpenAPI types.

The JSON artifact kinds were kept and repurposed as lightweight study tools:
`doc` → course notes, `slides` → study decks, `sheet` → schedules / trackers.
Display labels and blank titles follow (`KIND_LABELS`, `DEFAULT_TITLES`,
`ArtifactsPanel` hints). These are typed JSON generated, cited, and edited
inside Stacks; they never represent a real Office file.

**Safety net.** All removed work plus the first add-in spike was committed on
a new `old-office` branch (`3a806c6`) before deletion, and the untracked
editor code was copied to `runs/attic/office-editors/` (with the full tracked
diff at `runs/attic/tracked-changes.patch`). `main` kept every unrelated
uncommitted change — the doc-reference cleanups, the resizable-panel
refactor, the title-only rename route/repo method, and the entire Office
add-in. The branch and attic are to be pruned once this direction is proven.

**Gates after retirement:** 436 pytest passed, 1 Starlette warning; Ruff
clean; mypy clean (90 source files); `npm run check` 0/0; SPA build clean;
`cargo clippy -D warnings` clean; `node --test src/office-addin/bridge.test.js`
8/8; add-in bridge/manifest tests 14 passed.

## Office assistant: grounded pane actions + redundant readers (2026-09-27)

The "big one" from the pivot: Stacks as an assistant living inside the
student's Office applications. Built the whole reasoning path behind the
PowerPoint task pane, host-parameterized for Word/Excel.

**Grounded actions.** `src/backend/tutor/office.py` answers explain / find /
quiz / summarize. It reuses the existing seams — `funnel.retrieve` for
course material, `provider.generate` through `tutor_answer` for the call,
`trace.record_trace` for the audit record — so a pane answer is as grounded
and inspectable as one from the built-in tutor. It returns real citations
(chunk text + locator + filename), refuses graded work with the tutor's own
classifier, and refuses empty retrieval (Fork B). Four prompts added to
`configs/prompts.toml` (`office_explain/find/quiz/summarize`, prompts
version 13 → 14) and registered in `prompt_registry.OFFICE_PROMPTS`.

**Redundant readers.** New `src/backend/office_reader/` reads a document by
several independent methods and merges them, keeping the redundancy
inspectable rather than hiding it: `package.read_package` (unzip a
`.docx`/`.xlsx`/`.pptx` in memory, read paragraphs / cells / slide text —
read-only, never re-serialized), `screens.read_screens` (OCR PNG renders via
the same multimodal `ocr` seam the ingest pipeline uses),
and the host's own `scrape`. `merge.merge_reads` unions them in document
order and reports per-method agreement ("agreed on 12 of 14 units"), so a bad
read in one method is caught by another. The OCR page-splitter was extracted
to `src/backend/ingest/ocr_pages.py` so ingest and the Office reader share
one implementation.

**Bridge.** `src/backend/api/office.py` gained `GET /courses`,
`POST /assist`, and `POST /read` (the merge), all behind the Office token
when configured; the first-spike `POST /process-selection` echo is kept.
The task pane (`src/office-addin/`) is now a real assistant: course picker,
the four actions, an optional question, an answer with rendered citations,
and Insert/Replace through Office.js; the echo round trip moved under
"Connection test". `bridge.js` gained `listCourses`, `sendAssist`,
`assistRequest`, `assistError`, `citationLines`, multi-host `writeSelection`.

**Tests:** `tests/test_office_assistant.py` (11), `tests/test_office_reader.py`
(11, with synthetic in-memory OOXML fixtures — no external libs, no real
files), `tests/test_office_bridge_api.py` grew to 17; `node --test
src/office-addin/bridge.test.js` 16. Gates: 468 pytest passed, 1 Starlette
warning; Ruff clean; mypy clean (97 source files); `npm run check` 0/0; SPA
build clean; `cargo clippy -D warnings` clean.

**Still open.** The live PowerPoint sideload onto the owner's deck — the pane
UI and its Office.js wiring have not run in a real host (the pure logic is
Node-tested; the wiring is not machine-tested). The pane does not yet send
screenshots or package bytes to `/office/read` (only the scrape); the
endpoints already accept them. Speaker-notes insertion is not wired.

## One-click Office: the backend hosts the add-in (2026-09-27)

**Problem.** The add-in could not actually be launched. The pane called
`http://127.0.0.1:8000`, but the desktop backend binds a random port; the
Tauri shell started a "dev server" through a PowerShell script pointing at a
file that did not exist, serving plain HTTP (Office refuses it); the register
script wrote a `TrustedCatalogs` entry with an HTTPS URL (catalogs are
network shares — Office ignores it); every path was `CARGO_MANIFEST_DIR`, so
none of it existed in an installed build; the manifest test still pinned
PowerPoint only; and the pane called `PowerPoint.getSelection()`, which is
not an Office.js API. Separately the home page 500'd on the owner's database:
the retired builds left an `excel` artifact row the list endpoint could not
validate, and schema_migrations still records 006/007.

**Decision.** The backend is the add-in host (`src/backend/office_addin/`).
Connect = issue a `localhost` cert from a throwaway CA whose key is
discarded; `certutil -user -addstore Root` (one Windows dialog); render the
manifest for the port and app version; register it under
`HKCU\Software\Microsoft\Office\16.0\WEF\Developer` (the per-user developer
add-in key Office's own tooling uses; works for Word, Excel and PowerPoint);
serve pane + bridge same-origin over HTTPS from a thread in the backend on a
fixed port (47831, `APP_OFFICE_PORT`). Same origin removes CORS
(`APP_OFFICE_ORIGINS` is gone) and port discovery. Startup re-serves a
connected add-in but never prompts; expiry or lost trust shows as "Needs
attention" with a Repair button. The Rust shell is back to its committed
state (no Office commands); `scripts/office_addin/` and `docs/office-addin/`
are deleted (the usage story is in plan-notebook.md "Using Stacks in
Office"). Rejected: hosting the pane on a public site calling loopback
(Chrome's local-network-access prompts in Office's webview, and a
dependency on a website for a local-first app); a Node server (a second
runtime to ship and launch).

**Pane.** One adapter per host: Word (`getSelection`, cursor → its
paragraph; insert paragraphs after, replace), Excel (selected range →
address + values + formulas, capped at 100×20 cells; insert as a comment on
the active cell, else a "Stacks notes" sheet; replace the active cell),
PowerPoint (selected text, else every text shape on the selected slide;
insert a text box; replace the selected text), each with the Common API
(`get/setSelectedDataAsync`) as the fallback. The pane follows selection
changes, remembers the course per document, and prefers the course Stacks
opened Office from (`/office/courses` `suggested`). Inserted text is plain
text plus a "Sources:" list of the citations it uses, so grounding travels
into the document. The echo "Connection test" is gone from the UI (the
endpoint stays).

**App.** Settings → Microsoft Office (status, Connect/Repair/Disconnect,
Open Word/Excel/PowerPoint) and a course's **Open in Office** menu (new
document or a picked file; connects first if needed; tells the user where
the button is and that an already-open app needs one restart the first
time). API: `/api/office/status|connect|disconnect|open`.

**Data.** The artifact list skips kinds outside `KINDS` instead of 500ing;
`migrate.RETIRED_VERSIONS = {006, 007}` with a test that no migration file
reuses them.

**Evidence.** A real `AddinHost` serving over TLS verified against the
issued CA (`localhost`, `127.0.0.1`, `::1`), port-in-use reported, restart
on the same port. Tests: `tests/test_office_addin_setup.py` (certificate,
connect/declined trust/repair/disconnect/startup with a fake Windows layer,
opening documents, the API, the real TLS host); manifest tests now cover all
three hosts and the rendered copy; `node --test
src/office-addin/bridge.test.js` 12.

**Still open.** Nothing here has run inside real Office: the WEF\Developer
registration, the certificate prompt, and the Office.js adapters are built
to the documented behaviour but only a run on the owner's machine proves
them. Keep Stacks open while using the pane (it is served by the app).

## Office add-in verification and repo cleanup (2026-09-27)

**Verification added.** The pane's JavaScript is now type-checked against
Microsoft's `@types/office-js` (`src/office-addin`: `npm run check`, in CI).
It caught a real bug: Word's Insert set `Word.Style.normal`, which does not
exist (the enum is `Word.BuiltInStyleName`), so Insert in Word would have
thrown. Every other call (PowerPoint `getSelectedTextRangeOrNullObject`,
`getSelectedSlides`, `shapes.addTextBox`; Excel `comments.add`,
`getActiveCell`, `getResizedRange`; Word `insertParagraph`) type-checks.
Microsoft's `office-addin-manifest validate` rejected the manifest version
(`0.x` is below the required 1.0); the registered version is now
`1.<major>.<minor>.<patch>` (monotonic, tested), and the rendered manifest
validates. An end-to-end run of the real `serve.main` (only trust store,
registry and app launch faked): the app API needs the launch token; Open in
Office connects, registers and launches; the pane is served over TLS and
sees the course as suggested; tooling files are not served; `/office/assist`
refuses honestly with no sources (404, ~27 s on a fresh data folder: the
encoders' first load); closing stdin stops the backend and frees the port.
The add-in icons were placeholder squares; they are now the app icon.

**Layout.** Shipped pane files moved to `src/office-addin/public/` (the only
directory served and bundled); `src/office-addin/` holds `package.json`,
`tsconfig.json` and `bridge.test.js`. The Office types live there, not in the
SPA, so `Word`/`Excel` globals never leak into the app's own type-check.

**Cleanup.** `.gitignore` ignores all of `data/` except `data/eval/` (a
dev-checkout Connect writes the add-in's private key to `data/office-addin/`,
which the old per-folder rules would have exposed), plus `images/` and
`node_modules/`; the old `src-tauri/*.docx` rules and the stray empty
"Untitled document.docx" the retired Word editor left there are gone.
`runs/attic/` was deleted after checking all 50 files are byte-identical to
the `old-office` branch. `artifacts_repo.rename` moved its inline SQL to
`queries/artifacts.sql` and gained a test; the OCR splitter alias in the
orchestrator is gone. `plan-notebook.md` was rewritten as the current plan
(start here, how it works, code map, the owner's first-run checklist, next
steps, open questions).

**Old editors removed for good** (owner, same day). The `old-office` branch
(`3a806c6`, local only, never pushed) is deleted; `git branch old-office
3a806c6` recovers it until git prunes unreachable commits. From the dev
database, the 8 artifacts the retired editors had created (their test files:
`hostile.docx/.xlsx/.pptx`, "Untitled document/workbook/presentation", the
"UI paper" sample, all re-kinded to doc/sheet/slides but still holding the
old block format) were deleted with their versions; the 3 real artifacts are
untouched. Also deleted: the old file store `data/artifacts/` and the stale
`build/` and `dist/` installer output. The nullable `file_*`/`ops` columns
those builds added stay in old databases, unused and harmless; migrations
006/007 stay reserved.

## Dockable desktop companion (2026-09-27)

**Problem.** The Office add-in could launch Word, but the product still gave
the owner no obvious everyday surface. Finding a ribbon button and opening a
task pane felt like setup work rather than a companion. The intended experience
is closer to the ChatGPT Windows sidebar: visible beside the current app,
available outside Office, and easy to collapse.

**Decision.** The Tauri app now starts with a 420 px `companion` window snapped
to the right Windows work area. It can stay on top, collapses to a 56 px edge
tab, opens the existing full library on demand, and owns the explicit Quit
action. The full `main` library window starts hidden and hides on close. This
keeps one cross-app interface while preserving the Office task pane as the
live document read/write bridge. A true Windows AppBar that reserves desktop
space is deferred until daily use shows the always-on-top window is inadequate.

**Product flow.** The companion chooses a course, accepts pasted context from
Word, a browser, a PDF, or another app, and offers Explain, Find in course,
Quiz me, Summarize, and a free question. `/api/companion/assist` delegates to
the existing Office assistant pipeline so citations, empty-retrieval refusal,
and the graded-work fence stay identical. Conversation UI is session-local;
answers and citation traces retain the backend's existing behavior.

**Verification.** The Svelte and Rust sides compile. A browser click-through
at the real 420 x 800 dock size verified course loading, disabled/enabled
actions, collapse/restore, and error rendering. That pass caught and fixed a
Svelte reactivity bug where a completed failed request stayed visually stuck
on "Reading your course" because a turn object inside the array was mutated in
place. Turn updates now replace the array item immutably. The remaining owner
gate is a grounded answer with a ready model/course and live Office selection,
insert, and replace.

## Companion release cleanup (2026-09-28)

**Direction confirmed.** After the live Windows preview, the owner approved the
docked companion as the main interface. The full library remains the management
surface and the Office add-in remains an optional document bridge. The current
plan is now a release gate rather than an Office feature backlog.

**Documentation reset.** `system.md` was replaced with the current local
SQLite/Tauri architecture; it no longer interleaves retired hosted/Postgres
design. The root and frontend READMEs, `project.md`, `AGENTS.md`, changelog, and
environment template now describe the companion-first product. `docket.md`
keeps its original static-review findings but begins with a dated triage table,
so unverified candidates are not mistaken for reproduced blockers.

**Release hardening.** Version changes now update and test both npm and Cargo
lockfiles. Missing lockfiles and missing installer output fail loudly. Release
builds use Cargo's locked graph. Direct `lxml` dependencies are declared. The
library and companion have separate Tauri capabilities, external URL opening
uses the plugin's scoped default permission, and backend startup errors identify
the correct stderr log. The desktop API refuses production requests without a
launch token, non-ASCII token input returns 401, and the mounted API publishes
no documentation routes. Exported chart HTML is placed in a sandboxed iframe
under a restrictive CSP; the old regex filters remain defense in depth.

**Build proof.** The full PyInstaller backend and NSIS path produced one 83 MB
`Stacks_0.2.0_x64-setup.exe`. That run exposed an incorrect `--locked`
placement in the npm/Tauri command; the runner argument is now passed after
Tauri's separator. Installer output is cleared first and the script requires
exactly one artifact, preventing a stale installer from looking like a new
successful build.

**Still required.** The companion files and their references must be committed
together. A clean install/uninstall smoke test, grounded companion run, and
Word/Excel/PowerPoint read-write-save-reopen pass remain the release gate.

## Library-first cross-platform correction (2026-09-28)

**Direction corrected.** The owner rejected the companion-first launch model.
Stacks is a centralized course, knowledge, memory, and saved-work application;
the companion is an optional tool the user opens from that home. The previous
decision to launch a fixed Windows edge dock is superseded.

**Lifecycle.** Tauri now creates only the visible library at startup. The
library's persistent navigation invokes `show_companion`, which dynamically
creates one companion webview and focuses the existing one on repeated calls.
The companion uses native decorations, can move and resize, defaults to normal
z-order, and may still be pinned on top. Closing it destroys only that window;
closing the library exits Stacks and shuts down the supervised backend. A second
app launch focuses the library.

**Platform baseline.** The prior `v0.3.1` Actions run already built a Windows
NSIS installer, Apple Silicon DMG, Linux AppImage, and Linux deb successfully.
The shared CI gate now runs backend and Tauri/frontend checks on Windows x64,
macOS arm64, and Linux x64. Release jobs assert the runner architecture before
packaging. The Office bridge remains Windows-specific; the library, companion,
and local-model paths are platform targets in their own right.

## Cross-platform lifecycle CI proof (2026-09-28)

The first three-platform CI run exposed a direct reference to Windows-only
`subprocess.CREATE_NO_WINDOW` in shared Python runtime code. The launch path now
uses a guarded lookup, and mypy passes locally for `win32`, `darwin`, and
`linux`. CI run `36490786861` then passed all six backend and frontend/shell
jobs on Windows x64, Apple Silicon macOS, and Linux x64. The packaged Windows
shell also started its authenticated backend and shut that backend down when
the shell process closed.

Release workflow run `36492354123` subsequently built the current tree on its
native runners and uploaded `stacks-0.3.1-macos-arm64` (82,351,725 bytes) and
`stacks-0.3.1-linux-x64` (309,789,035 bytes) workflow artifacts. Attachment to
the existing GitHub release was disabled; installed UI smoke testing remains a
separate gate.

## Usability review and live-model evidence (2026-09-29)

Reviewed generation, retrieval, tutor framing, workspace parsing, chat and
Office state, and evaluation reliability with three Luna agents. Source filters
now run before retrieval limits; Office uses the common reranker. Explicit
explanation requests are no longer overridden by assignment text being read.
Unavailable saved providers do not silently fall through to another endpoint.
Malformed/truncated/empty model output and stale UI responses are handled at
their boundaries, and informational questions do not request artifacts merely
because they mention notes or slides.

The first live MiniCPM5-2B run scored 9/10 mechanically while producing a false
quiz answer and incorrect code. Mechanical evaluation is now labeled as a
contract check, its command fails on unsuccessful cases, and it supports narrow
content assertions. It is not a semantic correctness gate. See
`docs/usability-review.md` for verified fixes, real-model evidence, and the
remaining acceptance work; no arbitrary relevance threshold was introduced.

## Code cleanup and unresolved lifecycle decisions (2026-09-29)

Consolidated repeated course existence checks, made retrieval normalization
preserve frozen candidate objects, and fixed retrieval evaluation to include
all locators spanned by a chunk. Removed verified unused helpers and a duplicate
compression path; storage tests now exercise production compression and restore
their temporary-directory state. Office token comparisons use UTF-8 bytes.

The existing dirty working tree was the baseline and its feature work was
preserved. No migrations or model/prompt changes were introduced. Draft recovery
versus blocking navigation, single-server scheduling versus concurrent local
models, and native versus backend file authority remain explicit decisions;
evidence, costs, and recommendations are in `docs/code-health.md`. The older
candidate ledger was cross-checked against current code rather than treated as
proof; retired migration-number guards and used PDF extraction parameters
remain intact.

## Behavior review: saving the visible workspace draft (2026-09-29)

Following the generate → edit → save → reopen journey found that workspace
edits were local while Save to artifacts copied the original stored model
response. The app could report a successful save and then reopen different
content. The save request now includes the editable draft; the backend preserves
kind, title, evidence numbering, and origin from the stored answer, validates
the revised content, and attributes actual edits to the student. The original
chat remains unchanged.

Saved notes, slides, and table rows are checked after reopening through the API.
An actual browser interaction also edited notes, saved them, reloaded, and
reopened the persisted edits in a disposable course. No live model was called
for that persistence check. `docs/flow-review.md` records the higher-level
contracts and remaining questions about source scope across chat history,
generated drafts versus adopted artifacts, and source support versus synthesis.


## Adaptive learning and background memory (2026-09-29)

The user explicitly chose background adaptation with optional inspection. COURSE
memory records topic-specific understanding; CORE memory records presentation
preferences and method observations across courses. Neither authorizes factual
claims from excluded material. Ordinary conversation creates tentative checks
with quoted evidence, rather than capability judgments.

Migration 008 implements complete multiple-choice suites/sessions, immutable
questions and answers, separate key corrections, durable distilled observations,
a bounded experiment docket, and CORE method observations. Numbers 006/007 remain
reserved. `course_memory.refresh` is still the course-memory write seam. The
versioned baseline uses conservative evidence caps, explicit unknown capabilities,
reveal-aware repetition, assisted evidence weighting, and practice cooldowns.
These are inspectable estimates, not validated learning measurements.

Raw session deletion retains observations. Forgetting is a separate explicit
memory control. Key correction updates COURSE and existing CORE outcomes and
reopens dependent experiments. Source snapshots support inspection after source
changes and deletion. Course export/import includes practice history and remaps
source/suite/run links, without exporting or duplicating global CORE state.

Background research uses the conversation's chosen provider through the existing
model seam, requires an exact quote and valid citations, and fails open without
losing the saved answer. The app controls quotas, expiry, and experiment checks;
the model cannot directly write capability scores or arbitrary root instructions.
The library and saved artifact practice UI share the same persistence path.
Verification and material limits are recorded in `docs/learning-memory.md`.


## 2026-09-29 — Companion work sessions and memory isolation

The companion assists with an external document, with Stacks supplying course
references and existing presentation preferences. Migration 009 stores separate
work sessions, immutable document snapshots, and version-bound conversation
turns. Papers, slides, reference material, and practice worksheets are raw
working context. No document or generated review automatically updates course
knowledge, learning experiments, CORE observations, preferences, or test scores.
Formal practice remains an explicit separate assessment path.

Files, paste, a Windows accessibility/window-render reader, and an Office
whole-document publication action feed the same session contract. Capture scope
is exposed; pixels cannot establish whole-document coverage. Edits are proposed
for review and copying; arbitrary external writes and continuous monitoring are
outside this baseline. See `docs/companion-work.md` for behavior and open limits.

Live checks exposed critique-only revisions and a review that attributed course
evidence to the student. Review findings now require exact draft passages;
revisions require an exact original, nonempty changed replacement, and reason.
Both remain proposed advice, with source markers validated, not proof of factual
entailment. The final local model still misdescribed an original claim in an
otherwise useful edit explanation; semantic quality remains open.

Archive imports never retain original IDs for missing work citation mappings.
They keep the saved quote, clear the unavailable source/trace link, assign new
snapshot IDs, and label disconnected sources in the interface. Imported replies
must agree with their saved document revision.

## 2026-09-30 — Office readers and typing-triggered refresh

Use host Office.js APIs to read the pane's own document. Word body, all Excel
worksheets with formulas/display values, and all supported PowerPoint slide text
feed saved work sessions with explicit partial coverage. A bounded, ephemeral
broker serves refresh commands when companion composition starts; it never polls
document contents continuously or writes learning memory. Exact identity/revision
checks and idempotent completion prevent wrong-course and late-read replacement.
Unchanged snapshots retain their revision and timestamps. Offline reads preserve
draft questions and require an explicit saved-snapshot choice before answering.

Office reconnection looks up the latest session revision and rejects a different
document identity. Mac setup uses user container manifests and the login keychain;
its commands and service flow are mocked, with native Office testing pending.
Explicit screenshot upload reuses OCR and keeps partial scope on either platform.
Google files use exports; live Google authentication and native Mac window capture
are outside this pass. See `docs/office-live.md` and the prior scoped plans.

## 2026-09-30 — Keep one backlog and retire completed task documents

Consolidated eight temporary reports/handoffs/plans into the six working docs:
`review.md`, `code-health.md`, `flow-review.md`, `usability-review.md`,
`learning-memory.md`, `companion-work.md`, `office-live.md`, `office-live-plan.md`.
The files were removed after retaining behavior contracts in system sections
12–14, acceptance/quality gaps and review candidates in docket, and current
validation/release gates in plan-notebook. Earlier mentions of those paths here
are historical; tracked originals remain in Git history. No archive directory
or replacement per-task reports were created.

Cleared original docket IDs (completed, superseded, or disproven as written):
C-01, C-25, C-28, C-29, C-68, C-78, C-99, D-01, D-02, D-03, D-04, D-05, D-07, D-10, D-16, D-18, D-21, D-24, P-08, P-13, R-01, R-03, R-07, R-08, R-10, R-11, R-12, S-02, S-03, S-05, S-06, T-01, T-02, T-05, T-06, T-07, T-17, T-18, T-19, T-24, T-32, X-33, X-34.
Partial findings remain narrowed. MODEL_TASKS and raw_pdf_bytes are used;
retired migration numbers have a safeguard test; companion files are tracked;
student learning/companion paths now have substantial tests. The second review's
correct-behavior entries L5/L7/L19/L20 were cleared; remaining IDs are mapped or
qualified under REV in docket. Clearing a report is not claiming its open
quality/native checks passed. Durable drafts, source-scope history, draft
adoption, model scheduling, authority, generated quality, learning calibration,
and native/live-host verification remain explicit B-01 through B-13 work.

Current product documentation now reflects the existing one-vector-per-chunk
embedding replacement contract. Applied migrations were not edited. This was
documentation-only work; all pre-existing implementation changes were preserved.
Future task closure deletes its temporary file after transferring only lasting
facts and remaining work; current handoffs are replaced, not appended forever.

Disposition clarification: D-16 is cleared only as originally worded. Its unused
UPDATE_TOC/EXTRACT_KNOWLEDGE mapping entries remain a narrowed candidate; the used
OCR mapping and PDF argument must stay. D-20 no longer asks to remove the already
deleted marker, and D-22 requires credential migration rather than a branding rename.

## 2026-09-30 — Saving identity, draft recovery, and local backup tiers

User chose optional local backups: full keeps saved academic data; partial keeps
sources/materials/memory and omits chats; heavy keeps sources/materials and omits
learning memory/results/preferences. Retention tier and compression strength are
independent. Initial retained-content compression is lossless; lossy visual
transformations remain B-14. Disabled scheduling preserves existing copies.
Permanent live deletion does not remove historical backups.

Generated message items adopt one versioned material on first save, with explicit
copies separate. Migration 010 adds identity without guessing legacy provenance;
portable chat export/import retains and remaps message IDs. Saves serialize and
guard dependent actions; model acceptance retains its version and citation intent.
IndexedDB draft recovery is always available independently of optional backups,
but belongs to the WebView/browser and is not included in backend archives.

Backups use an online SQLite snapshot plus verified raw sources. Reduced copies
must VACUUM after deleting rows: removed chat text otherwise survives in free
database pages. Restore validates before publishing a new folder, clears machine
settings/claims, preserves saved citation passages, and queues omitted vectors
for rebuilding. Live database replacement is deliberately separate; desktop
activation/rollback and native crash/close acceptance remain B-14/B-10.

The save/recovery implementation closes B-01/B-03; X-31 is addressed by disposal.
F-09's suggestion to close despite a failed save was rejected: preserving the tab
and draft is the chosen behavior. F-06/F-27 remain independent candidates.
Browser acceptance exposed reactive metadata that could not be cloned for draft
storage despite helper tests passing; verification must exercise the actual
client store and visible failed-save/reload journey.


## 2026-09-30 — Quiz help and content opinions

Hint delivery persists assistance by question fingerprint; a reloaded client
cannot claim independent success by changing its attempt ID or help checkbox.
Inference holds no writer transaction, and submission/late delivery/changed-key
races are guarded. Explain uses completed answers and the current source-backed
assessment; excluded keys are withheld. Generation and content opinions do not
create capability or CORE evidence. Editable ratings refer to the question or
specific generated output and enter future library quiz prompts only as bounded,
source-scoped weak opinions. Rating content and disputing an assessment are
separate actions. Ready help/feedback travel in course archives and full/partial
backups; heavy retention omits them. Live originating chat model choices are
honored without saving provider configuration into academic archives.

Real local probes exposed answer-revealing hints, citation-format omissions, and
assessment/policy instruction echoes. Prompt 31 narrows hint generation and splits
ordinary/excluded explanations. Source numbers are validated and rendered rather
than assuming the model formats them reliably. Remaining semantic quality is
B-06; successful persistence and mechanical checks do not establish pedagogy.

## 2026-09-30 — Interactive maps use the existing course and practice paths

Maps are versioned study artifacts with colored branch forests and separate
comparison edges. Branch membership controls focus and quiz scope; comparison
neighbors never become children merely because they are related. HTML controls
over SVG paths give keyboard/mouse access, and layout distance remains a visual
heuristic. Explain is transient; generated quizzes save through normal artifacts
and deliberate suite submissions are the only learning-write path. Request IDs,
origin versions, source exclusions, live chat model choices, and archive remapping
keep these actions connected to their course and evidence.

Actual MiniCPM5 2B output invented umbrella topics and classified student/labor
events as figures. Prompt 35 and application-selected literal excerpts replace
model-written descriptions and relationship captions with inspectable evidence
and neutral labels. This prevents invented descriptions, not incorrect inferred
structure: exact co-occurrence does not establish entailment. The latest map
under-covers named examples, and Explain/quiz semantics remain B-06. Map generation
must not turn its own output into student preferences, hypotheses, or capability.

## 2026-10-01 — Course graph design: chunks are nodes, concepts annotate

The mind map's underlying graph never populated: `extract_knowledge` is a
recorded skip (`ingest/orchestrator.py:300`) and `MindMapContent` is generated
per chat message, so `concepts`/`dependencies` have no writer and no course-wide
structure exists. Chosen design (full plan in `plan-notebook.md`):

Nodes are chunks, not concepts. Chunks are already the embedded unit and are
L2-normalized, so similarity is real passage cosine. A separate concept
embedding was rejected: a generated vector space lets non-concepts become
concepts and crowds the map. Concepts are annotations over chunks and must have
at least one chunk. Edges are weighted chunk↔chunk cosine kept by minimum
strength, not top-k, so a hub like "derivative" retains every strong link.
Hierarchy comes from agglomerative clustering (scipy); nested clusters are
families and subnodes, and each family's core chunk is its medoid. TOC-preferred
and uniform clustering are both implemented and compared on results and cost.

Similarity edges, cluster membership, and labels are `hypothesis`-level and
shown as inferred; only TOC-derived structure is grounded. Cohesion and bridge
outlier flags surface possible misplacement for inspection but never rewrite
the graph, and no flag claims objective wrongness. This changes product
language in `project.md` and `system.md`, which currently say map
position/comparison implies nothing. Tracked as docket B-15; the dependency
seam becomes live once concept_chunks links concepts to chunks and fixes the
concept-to-whole-source precision gap.

First pass landed (same day) on `codex/full-chat-review`: migration 013
(`graph_edges`, `concept_chunks`, `graph_clusters`, `graph_cluster_members`),
`configs/graph.toml`, `backend/graph/` (edges, clustering, concepts, view),
`extract_knowledge` replaced with per-chunk concept annotation, the dependency
seam re-pointed at `concept_chunks`, prompt 36 for the extractor, and
`GET /courses/{id}/graph` (frontend types regenerated). Gates passed: 790
backend tests, ruff, strict mypy across 140 files. Deliberately open: the two
clustering modes are not yet compared on a real course, cluster labels are not
LLM-written (TOC title or null), the mind map UI still renders per-message maps,
and the `project.md`/`system.md` similarity language is unchanged. Concept
extraction is enrichment (a missing provider skips it); the dependency seam now
reads `concept_chunks`, so a concept with no chunk is not retrievable.

**Same day, Q1 decision.** The user chose to pull concept tagging out of the
ingest pipeline: upload stays model-free and deterministic (extract → chunks →
embeddings → similarity edges → clusters), and tagging runs as a separate,
re-runnable job (`graph/jobs.py`; `POST /courses/{id}/graph/tag`, status at
`GET /courses/{id}/graph/tag`). `extract_knowledge` is again a recorded skip.
Rationale: hundreds of model calls must not gate uploads, and improving the
prompt should be a re-run, not a re-ingest. The job replaces a course's concepts
atomically, so a failure leaves the previous tagging intact. In doing so the
same-chunk dependency bug was fixed: `depends_on` now resolves against the
whole course vocabulary (`replace_course_concepts`), so `chain rule → derivative`
is written even when the two are named in different chunks. Note the user's
framing that emerged in discussion: this is a **similarity taxonomy**
(big topic → methods, e.g. derivatives → power/chain/product rule), NOT a
prerequisite tree; `depends_on` belongs to a separate requisites architecture and
is not what drives the map. Remaining: cluster labels, top-level roll-up
(connections only between big nodes, children inherit the parent's), and the
graph as a soft fused fifth retrieval seam. Gates after Q1: 794 backend tests,
ruff, strict mypy across 141 files.

## 2026-10-01 — RAG replacement drops the prerequisite graph

The user clarified that "binary graph" referred to prerequisite relationships
and explicitly cut that architecture. The replacement keeps source containers,
coherent passages, ordered neighbors and similarity links. The encoder selects
nearby supporting context at retrieval time; no prerequisite or supporting-context
dependency graph is persisted. The tutor will tentatively suggest background
review and practice through the existing prompt registry. Suggestions alone do
not change COURSE proficiency or CORE preferences. `plan-rag-storage.md` owns the
active implementation plan, including retirement of existing dependency retrieval
and `depends_on` extraction. This is a planning decision; runtime removal and
the prompt change have not been implemented.


## 2026-10-01 — Passage RAG implemented; old knowledge stores retired

Completed the source-backed SQLite passage architecture and conversational
prerequisite-review prompt. Original IDs/evidence, learner records and old
annotations survive migration; indexing does not refresh student memory.
Legacy annotations are saved study artifacts, with generated lookup opt-in and
original-support scope checks. Parent/order and bounded inferred similarity
replace TOC/clustering/dependency subsystems; no separate graph engine was added.
Copied-database acceptance exposed and repaired retired Office fields during
migration 012, preserving their metadata in exported artifact provenance.
The temporary RAG plan and superseded graph milestone are retired. Original TOC
candidate entries C-04/C-21/C-22/C-32, P-09/P-11/P-14, T-12 and dead-package/mapping
entries D-12/D-15/D-16 were removed with their retired paths. Broader retrieval and generated
answer correctness remain B-07/B-06; green mechanical checks do not close them.

## 2026-10-02 — Code coherence after the rebases

Removed runtime scaffolding for obsolete chat/claim/practice models, the unused
old chunker and uncalled SQL/prompts. Existing data and migrations remain intact;
saved HTML workspace parsing is a live compatibility path and was retained.
Read-only student inspection is separated from practice writes, and Office course
preferences no longer depend on setup/hosting. These remove the two deferred import
cycles. Public API shapes and retained prompt content are unchanged. Python
formatting now has a CI gate; frontend tests share runtime/API and draft fixtures.
Resolved D-06/D-09/D-11/D-14/D-17/D-23 and T-11/T-31 leave the docket; D-13 was
cleared because saved workspace HTML is parsed and rendered with citation guards.
The lower test count reflects retired-code tests, not removal of active learning,
archive, backup or retrieval acceptance. Remaining semantic/native quality gates
stay open in the existing docket.

## 2026-10-02 — B-02/B-06/B-07/B-13/B-14 pass

Landed five docket issues together. B-13: `constraints.txt` pins direct deps
cross-platform, regenerated by `scripts/freeze_constraints.py` with a CI
`--check` drift gate and `pip check`; the bake-off report path is
whitespace-sanitized so the documented cloud id (`...:free`) no longer crashes
on Windows; production eval seeding moved to `src/backend/evals/seed.py` (with
`tests/factories.py` delegating); pytest now uses a per-process basetemp. B-02:
migration 018 records a scope revision when a chat's selection (or a selected
source deletion) changes; the answer prompt gets a trusted scope instruction
and the rolling summary a topic-only prompt, so an excluded source cannot supply
facts through context. Retrieval was already scoped. B-14: desktop activation —
the shell stops the backend, runs `stacks-backend --activate <id>` to verify,
migrate and swap database + `raw/` with the previous library preserved for
rollback, then restarts; models/runtime/Office state are untouched. B-06/B-07:
built the measurement infrastructure — a judge seam with a committed semantic
case file, enforced expectation keys, precision@k/MRR and load-time rejection of
label-less retrieval cases, committed retrieval cases actually seeded in tests,
and CI jobs for encoder parity and the eval suites. The infrastructure does not
close the quality gates: B-06 (real generated-answer correctness) and B-07 (real
PDF/OCR/proof/table/slide/large-course retrieval) remain open and now have
harnesses plus honest baselines. 774 backend tests, ruff, strict mypy (141
files), frontend check and 17 frontend tests pass; the installed desktop restart
journey stays under B-10.

## 2026-10-03 — Coverage, teaching default, and the source list

`extraction_version` is `structured-v2`. An index is stale when its extraction
version or segmentation version is older than the current policy (passage
policy version `3`). Page quality is scored after glyph repair. A mixed PDF
keeps its text layer when page OCR is unavailable; a fully blank PDF still
fails closed. Blank pages are queued before garbled ones, under the existing
OCR cap. Counts stay null until the source is reindexed.

With no saved teaching method and no method history, a direct question leaves
the method unset. `step_by_step` applies only to a practice request
(`Intent.QUIZ`), or when that preference is saved. A non-empty method history
still explores as before.

The source list is oldest first (`created_at`, then filename). An upload that
omits `source_type` is slides for a PowerPoint file or a near-16:9 first page,
syllabus when that word is in the filename or the first page, and notes
otherwise. The label can be changed later without reindexing. A saved
bigger-model choice still wins. `LLM_BIGGER_BASE_URL` and `LLM_BIGGER_MODEL`
fill that slot only when it is empty, and never replace the interactive model.

STK-011 (a named reading missed by retrieval) stays a hypothesis until a
reindex is measured. STK-022 (Enter not sending the first question) was not
reproduced, so the composer was left alone. Existing courses keep their old
index until reindex; older chats keep the citation text they already saved.

## 2026-10-03 — One docket

The three issue files were merged into `docs/docket.md`: the standing engineering
ledger plus the two QA passes of the Latino Politics run (`bug_docket.md`, the
earlier draft, and `DOCKET2`, its superset). The QA dockets are deleted; no code,
test, or script referenced their `STK-` ids, only `notes.md`, so nothing else needed
fixing. The merged file keeps every QA entry's full repro/evidence/root-cause and
proposed fix, now grouped by severity under "QA run findings", with the standing
`B-/C-/S-/X-/P-/F-/R-` candidates below it and a "Done" section at the bottom.

Five QA entries were verified fixed in the working tree and moved to Done:
STK-006 (currency no longer parsed as KaTeX), STK-007 (sources list the cited
subset), STK-012 (`step_by_step` no longer the default), STK-015 (`LLM_BIGGER_*`
env fallback), STK-016 (no stray Cancel on an empty library). Nine more show partial
progress and keep a Progress note: STK-004, -010, -014, -017, -024, -028, -030,
-037, -047. The rest stay open. The fixed items are marked for user confirmation,
then deletion.

## 2026-10-03 — Docket triage and confirmed-defect fixes

The user asked to fix real problems, defer unsupported/optional candidates to `backlog.md`, and surface genuine tradeoffs. The old 279-ID mixed ledger was replaced by an active docket and a deferred-candidate table. Closed routine fixes no longer require user confirmation or remain in a Done graveyard. Unconfirmed is not proof of correctness; remaining observed output/history, retrieval, native integration and lifecycle problems stay explicitly active.

Implemented contracts: bounded text decoding with Unicode BOM detection; no invented PDF glyph digits; strict OCR boundary handling; ingestion heartbeat/failure/retry history; shared code-aware citation validation and renumbering; quoted-answer marker reconciliation; edits retain their cited material and blank slides; archives stream sources and reserve export names; provider/message updates serialize; per-attempt model usage is recorded even when output validation fails; migration 020 records locality and queue/run indexes; Office package expansion, XML entities and incoming request bodies are bounded; package order follows manifests; model download paths are namespaced and hashes/ranges checked; Office TLS/readiness errors are honest; course navigation remounts bound stores, and pending chat sends remain visible.

STK-048: quizzes now accept prior teaching only from a trace in their own conversation. Migration 021 removes unsupported method associations from active historical suites/observations and CORE method evidence while keeping quiz results, COURSE capability observations and the discarded association for inspection. Same-chat associations survive. Companion work remains separate from learning writes.

Removed resolved IDs (some were fixed before this pass and verified with the current suite): C-02, C-03, C-05, C-06, C-07, C-08, C-09, C-10, C-11, C-12, C-13, C-14, C-15, C-18, C-23, C-27, C-33, C-34, C-35, C-36, C-37, C-39, C-40, C-41, C-42, C-43, C-44, C-45, C-46, C-47, C-49, C-50, C-51, C-56, C-57, C-60, C-62, C-64, C-65, C-66, C-67, C-70, C-71, C-72, C-73, C-74, C-75, C-80, C-81, C-82, C-83, C-84, C-86, C-87, C-88, C-89, C-90, C-94, C-95, C-96, F-01, F-02, F-03, F-04, F-05, F-06, F-10, F-11, F-12, F-13, F-15, F-16, F-17, F-18, F-21, F-27, P-10, P-12, P-24, S-10, S-11, S-12, S-14, S-15, S-17, S-21, S-22, S-23, S-24, S-25, S-28, S-32, STK-001, STK-002, STK-003, STK-005, STK-006, STK-007, STK-008, STK-009, STK-010, STK-012, STK-014, STK-015, STK-016, STK-017, STK-018, STK-021, STK-034, STK-048, T-08, T-09, T-10, T-21, X-10, X-19, X-21, X-23, X-25, X-26, X-27, X-30, X-32.

Native startup source changes: R-04, R-05, X-01, X-04, X-05; formatted/parsed only, no Rust compilation. B-10 remains open. Backend suite: 834 passed; frontend: 19; Office: 55; Python lint/format and 145-file mypy passed. Follow-up runtime/Office tests passed 40 after executable-path cleanup and registry/trust handling changes. These are mechanical evidence, not closure of B-06/B-07 semantic quality.

Further confirmed fixes in the same triage: X-18 serializes catalog ID allocation,
add/remove and duplicate checks in one short SQLite transaction; X-24 distinguishes
credential-store failure from absent keys and returns actionable API errors;
F-14 traps confirmation-dialog Tab focus and restores its trigger; F-20 resets
flashcard order and review marks after edits. PowerPoint note ownership follows
slide relationships, including reordered decks; unlinked notes stay uncertain.
Explicit quiz counts no longer hit a six-question schema or three-question repair
cap; incomplete requested suites are withheld, and title-only study materials
are rejected. Prompt policy 42 adds count framing; study sheets route to documents.
F-19's generic reset claim is deferred until unchanged-identity data loss is shown.
The shared output controller, context/history gaps, semantic/native acceptance and
coordinated Office setup rollback remain active, rather than silently deferred.

Final same-pass validation: 845 backend tests passed; Python lint/format and
145-file mypy passed. Frontend check and 19 tests passed after the UI changes;
Office.js check and 55 tests passed. Native compilation/packaging and live-model
semantic acceptance were not performed. Temporary triage tooling was removed.

## 2026-10-03 — One outstanding-work docket

At the user's request, the separate backlog was removed and its remaining entries
consolidated into `docket.md`. The docket is now the sole work queue; earlier
notes describing two queues are historical. The round-2 report at `92be710` is
reconciled there, including all 48 STK findings and seven new findings, reopened
extraction failures and pending GUI checks. Evidence status remains explicit;
this documentation reconciliation implements no code fixes.

## 2026-10-04 — Review fixes retain retest status

At the user's request, implemented bugs now remain in the docket as **Fixed in
code — awaiting verification** until the user confirms retesting. This supersedes
earlier removal instructions. The AR review is consolidated into the same docket,
retaining all 71 original findings and separating cleared claims from fixes.

Answers distinguish the original numbered evidence space from actually cited
evidence; saved materials, practice, maps, exports and portable archives retain
that numbering. Explicit empty trace attribution does not become all retrieved
sources. Ingestion 6 stages page-aware bounded OCR, preserves usable mixed text
on failure, normalizes before assigning spans and omits blank passages.

Native Save selection belongs to the shell, including replacement confirmation;
the backend provides cited bytes and refuses arbitrary write paths. Workspace
draft export writes no artifact or learner observation. Backup startup/restart
has a retry path with short state locking. Office setup uses staged certificate
cohorts and compensated lifecycle steps; failed cleanup keeps public trust identity
for inspection/retry. Graph source refresh rebuilds the full valid course cohort
to preserve inbound top-k edges; O(n²) CPU cost remains a scale-acceptance concern.

Mechanical checks are implementation evidence. Native Save/recovery/Office fault
journeys, the affected PDF corpus and semantic generation gates remain open.

## 2026-10-04 — Preserve generated material boundaries

Documents/decks use transient section/slide arrays with paragraph arrays for model
responses; the app inserts Markdown boundaries before the existing workspace
citation gate. Saved-material/storage/export contracts remain unchanged. Explicit
recognized slide counts, title-only units, exact duplicate slides and intro
citations are validated before delivery. This repairs structure independently of
the still-planned shared output budget/recovery controller; real-model correctness
and cutoff recovery remain open. Companion sheet cells/quiz options now wrap and
support multiline editing; installed visual acceptance remains pending.

## 2026-10-04 — Shared whole-unit output recovery

The provider transport performs one completion per call; a common controller now
bounds nested output/schema/content/HTTP recovery and pins the chosen endpoint for
the operation. Cutoffs regenerate the same bounded unit with configured headroom,
preserving scope/evidence rather than shortening the request. Partial output never
becomes a saved answer/material/practice item. Failed completions record supplied
usage before validation, with cloud budget rechecked between HTTP requests.

Optional profile ceilings and the managed runtime context constrain widening;
unknown capacities stay unknown. MiMo's exact retest ID ships the empirically used
16k preference. Input costs remain estimates and visual costs unknown. Missing
usage is explicit in results/operation accounting but its persisted ledger flag,
multi-unit generation/checkpoints and context compaction remain open in the docket.
These internal operation budgets do not resolve the financial reservation tradeoff.

## 2026-10-04 — Usage completeness survives storage

Migration 022 records new completions' reporting completeness while retaining
historical rows as unverified, without guessing whether old zeros were measured.
Known partial counts are preserved; missing or malformed counts contribute zero
and an explicit incomplete status rather than estimated billing. Settings exposes
the incomplete/unverified request count and calls its totals reported usage.
Full backup recovery preserves the flag; reduced snapshots still omit usage.
Optional provider metadata cannot discard an otherwise usable answer. Monthly
budget remains a stop between requests; hard concurrent reservations stay C-63.

## 2026-10-05 — Exam-style practice

Students can upload a quiz or exam PDF or photo from the Artifacts tab, beside
mind maps. The file is read for topics, difficulty, and formats only. It is not
indexed, not stored, and not answered. New questions use the existing quiz
artifact and practice suite, grounded in retrieved course passages, and are
dropped when they reuse a six-word run from the upload. Multiple choice is
unchanged. Short answers and multi-part items are flat questions: a shared
stem, a part letter, an expected answer, and required points. A short answer is
correct only when every point's content words appear in the student's words.
That check is mechanical, so a paraphrase that drops the source's terms is
incomplete. No new generation task and no SQL migration. Prompt policy is 44.
A live model evaluation of the generated questions has not been run.

## 2026-10-06 — Exam-style review fixes; round-3 items enter the docket

Review of the exam-style work tightened the copy filter (copied explanations
are cleared, copied topics replaced) and separated unreadable model output from
copy rejections; one unreadable scan page is now skipped instead of failing the
upload. The quiz editor authors short answers and multi-format quizzes. Tests
cover scanned-PDF OCR, size/text rejections, quiz export with stems and short
answers, and learning-archive round-trips of written answers. The round-3
retest's R3-NEW-1…17 are now docket rows (statuses from `f595461`, awaiting
verification), which resolves the report's dangling references; the report
itself remains `docs/RETEST-round3.md` (untracked evidence). Exam-style question
quality stays structural only; live model evaluation is docket F-20.

## 2026-10-06 — Bug sweep of exam-style practice and short answers

A full sweep found seven defects in the new short-answer path. Grading ignored
"no" and "not", so a negated required point was marked covered. Blank lines in
the required-points editor counted toward the six-point cap and rejected a
valid quiz. Nested multi-part items dropped the parent citation, topic, and
explanation. A required point copied from the upload stayed as the grading key.
An unreadable photo raised Pillow's decompression error as a server failure and
left a lower process-wide pixel limit behind. Hint and explain omitted a shared
stem. Quiz Markdown numbered the answer key while the questions used part
letters. Each is fixed in code and awaiting a retest (docket SW-01 through
SW-07). Live model quality is still F-20.

## 2026-10-06 — Bug sweep, continued

The same sweep found another thirteen defects after the short-answer pass.
Grading still accepted "do not preserve addition" for the point "preserve
addition", and it dropped one- and two-digit numbers. Exam-style grounding
used that loose overlap. An excluded short answer's explanation included the
key, and a hint could quote a two-word required point. "Make a quiz from my
notes" was treated as a request to fetch notes. A quiz filtered to nothing
was recorded as a lesson. A citation refusal was cached. An Office answer
with a dangling citation was a server error. Deleting a model that was still
verifying stopped the server before the delete was refused. Activation with
a database outside the data folder swapped the wrong files. Shutdown could
restore a stale active model. A trailing newline inflated the last line-range
label by one. Quiz proposals hid every question, and a quiz edit's schema
dropped short-answer keys. Each is fixed in code and awaiting a retest
(docket SW-08 through SW-20). Live model quality is still F-20. The proposal
preview was not opened in a browser.

## 2026-10-06 — Two open docket bugs

The course page stopped asking about an in-progress source after 15 minutes,
which is shorter than a long scan. It now keeps polling until the source
settles, the page closes, or several fetches fail. A machine with no keyring
backend made the provider settings request fail; that case is an absent key,
so the settings page and local models still load. A locked keychain is still
a 503, because the key may exist. Docket NEW-8 and NEW-9.

## 2026-10-06 — Essay critique comments, and does not write

The critic is a paper-session action shared by the course Critic tab, the
companion, and the Word pane. The score is how hard that pass pushes. It is
not an essay grade, and an empty finding list is a real result. The student
picks the essay kind. Syllabus passages are reserved from sources already
marked syllabus; nothing is reclassified, and a missing syllabus is said out
loud instead of filled in. Earlier quotes are compared to the new snapshot
before the model call. Rewrite requests and invented quotes save no turn.
Word receives an empty insert payload. Excel and PowerPoint do not get the
action. Critique writes no learning row. Docket F-21. The pass was not run
against a live model, and the new screens were not opened in a browser.
