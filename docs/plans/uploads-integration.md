# Uploaded advising materials: initial integration plan

Status: revised on 2026-10-15 after review. One-time unpacking, Git exclusion,
the offline catalog option, and staged catalog regeneration are complete.
Source-model extensions, discovery metadata, and reviewed pilot preparation are
implemented. Shared-corpus publication and structured degree auditing remain
future work; the isolated pilot validation is recorded below.
`docs/architecture.md` remains the design contract. Update its ingest, provenance,
program binding, and research sections when implementing any new behavior below.
The existing database was inspected read-only; shared source rows were not changed.
Pilot indexing uses a separate database under `data/review-uploads-20261015/` and
the cached Granite embedder offline. Subsequent bounded advising checks use the
configured generation provider with public source text in that isolated database;
they do not change the app's default collection or feature flags.
Existing uncommitted application changes were left intact.

## 1. Recommended outcome and order

Use a hybrid workflow. Code owns repeatable extraction, canonicalization, identity,
validation, evidence binding, and publication. A person reviews publisher authority,
applicability, conflicts, and ambiguous rules. Agents can propose classifications,
compare documents, and identify omissions for review; their assessments do not
silently become institutional requirements or prerequisite edges. This hand-supplied
batch needs a one-time unzip, not a maintained archive-processing subsystem.

Make the supplied material usable as cited evidence through the existing
`sources -> locators -> chunks -> keyword/embeddings -> scoped research` path.
Keep deterministic program projections as an additional index over that evidence.
Do not treat every document containing course codes as a degree requirement.

Implement in this order:

1. Unpack once and refresh the stale catalog exports in staging (done).
2. Decide the source model, investigate existing department-source identities,
   and prepare a reviewed registry and one canonical searchable representation
   per document, preserving the originals and extraction lineage.
3. Pilot ingestion of current program/advising documents with the existing pipeline.
4. Add deterministic checklist/track projections after validating their table layout
   and evidence binding; publish the expanded corpus after retrieval evaluation.
5. Add calendars, transfer tables, and the single scanned sheet only after their
   specific extraction gaps are handled.

This is phase one of end-to-end advising. The initial release supplies cited
documents and recommendations. Academic-record handling, requirement composition,
degree audit, and semester planning still need separate design, outlined in
section 8. Existing `program_items` are raw requirement lines and course mentions,
not a degree-audit engine.

### Grounded constraints and advising judgment

The research path already separates facts, judgments, assumptions, limitations,
and questions (`advising/research_types.py`, `generation/research_controller.py`).
It is selected by `ADVISING_RESEARCH_ENABLED` plus `TOOLS_ENABLED`; its default
remains off. Extend its evidence coverage and applicability rather than creating
another rigid question-to-answer taxonomy.

Requirements are factual constraints tied to an institution, program, edition,
cohort, and sometimes admissions cycle. Preserve explicit alternatives, exceptions,
approval conditions, and uncertainty about which rules apply. Deterministic checks
evaluate only rules they support; unhandled conditions remain unresolved. Neither
a model nor an agent may invent missing requirements or prerequisite edges.

The LLM may recommend and compare paths using the student's interests, supported
curricular overlap, tradeoffs, and explicit assumptions. For "which major for
premed?", first establish the target-school requirements or the limits of common
pre-health guidance, then compare plausible majors. A preference can be a judgment;
claims of fewer additional courses, higher acceptance rates, easier grades, or
"most recommended" status remain factual and need evidence. Labeling a paragraph
judgment cannot exempt its factual premises from grounding. Existing structural
validation does not prove semantic entailment; reviewed answer examples remain
necessary.

## 2. What is actually supplied

The unpacked tree contains 157 files: 73 Markdown, 53 text, 29 PDF, and 2 CSV.
Six ZIPs contain a union of 245 distinct paths: the same 73 Markdown and 53 text
files, 116 PDFs, 2 CSVs, and a sitemap XML. The only repeated archive path is
`sheets/INDEX.md`, identical in all six archives. Every already-unpacked file
matches its archive entry byte for byte. At initial review, **87 PDFs and the XML
remained packed**; all 245 paths are now unpacked under
`data/advising_uploads/2026-10-15/originals/`. Original `uploads/` is preserved and
now gitignored.

