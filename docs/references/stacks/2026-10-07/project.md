# Project: Stacks — course memory and adaptive study

## One-line idea

A desktop study tool that ingests a student's course materials, keeps
source-grounded course knowledge plus a course-memory focus record, and helps
the student decide what to study next. The central library is the product home;
an optional companion can be opened beside the student's current work. It runs on
the student's own laptop, with a small open model by default and any cloud
model the student chooses.

This is **not** just "chat with PDFs." The useful output is an inspectable, evolving model of:

1. what the course materials say;
2. how the course concepts connect;
3. what the student appears to understand, misunderstand, and need to practice;
4. what new content (practice tests, study artifacts) is worth generating.

Interactive mind maps provide another way into the same course material: colored
topic branches, expandable examples, inspectable source passages, and optional
comparison links between distinct subjects. A selected branch can open an
explanation or generate a saved practice quiz. Comparisons do not imply membership,
and map positions do not measure semantic similarity. The initial generator maps
retrieved passages; it does not claim exhaustive coverage of an entire course.
Exploration alone supplies no evidence of student proficiency or preferences.

Course retrieval keeps original coherent passages and source parents in the same
local database. Proofs remain logical units; long ones are searched through
bounded windows and incomplete reading is visible. Saved generated study aids
are searchable only by opt-in and point back to eligible original evidence.
No prerequisite graph is stored. The tutor may tentatively suggest background
review and practice without recording an unearned proficiency judgment.

## Problem

A course creates fragmented information:

- syllabi, slides, readings, problem sets, solutions, lecture notes, and announcements live in different places;
- course-specific notation and definitions differ from generic textbook explanations;
- ordinary chat-with-PDF tools retrieve passages but do not accumulate a reliable model of the course;
- grades and exams arrive late, giving weak feedback about whether the student actually understands a topic;
- the student's mistakes are usually corrected once and then lost rather than turned into a reusable error model.

The project should make course work compound across a semester. It should help answer:

> What does this course actually say about this concept, what prerequisites does it rely on, and what should I practice next to demonstrate independent understanding?

## Primary user

One student on their own machine, using it for their own courses. No
accounts, no server: one SQLite database per user in their app-data
folder. Not an LMS, not a universal tutor — a useful personal instrument
that can be evaluated on real courses. The target is an ordinary laptop
(8 GB RAM minimum, 16 GB recommended).

## Product principles

- **Source grounded:** substantive academic answers link to uploaded source material. Every claim carries an evidence chain.
- **Inspectable:** the system shows what it retrieved, what it inferred, and why it believes a concept is weak or mastered.
- **The user owns their data:** course material and study history stay in
  the user's data folder. The default model runs locally; a cloud provider
  is used only if the user picks one, after a one-time notice of what it
  receives. A whole course can be exported as one `.course` file. Optional local
  library backups offer full, partial (no chats), and heavy (no learning memory)
  retention, independently of compression strength. Unfinished editor recovery
  stays available even when automatic backups are off.
- **Course-specific:** preserve a professor's notation, definitions, rubrics, and examples rather than replacing them with generic explanations.
- **Small models, strong harness:** the harness frames each task (prompt,
  output schema, bounded citations) so a ~2B local model does narrow,
  checkable work. Every model call is recorded in a local usage ledger.
- **Learning over completion:** the tool helps practice and diagnose understanding, not produce assignments for submission.
- **One inspectable home:** the library is the primary interface for courses,
  knowledge, memory, saved work, and setup. The companion is an optional
  pop-out for focused help, and the Office add-in is a document bridge.
- **ML earns its role:** begin with retrieval, structure, and simple measurable baselines; add fine-tuning only for documented failures.
- **Extensible by design:** new content kinds, locator types, and formats are free strings — they insert without schema redesign.
- **Destruction leaves a distilled record:** a deleted course sits in the
  trash for 30 days; purging it keeps only its bounded, evidence-bearing
  course-memory keepsake.

## Non-goals