Grouping representations by folder and filename stem gives 128 groups excluding
the index. These are not 128 independent authorities: several are catalog excerpts,
derived exports, duplicate web captures, or older editions.

| Material | Observed contents | Intended use |
| --- | --- | --- |
| Catalog | Full 158-page 2026-27 PDF and text, plus eight program excerpts | Reuse the existing catalog splitter and program importer; avoid indexing the full catalog alongside all its duplicate sections |
| Data Science | Major/minor checklists, 22 track checklists, March 2026 handbook, pages, older minor checklist, unofficial QTM map | Current core/track evidence, with explicit links between a core and its track supplements |
| Biology/NBB | Degree pages, 2020/2023 sheets and worksheets, tracks, elective/area lists, research packet, term offerings | Degree and elective evidence; separate older guidance and term-specific availability |
| CS/AI/Econ and Chemistry | Degree pages, AI concentration/electives, Chemistry guide, catalog excerpts | Requirements plus departmental explanations and alternatives |
| Interdisciplinary Studies | 2026-27 and 2025-26 handbooks, major/advising pages, writing companion | Current requirements and process guidance, with older editions kept separate |
| Pre-health/pre-law | Admissions preparation, application guidance, outcomes, letters, nursing sheet | Advising evidence scoped to the profession, institution, and applicable cohort |
| Dual degrees and 4+1 | Emory, Georgia Tech, Agnes Scott, and Laney material | Pathway/admissions guidance; external institution rules retain their publisher |
| Policies/calendars | Academic rules, AP credit, honors, school-by-school calendars | Policies and dates scoped to edition, school, term, and session |
| Careers | Eight CPD captures, some containing event feeds and navigation | Curated career guidance after deterministic cleanup |
| Course exports | 3,842 sitemap course IDs and a partial 568-course CSV | Discovery/coverage comparison against the atlas, not another prerequisite authority |

Counts and comparisons describe local files inspected on 2026-10-15. The index's
retrieval dates, CURRENT/OLDER labels, and source URLs are supplied acquisition
claims; this review did not independently re-fetch their web sources.

## 3. Findings that affect implementation

### Refresh catalog exports before adding another catalog copy

The existing `data/programs/programs.jsonl` contains 152 program records. An offline
replay of the uploaded PDF through the current working-tree splitter and duplicate
check produces **155**. It separates these three additional programs:

| Newly separated program | Its text currently appears inside |
| --- | --- |
| CHEMISTRY (BA) | BIOPHYSICS (BS) |
| INTERDISCIPLINARY STUDIES IN SOCIETY AND CULTURE (BA) | INTEGRATED VISUAL ARTS (CO-MAJOR) |
| Data Science (Minor) | Dance and Movement Studies (Minor) |

149 regenerated program Markdown documents match the existing exports exactly.
Three existing documents lose the incorrectly attached following program, and
three correctly named documents are added. This is a stale generated-data issue
relative to the current parser, not evidence that another parser must be written.
The uploaded PDF SHA-256 is
`3145288ac9732bd1194a44a9ae077f128b4577d32caa94e48eaf19e8c68903dc`.

The index's note that the Data Science minor lacks a separately headed catalog
section is contradicted by this replay. Use parsed, verified source evidence over
that discovery note. The eight hand-cut catalog excerpts should serve as review
fixtures/coverage checks, not additional competing catalog sources.

### Text extraction succeeds more often than structure extraction

All 116 PDFs were checked with pypdf and the current application extractor:
605 pages, two pages yielding no text, and one entirely textless document. No
replacement-character warnings were emitted. These checks do not establish correct
reading order, numerical fidelity, or complete table semantics.

- The October 2023 nursing BSN sheet is a one-page image. Its supplied TXT contains
  only a form feed. The application correctly raises `EmptyExtraction`. Keep it
  explicitly excluded pending a targeted OCR or verified transcription workflow.
- The CPD "What can I do with this major" capture has an empty sixth page. Retain
  the usable content and the existing warning; inspect whether that page is blank
  before treating it as a missing-content problem.
- Visual checks of the AI track, Registrar calendar, transfer equivalency chart,
  and nursing sheet confirm meaningful table structure. Plain text is insufficient
  to establish every row/column relationship.
- The AI-track PDF extracts its title last. Its `DATASCI F1`, unnamed electives,
  and blank experiential-learning rows are placeholders, not real course codes or
  verified choices. The core checklist explicitly says track requirements are
  additional and can differ.
- Calendar cells vary across schools and sessions. A date cannot be attached to
  "Emory College" merely because it occurs near that heading in flattened text.
- The major checklist contains 29 form fields. Blank name, ID, grade, credit, and
  completion fields are template furniture, not student data or requirement values.

### Deduplication and trust need explicit decisions

The CPD law/policy/government page appears under both `careers/` and
`prehealth-prelaw/`. Their bodies are identical after the supplied wrapper is
removed. Preserve both acquisition paths as aliases of one document.

`qtm-to-datasci-course-map.md` explicitly identifies itself as unofficial. Keep it
as a discovery/review aid, outside the verified institutional corpus. Cite the
official checklist/handbook statements for any equivalence claim, and retain the
scope of those statements. Do not apply a universal subject-code rewrite or turn
the compiled map into graph facts.

All 568 CSV course codes already occur in the local 3,342-course atlas JSONL.
This is a code-coverage comparison, not a claim that every field matches. The CSV
also has 197 blank requisite fields. Compare content and flag differences before
any course update; a blank is not evidence that a prerequisite can be deleted.
The 3,842 sitemap IDs are discovery URLs, not verified course records. Their count
cannot be subtracted from the atlas count to assert a specific number of missing
courses without reconciling IDs, canonical codes, aliases, and unavailable pages.

## 4. Fit into the existing structure

### Intake and source selection

Preserve `uploads/` as supplied input. Keep the one-time extracted originals and
generated material under ignored `data/advising_uploads/2026-10-15/`, with originals,
canonical documents, a reviewed registry, and `ingest-manifest.json`. Keep ZIPs,
the index, metadata sidecars, CSV
discovery files, and XML out of ordinary document ingestion.

`backend/configs/advising_sources.toml` now records a reviewed pilot: logical
source identity, exact original hash and relative path, representation choice,
publisher/URL, document role, edition, school/cohort/term, related programs/tracks,
supersession relationship, and inclusion/hold reason. It includes 12 documents,
three historical records, six holds, and one alias. Unlisted files are excluded.

Do not build `scripts.prepare_uploads` for ZIP handling. The six verified archives
were unpacked once using the standard library. Reusable document preparation lives
in `app/ingest/advising_documents.py`, called by `scripts.prepare_advising_documents`.
It validates the allowlist before writing documents, their exact-SHA manifest, and
separate source-byte lineage. It is deterministic and uses neither models nor
the network. `--check` validates without writing. Diagnostics stay in the terminal.

**Source-category implementation (040):** `institutional_publication` is
one broad reviewed category while retaining the existing four for compatibility.
It covers official pre-health-office guidance, School of Medicine admissions,
Laney/OUE 4+1 pages, Georgia Tech publications, and Agnes Scott publications.
They are not relabeled as departments. Runtime validation, research evidence,
and migration 040 implement this category. The existing shared database was not
migrated or reclassified by the read-only inspection.

Keep independent facets for institution, issuing unit/publisher, multiple document
roles, and applicability (school, program/pathway, track, cohort, admissions cycle,
term/session, or unknown). Roles and topical tags can grow without a new source
category for every office. A publication may combine requirements, recommendations,
outcomes, and promotional text. Category or official authorship does not prove that
every sentence is a binding rule. A reviewed registry determines inclusion and
scope; authenticity, currency, and applicability require separate checks.

Migration `040_source_applicability.sql`, the manifest validator, research
`SourceKind`, and evidence handling implement the category and independent facets.
The validator accepts bounded open role/dimension labels and caps combined UTF-8
metadata at 8 KiB. Old evidence keeps its saved labels/defaults. Applicability is
surfaced, not guessed or used as an implicit eligibility filter. The intake registry
excludes historical/held inputs from the pilot's default corpus.

Emit the existing exact-SHA manifest shape: `sources[relative_path]` with
`origin_key`, `content_sha256`, and validated `provenance`. Use the existing
`source_category`, `publisher`, `source_url`, `edition`, and date fields, plus the
new explicit facets inside `provenance`. Unknown fields are still rejected.
An item is classified by explicit review,
never by its filename, hostname, folder, or self-declared header alone.