- Predicting grades from a calendar or course schedule.
- Rebuilding Canvas, Notion, Anki, or a generic PDF-chat wrapper.
- Claiming the student understands something solely because they read it or asked a question.
- Blindly fine-tuning a model on all uploaded files.
- Automating graded coursework in ways that undermine actual learning.

## Core system model

The product has six concerns. Runtime contracts are grouped by feature,
with repository and archive records beside their implementations. The SQLite
baseline is extended through append-only migrations for saved chats, artifacts,
adaptive practice and passage retrieval; 006 and 007 remain retired.

### 1. Courses

Courses and their uploaded sources. `kind` is a free string;
`content_type` routes storage/serving. A course can be exported to a
`.course` file (sources, chats, artifacts, versions, and cited passages)
and imported on another machine. Search indexes rebuild there; cited
passages stay readable in saved work.

Tutor preferences change presentation, not truth or retrieval.
Presentation is a user-memory (root) concern per decision 007 and may not
alter factual evidence, source eligibility, citations, or mastery evaluation.

### 2. Source content

Materials are stored **whole** — nothing is destroyed at ingest. Uploads
stream to disk under a hard byte ceiling, are stored under generated names
(path traversal structurally impossible), and are gzip-compressed only
when the mime type allows and it saves ≥10% (`stored_encoding`:
`identity` / `gzip`). Each source gets **locators**: a free-typed
source location (slide 7, page 3, timestamp 12:30, cell range
A1:D20 — each format keeps its natural unit). Retrieval units are
**coherent original passages**, stored once with their locator links
(`chunk_locators`), authored parents, and original order. Long passages keep
their logical identity and use bounded embedded search windows. Sources carry
`file_hash` for dedup. Ingestion is an ordered, versioned pipeline: text
extraction → OCR (image-only PDFs; rasterization-capped) → passage preparation →
atomic index publication. A stage runs only after its predecessor succeeds,
retries per versioned configuration, and reports exhausted attempts. A failed
refresh leaves the last successful index usable.

### 3. Course factual evidence

One passage store holds original text, source/chapter/section parents, bounded
search windows and locators. The encoder proposes coherent boundaries without
rewriting or discarding text. Containment and original order are structural;
similarity links and inferred headings are labeled as inferred. There is no
separate TOC, concept inventory or prerequisite graph. Legacy knowledge
annotations survive as ordinary saved artifacts with their evidence.

Keyword (SQLite FTS5), window embeddings and passage similarity generate
candidates within the selected originals. Fusion and a cross-encoder reranker
choose bounded excerpts; nearby context retains qualifications. Citations show
when the model read only part of a passage. Saved generated study materials are
searchable only when enabled for the course, and lead back to eligible original
support rather than becoming factual authority. Each retrieval seam must earn
its place on broader course evaluations.

### 4. Student model

Complete multiple-choice practice suites retain every answer, help flag, source
snapshot, and reviewed answer key. Hints track assistance; completed questions
offer source-backed explanations. Editable content ratings guide future course
quizzes cautiously without changing mastery or CORE preferences.
COURSE memory estimates each topic separately
for recognition, explanation, application, counterexample, and transfer on a
0–100 scale; untested capabilities remain unknown. Evidence strength and freshness
accompany each estimate. Repeating a revealed question cannot establish
proficiency. CORE memory holds teaching preferences and observations across
courses. Chat supplies tentative experiments, never capability scores. Adaptation
runs quietly; the Memory tab exposes evidence, corrections, and forgetting.
See `docs/system.md` section 12 for the baseline policy; `docs/docket.md` B-08
tracks assessment and effectiveness limits.

### 5. Chat history

Conversations are stored in **two forms**: the raw turns (what the user
sees) and a compressed summary (what the model is prompted with),
regenerated when the conversation grows past a threshold so a small
model's context stays current.

### 6. Evidence & grounding