The existing `data/programs/ingest-manifest.json` has 182 catalog entries and **no
department entries**, despite 35 files under `data/programs/departments/`.
`data/load_programs.log` lines 208-242 records successful ingestion IDs for all
35, and line 243 records exit=0. Their hashes exist in the historical collection's
raw store and reproduce all logged UUID5 source IDs. This proves historical
ingestion. A subsequent read-only database inspection on 2026-10-15 confirmed all
35 are indexed/current at revision 1, with null origins and no provenance rows.
This identifies reconciliation work; it does not verify academic applicability.

The uploaded Biology BS requirements PDF is byte-identical to a historical source:
`07feb903-9208-57a0-b2f3-02131417f8a8`, SHA-256
`7272b4c7b184cab3b31115dd98935f4958cd7146fbe77b2ba9f14b62b269dd0d`.
The inspection matched exact hashes in collection
`cf9d7a06-5d2c-44a8-956f-66eb68394666` through the connection seam and named
`intake.sql` queries, in a read-only transaction. The Biology PDF is still
indexed/current, unclassified, and without an origin. No source was reassigned or
superseded. PostgreSQL was recovered using the documented external log location
and stopped cleanly afterward.

Verified replay inputs now map all 35 files (22 Markdown, 13 PDF) to the scraper's
logical origin rules. `data/advising_uploads/2026-10-15/legacy-department-origins.json`
contains explicit source groups and per-hash provenance;
`data/programs/legacy-department-ingest-manifest.json` covers the exact existing
department bytes, and a separate lineage input records config/log/raw-store
verification. PDF dates and all applicability remain unknown. Configured PDF URLs
establish historical acquisition intent; original HTTP responses are unavailable.

The reconciliation script now supports these PDFs with exact raw hashes, PDF
signature, configured URL and origin checks. It defaults to read-only verification;
`--apply --database-url` is explicit and performs no automatic migrations. Origin
conflicts, omitted versions and intervening source/provenance corrections abort
the transaction. In `data/review-legacy-20261015/`, all 35 real originals were
ingested without origins, verified read-only, reconciled and reconciled again.
Source IDs, index revisions, chunks and locators were unchanged, and repeat
metadata was identical. This rehearses the identity repair, not academic currency.

Do not use a new `uploads:*` origin to conceal duplication or delete historical evidence. The old
catalog-plus-department glob fails with the current incomplete manifest; do not
recommend it until every submitted file has a reviewed exact-SHA entry.

Choose one current edition for normal shared retrieval. Older-but-still-linked
guidance is not automatically superseded, and undated material is not automatically
current. Review applicability separately. Keep superseded editions on disk initially;
index them later only in an explicitly selected history collection/scope.
Do not ingest all editions with distinct keys and rely on a prompt to choose.
Term-specific offering sheets need term-specific identities. `is_current` manages
source replacement; it does not by itself establish cohort or term applicability.

### Discovery metadata and canonical names

Use `<publisher>-<school-or-unit>-<topic>-<role>-<edition>.<ext>` for newly prepared
documents, for example `emory-college-datasci-ai-track-checklist-2026.pdf` and
`emory-medicine-md-admissions-requirements-undated.md`. Preserve original filenames
and aliases in the registry. Do not rename unchanged existing catalog files merely
for cosmetic consistency. Keep prepared stems within 100 characters while legacy
directory IDs truncate there. Origin keys are stable logical identities independent
of filenames; use `undated` rather than treating acquisition date as an edition.
Do not put mutable `current` labels in names.

`list_directory_documents` now selects verified metadata as well as stable source
IDs and filenames. Directory format 2 renders descriptive document records, and
the planner prompt includes advising, admissions and partner pathways. Research
discovery, exact reads, and immutable evidence expose the same source facets.
Long discovery metadata is marked incomplete; reads retain full bounded metadata.

Both paths expose a compact discovery record: handle/title,
publisher/institution/unit, verified category, document roles,
edition, relevant school/program/track/cycle/term, and reviewed applicability
status. Unknown stays explicit. Filenames are a fallback, not authority or a filter.
Metadata is pinned and rechecked on reads, evidence acquisition, and publication.
`metadata_revision` changes only when stored provenance changes and is included in
the directory cache key and research scope format 2. Metadata updates lock the
source row, so a refresh cannot slip between a read's check and its content.
Authorization and explicit-empty selections are preserved. Old active scope
manifests requiring verified metadata must restart; old evidence remains readable.

### Canonical text and locators

Use the supplied Markdown body for clean web captures, after deterministic removal
of the acquisition wrapper and documented navigation/event-feed boilerplate. Keep
the capture PDF as an original, not a second searchable copy. Move retrieval
timestamps and transient download URLs to sidecars so a repeat acquisition of
unchanged content preserves content hashes and UUID5 identities.

For ordinary text-bearing PDFs, prefer the original PDF and existing page
locators. Compare with the supplied pdftotext output when checking lost text,
reading order, course codes, and numbers. Never ingest a PDF and its TXT as two
independent sources. Plain TXT ingestion makes line-range citations and strips
form feeds during normalization; it does not preserve original PDF page citations.

For handbooks and table-heavy sheets, first measure the current extraction against
the originals. If a derived Markdown/table representation is necessary, preserve
the source PDF SHA, physical page, extractor/version, and exact derivation mapping
in a sidecar. Citations must identify the actual indexed representation and its
locator; an original PDF page claim needs a verified mapping. Existing program
binding matches normalized verbatim text, so paraphrased or synthetic summaries
cannot silently substitute for source text.

Start with layout-aware deterministic extraction for the calendar and transfer
chart. Preserve column headers, merged-cell applicability, qualifiers, and footnotes.
Keep them out of date/equivalence answers until reviewed. The known nursing scan
justifies a narrow OCR follow-up if needed, not an unconditional OCR pipeline.

### Program and track projections

Reuse `programs/catalog_pdf.py`, `programs/requirements.py`, and the current
catalog render/manifest logic. Regenerate the full catalog projection together:
**`import_programs` replaces every program in the collection. Never run it with
only the uploaded subset or one checklist.** Bind the resulting records to the
exact indexed origin/hash/revision before publishing them for research reads.

Department sheets initially remain ordinary cited documents. In the next stage,
associate reviewed supporting documents with the canonical program and distinguish
core, track, concentration, electives, and advising notes. The current program ID
is derived from its title and the importer rejects duplicate titles; it does not
already support several independent requirement sets for one program.

Do not invent titles to squeeze track sheets into that schema. If structured
track lookup is necessary, add an explicit program-to-document/requirement-set
relation with edition and track scope, source revision, and per-item evidence.
Design it in `architecture.md`, then use a new numbered migration and named SQL.
Inspect the current migration head; never edit the baseline or an applied file.
Mirror any exposed API fields in Python schemas and TypeScript types.

Reuse deterministic course-mention and explicit choose-count extraction only.
Keep original rows, alternatives, exclusions, grades, credit minima, approvals,
and unparsed conditions. A code mention is not necessarily a required course.
Do not manufacture a choose count from blank slots or convert total credits to
a course count. In cross-institution tables, bind course codes to their institution
and direction; Georgia Tech `CS 1301` must not resolve as an Emory course.

No uploaded document, derived map, or LLM output writes prerequisite nodes/edges.
Keep the atlas as the deterministic graph input. Transfer equivalence, admissions
preparation, and major membership are separate relationships, not prerequisites.
No similarity-graph rebuild or model-written nudges are required for these documents.

## 5. Implementation slices and acceptance gates

### Slice A: one-time preparation and catalog refresh (complete in review database)

- Done: ignore `uploads/`, retain supplied files, and unpack the complete verified
  union once into `data/advising_uploads/2026-10-15/originals/` (245 files).
- Done: add `scrape_programs --pdf PATH --source-url URL`. Offline mode skips
  network clients and department config/scraping and reuses `write_catalog`.
  Set a separate `DATA_DIR` for staging; no second catalog pipeline is introduced.
- Done: generate 185 catalog documents (155 programs, 29 department-front-matter
  sections, one policies document), full `programs.jsonl`, and exact-SHA manifest
  under `data/advising_uploads/2026-10-15/staging/programs/`.