Answers carry citations to original passages and source locations; retrieval
traces record the excerpts actually supplied to the model. Saved messages and
artifact versions retain that provenance and preserve cited passages across
source changes. Valid citations do not establish that every generated claim is
entailed by its source; independent semantic evaluation remains essential.

## MVP

A single-course MVP for one student on their own laptop.

### MVP user stories

1. I can install the app, create a course, and upload PDFs, Markdown notes, and text.
2. I can open a movable, resizable companion beside another app and ask about
   connected document snapshots, saved work sessions, and course references.
3. I can keep separate saved conversations in a course, choose their
   sources and model, and open an answer's cited passage in the source.
4. I can create and edit cited course notes, schedules, study decks, quizzes,
   and flashcards, review model edits, and restore an earlier version.
5. I can explore source structure or a saved mind map, inspect original passages,
   and request a cited explanation or practice quiz.
6. I can request a short closed-notes diagnostic constrained to selected topics.
7. I can answer the diagnostic, state my confidence beforehand, and receive feedback.
8. The system stores my errors by concept and displays the evidence behind any recommendation.
9. I can ask, "What should I work on next?" and get a transparent answer grounded in my attempts and the course's current material.
10. I can delete a course (30 days in the trash; its course memory survives the purge) and export or import a course as one file.

### MVP success criteria

The MVP is useful if, for one real course:

- source citations are correct on a manually checked evaluation set;
- a cold probe exposes at least some real gaps that rereading would not reveal;
- recommendations can be traced to specific attempts and concepts;
- the student uses it repeatedly for at least two weeks;
- it saves time or improves study decisions compared with manually searching files and guessing what to review;
- answers stay usable on the floor machine (< 30 s each on 8 GB RAM, no GPU).

## Technical requirements (agreed)

- **App:** a Tauri v2 shell that opens the SvelteKit library as its primary
  window and creates an optional native companion window on request. The FastAPI
  backend (frozen by PyInstaller) is a child process in the per-user installer.
  A per-launch token between the webviews and backend is the desktop API auth.
- **Backend:** Python / FastAPI under `src/backend/`, one package per subsystem.
- **Database:** SQLite (WAL, foreign keys, FTS5) via raw SQL, no ORM.
  Versioned, append-only migrations (`common/migrations/00X_*.sql`) applied
  by `common/migrate.py`. Every foreign key cascades.
- **Generation:** one seam, `common/provider.py`, routed per task class
  (interactive answers, background work, and an optional "bigger model")
  to the endpoint the user chose in Settings: the bundled llama.cpp server
  (default MiniCPM5-2B; others in `configs/runtime.toml`), OpenRouter,
  OpenAI, or any OpenAI-compatible endpoint. Keys live in the OS keychain.
- **Encoders:** embeddings (IBM granite-embedding-english-r2) and the
  reranker run in-process on ONNX Runtime, pinned and checksummed. Model
  choice is a pencil mark: each search window stores its embedding and model
  identity. Switching models requires re-ingestion and replaces those vectors;
  vectors from different model spaces are not mixed during retrieval.
- **Frontend:** separate codebase (`src/frontend/`), talks to the backend only via its API.
- **Config:** tunables versioned in `configs/` (`ingestion.toml`,
  `retrieval.toml`, `passages.toml`, `embeddings.toml`, `models.toml`, `runtime.toml`,
  `prompts.toml`, `tutor.toml`, `lifecycle.toml`). `.env` holds only
  optional development settings.

## Evaluation plan

Evaluation is the center of the project, not an afterthought. The
mechanical harness is live (decision 010): retrieval
(`retrieval/evals.py` + `data/eval/retrieval/cases.json`) and the answer
harness (`evals/answer.py` + `data/eval/answer/cases.json`, mechanical
scorers, prompt-version-stamped logs under `runs/`).
`scripts/eval_models.py` runs it against the bundled runtime, any model in
the catalog, and optionally a real course.

### 1. Retrieval evaluation

Create 30–50 course questions with known supporting passages. Measure recall@k,
citation precision, and source preference (instructor material when it should be
used).