- Done: verify all manifest hashes, the three repaired boundaries, and 149 unchanged
  canonical documents. All 155 records bind to their exact indexed evidence in
  the isolated review collection, including the three newly separated programs.
- Done: implement the source category and discovery metadata, inspect department
  rows read-only, and prepare the reviewed pilot registry. No shared source
  reconciliation is performed by this slice.

### Slice B: pilot cited document ingestion

Use an isolated review collection containing the same needed atlas/catalog inputs,
then the reviewed uploads. A collection name such as `uploads-review-2026-10-15`
is used in the separate review database; it does not change the app's collection.

The pilot includes Data Science core/minor/AI sheets and the full department
major/track requirements page, CS BS and AI concentration,
Chemistry guide, NBB requirements/electives, IDS 2026-27 handbook, pre-health
medicine guidance, School of Medicine admissions, and Laney 4+1 guidance. The
March 2026 Data Science handbook's cover says **2025-2026**: it is historical
despite the supplied index's CURRENT label. CPD pages are held because navigation
and events need reviewed cleanup. URL/filename years do not establish an entering
cohort for the undated checklists.
Use `scripts.ingest --origin-manifest` for prepared evidence and the complete
`scripts.import_programs` projection after the corresponding catalog sources index.
Ingesting files alone does not make checklists available through `read_program`;
they are initially available through document discovery/reads.

Gate: stable hashes/IDs on repeat preparation, correct sections/pages and warnings,
one searchable copy per document, verified classification, exact scoped citations,
no accidental history promotion, and no application of checklist text to the graph.
Retain the last good index if extraction, embedding, or publication fails.

Executed on 2026-10-15 using cached Granite only, in
`data/review-uploads-20261015/`, collection `uploads-review-2026-10-15`:

- Initially indexed 185 refreshed catalog documents, 12 reviewed pilot documents,
  and the existing atlas. A subsequent visual/source review added the full Data
  Science major/track page as the thirteenth pilot document: 199 current sources
  and 3,342 courses. The AI PDF genuinely contains placeholders; named focus
  choices and experiential conditions were absent from the sheet, not lost by
  extraction. The page supplies those details without resolving all composition
  questions.
- Imported the complete 155-program projection: 155 bound, zero unbound, zero
  evidence-unresolved programs. The newly separated Chemistry BA, IDS BA, and
  Data Science minor have no unresolved requirement-line evidence positions.
- Of 2,487 course mentions, 1,836 resolve to atlas courses. The remaining mentions
  stay unresolved; matching source evidence is not permission to infer aliases,
  substitute courses, double counting, or degree completion.
- Four targeted retrieval probes found the expected AI-track, Emory MD admissions,
  Chemistry BA, and IDS BA source/locator evidence. This validates source lookup,
  not end-to-end recommendation correctness or student eligibility.
- A second pass using natural questions found the expected source in all four
  cases. Unrelated passages still appear alongside relevant evidence. Catalog
  title/institution/role/edition facets were refreshed without reindexing content;
  the planner directory and research scope include the new metadata.
- The affected test set passed 156 tests, including disposable database/migration,
  contract, and no-model-in-prerequisite checks. The catalog metadata extension
  subsequently passed its 24-test scraper/preparation suite; Ruff passed.
- The existing isolated fixture baseline replayed at 0.9643 fused recall over
  42 unscoped positives, with both selected-scope cases at 1.0. Explicit empty
  scopes refused. Its unrelated-negative question still returned six chunks.
  The stored older run uses different fixture/config hashes, so these numbers
  are a current measurement, not a controlled before/after improvement claim.
  Expected labels remain pending human review, as its README states.

The shared corpus was not migrated, reclassified, reconciled, or replaced. Both
the initial inspection and review databases were stopped cleanly after their
first use. Subsequent review work uses explicit local database URLs and external
PostgreSQL logs.

Continuation findings on 2026-10-15:

- Reconciled all 35 legacy department inputs in a second isolated database,
  preserving source IDs, index revisions, chunks and locators on both the first
  application and repeat run. No academic applicability was promoted.
- Added scoped metadata discovery with `kind=document`, balanced broad leads,
  compact retained handle titles, and a source-read step after two discovery-only
  rounds. This addresses the observed loop that found sources repeatedly without
  reading them. Factual lookups may use empty candidates; schema feedback now
  distinguishes program handles from document handles. New reads and trusted
  cached rereads regain active-evidence priority; rereading an omitted source can
  make it visible again without granting citation authority before the next
  request. Repeated cached no-ops still hit the bounded stop rule.
- A real MD-admissions answer initially broadened a degree-country condition
  into an international-applicant rule and dropped a table qualifier. Manual
  evidence review found these despite valid citations. Prompt rules now preserve
  exact conditional predicates and table qualifications. The repeated MD answer
  retained the degree-country condition, accreditation, amounts, labs, timing,
  English/AP/IB/committee exceptions, and unknown admissions cycle. This is a
  reviewed sample, not semantic verification of all generated claims.
- Real transfer questioning returned insufficient evidence and did not assert
  a CS 170-to-CS 1301 equivalence. The final calendar check returned insufficient
  evidence without inventing a spring 2027 deadline. An earlier calendar run was
  interrupted by a long host pause; another reached the smoke runner's aggregate
  budget. Neither earlier run measures answer quality. The runner now labels its
  own budget exhaustion separately from provider failure.
- The latest AI answer cited the full department page and AI checklist but did
  not read the requested core PDF, and mislabeled webpage content as sheet
  content. It remains an incomplete comparison. The final premed attempt failed
  publication: omitted program evidence, 25 course recommendations without course
  reads, a stale state update, and an invalid correction. Candidate IDs on judgment
  blocks improved, but full recommendation fidelity is still unreliable. The
  cached-reread fix is covered by behavioral tests; this last provider run began
  before that fix and does not validate it. No invalid final proposal was published.
- The final affected preparation/research/reconciliation/contract/no-model suite
  passed 116 tests; Ruff passed. Real-provider checks remain manual and outside
  pytest.

Shared publication remains gated. The next focused work is to retain adequate
program evidence for each compared candidate within the bounded prompt, make
terminal schema/reference correction reliable, and distinguish required-course
mentions from recommendations to enroll. Repeat the broad comparison and exact
core-plus-track document case after those changes. Passing source lookup or
structural citation checks alone does not close this gate.

Known source conflicts are deliberate coverage gaps: the catalog's QTM 150+151
does not establish substitution by the checklist's DATASCI 160; a same-number
QTM/DATASCI statement does not prove it. The core and AI documents do not clearly
establish whether their elective counts are additive. Several newly listed
DATASCI courses lack atlas records, and the existing DATASCI 350 requisite still
names QTM 150+151 or CS 170. Do not fill those graph facts from checklists.

### Slice C: structured supplements and full publication

Add only the program/track associations needed by demonstrated queries, then
extend deterministic projections and the scoped research reader. Preserve
`unbound`/`evidence_unresolved` behavior for unsupported lines. Decide whether
edition/school/term filters need persisted metadata before admitting conflicting
scopes into the same default corpus; sidecar labels alone cannot enforce runtime
filtering. If such filters are deferred, keep the initial corpus restricted to
the reviewed default scope.

After the pilot passes, add the remaining reviewed current material in family
batches. Publish source replacements through existing atomic origin publication.
The source pipeline publishes per origin; it does not provide a batch-wide atomic
swap, so do not promise one without designing it. Keep the scanned sheet and
unverified table extractions as explicit coverage gaps.

## 6. Validation and example questions

Extend existing behavior tests rather than creating parallel suites:

- `test_extract.py`: checked extraction rows/pages, placeholders, and scan/empty
  behavior if extraction changes. Pure parsing tests need no database.
- `test_catalog_scrape.py` and `test_programs.py`: offline catalog preparation,
  relevant existing boundary cases, and raw-line/course/count behavior.
- `test_research_data.py`: exact source/revision binding and any new scoped
  supplement reads. Database tests must run, not skip.
- `test_contract_drift.py`: only if the public contract changes. Frontend typecheck
  and relevant behavior checks only if frontend code changes.

Use fakes and offline fixtures in pytest. Keep real embedding/model evaluation
outside the fast suite. For code changes, run the affected tests and Ruff; run
the existing fixture baseline before/after with the same embedding model, config,
and k. Its cases are at
`backend/tests/fixtures/retrieval_eval_2026_10_08/cases.jsonl`, not the absent
`data/eval/retrieval.jsonl`. From `backend/`, the literal generic-runner option is
`--cases tests/fixtures/retrieval_eval_2026_10_08/cases.jsonl`, but that option alone
does not reproduce its collection, summary documents, or selected/empty scopes.