### 2. Answer evaluation

For a small held-out set, score factual correctness against source
material, citation correctness, course-notation fidelity, appropriate
uncertainty, and usefulness. *Status: the mechanical half is live —
citation validity, refusal honesty, steer behavior per decision 009's
three zones, workspace output. LLM-judged qualities (notation fidelity,
usefulness) are open.*

### 3. Probe evaluation

For generated questions, assess alignment with selected concepts, whether the
answer is supported by allowed material, whether it tests application rather
than wording, and whether it leaks solutions.

### 4. Student-model evaluation

Do not initially claim predictive validity. Check that flagged weak concepts
match the student's own review, error clusters are coherent, recommendations are
actionable, and cold-probe outcomes improve over repeated attempts.

## Fine-tuning roadmap

Fine-tuning is phase two or three, not the MVP. Do not fine-tune until there is a
versioned evaluation set, a documented baseline failure, enough high-quality
examples, and a clear metric. Candidate targets include passage boundaries,
a reranker, a probe generator, and an error classifier.

## Privacy and academic integrity

- Course data and study history stay on the user's machine unless the user
  exports them.
- Nothing is sent to a cloud model unless the user chooses one; the
  one-time notice says what it receives.
- Never ingest classmates' work without permission.
- Do not automatically submit answers, solve graded assignments on demand, or conceal source use.
- Separate practice mode from assignment-reference mode.
- Visible evidence for every response based on course material.

## Milestones

- [x] **Milestone 0: Foundations** — scaffolding, tooling, six-layer
  storage, runtime contracts, migrations + runner, deletion design.
- [x] **Milestone 0.5: Hosted accounts and metering** — built for the
  hosted version (accounts, tiers, enrollment, sharing), then replaced by
  decision 012. That version lives on in the original hosted repository.
- [x] **Milestone 1: Source-grounded retrieval** — the ingestion pipeline,
  original-passage retrieval with fusion, bounded context and traces, the
  worker loop, the tutor endpoint (ask with strict refusal), and the
  citations endpoint behind the "sources used" panel.
- [x] **Milestone 1.5: Local-first desktop** — SQLite, the bundled
  llama.cpp runtime, provider choice, ONNX encoders, task framing, the
  desktop shell and packaging (`docs/plan-local-first.md`).
- [ ] **Milestone 2: User + course memory** — the memory tree of decision
  007: elevate tutor profiles into the user-memory root (behavioral,
  cross-course), evolve the course-memory node from a content summary
  into FOCUS memory as student data accumulates. The implemented practice and
  memory baseline still needs broader effectiveness validation (B-08).
- [ ] **Milestone 3: Cold probe loop** — diagnostics, confidence capture,
  scoring, error categories, per-concept history.
- [ ] **Milestone 4: Adaptive recommendations** — transparent "what to study
  next," weekly review view.
- [ ] **Milestone 5: Generated artifacts** — flashcards, practice tests,
  slideshows as course objects with artifact origin records; the
  three-zones assistance policy (decision 009) governs them.
- [ ] **Milestone 6: Fine-tuning experiment** — only on a documented baseline
  failure, against a frozen eval set.

## Open questions

- How will mathematical notation and diagrams be represented and cited?
  (Extraction is text-only; image-only PDFs need local OCR, which is
  planned but not yet bundled.)
- How well do passage boundaries and inferred similarity hold up on real courses?
- How should a student correct an inaccurate map link or mastery inference?
- What does "mastery" mean for a proof course versus a programming/data course?
- How well does the default model hold up on the 8 GB floor machine?

## Definition of done for v1

A student can install the app, upload one course's materials, ask
source-cited questions, take a short closed-notes diagnostic, review their
concept-linked mistakes, and receive a transparent recommendation for what to
study next — all on their own laptop. The system records provenance for
every claim, keeps a distilled record when a course is purged, and has a
small regression/evaluation suite that prevents silent quality loss.