Use `../.venv/Scripts/python -m scripts.manual.retrieval_baseline` for that isolated
baseline; omit `--output` so diagnostics stay in the terminal. The generic
`scripts.eval_retrieval` drops selected-scope metadata and unconditionally saves
run reports, so do not use it blindly for these fixtures. Treat the fixture cases
as the checked-in baseline; their README still marks them pending human review.
Add a small upload-specific evaluation set against the review collection with exact
`(source filename, locator label)` expectations once canonical files are stable.
Require no existing-case regression and successful reviewed positive/negative cases.

| Example question | Required behavior |
| --- | --- |
| What does Chemistry BA require? | Cite its correctly separated catalog section; never attribute it to Biophysics |
| What is additional for the Data Science AI track? | Read core plus track evidence; preserve unnamed slots and unresolved conditions |
| What changed between the 2025 and 2026 Data Science minor sheets? | Use an explicit history scope, label both editions, and preserve conflicts |
| Does a listed NBB course count as an elective? | Read inclusions/exclusions and conditions; an offering list alone is insufficient |
| Is this NBB course offered next spring? | Do not extrapolate from Spring/Fall 2026 snapshots to an unrepresented term |
| What is the Emory College schedule-change deadline? | Require the applicable school/term calendar cell and footnotes, or report the coverage gap |
| Does an Emory class satisfy a Georgia Tech requirement? | Cite the dated, directional mapping and its qualifications; do not mutate the atlas |
| Are QTM and DATASCI versions equivalent? | Cite the official statement with its scope; the unofficial compiled map is not authority |
| Can these courses count toward two programs? | Mentions alone do not prove double-counting permission or degree completion |

## 7. Execution and next publication gate

The first implementation adds source/applicability persistence, scope checks,
discovery metadata, and deterministic preparation. Run from `backend/`:

```powershell
../.venv/Scripts/python -m scripts.prepare_advising_documents --check
../.venv/Scripts/python -m scripts.prepare_advising_documents
```

Prepared pilot inputs are under `data/staging/advising-pilot-2026-10-15/`;
the refreshed catalog remains under
`data/advising_uploads/2026-10-15/staging/programs/`. Only files explicitly in
their manifests are import inputs; sidecars are not evidence documents.

Validate real cached embeddings, complete catalog program binding, and the
deterministic atlas in the isolated review database. Check representative
source/locator retrieval and report weak or missing evidence. Shared publication
still requires resolving overlapping legacy sources and reviewing applicable
editions/conflicts. Importer success alone does not authorize switching the live
app to this database or bulk-publishing the batch.

## 8. Path to end-to-end advising: still to design

The initial integration is not a complete design for the following capabilities:

1. **Student academic records:** course attempts, grades, credits, terms, transfer
   awards, substitutions/approvals, declared programs/tracks/catalog year, and
   provenance/verification status. Student reports remain distinguishable from
   institution-verified records. Existing conversational context is not a transcript.
2. **Requirement composition:** evidence-bound core plus track/concentration sets,
   explicit alternatives/counts, credit/grade/residency minima, exclusions,
   cross-program counting and approval rules. Multiple course mentions do not
   define this logic. Build a typed rule representation with explicit unsupported
   conditions and reviewed source bindings before offering deterministic audits.
3. **Degree/admissions audits:** evaluate supported rules against the selected
   academic records and applicable rule sets, returning satisfied, outstanding,
   and unresolved results with evidence and assumptions. Professional-school
   admissions and Emory graduation are different targets; neither can be inferred
   from generic pre-health advice.
4. **Semester planning:** incorporate actual term availability, prerequisite and
   corequisite sequencing, course load, student constraints, approval conditions,
   and remaining requirements. The existing dependency-stage planner does not
   verify an enrollable semester schedule or graduation plan.

The LLM proposes and explains candidate paths throughout. Deterministic evaluators
enforce supported constraints and expose blockers. An advising recommendation may
be useful before every future rule is modeled, provided its factual basis, scope,
and unresolved checks are made clear.
