# Data and advising audit

Requested audit, 2026-10-10. Implementation code was read, not edited. The earlier 12-question live sample is explicitly identified below; this audit made no new real-model calls. New reproductions used in-memory fakes or disposable Postgres instances, never the development database.

Priority: **P1** substantial user-visible failure; **P2** narrower failure; **P3** defensive edge case. Confirmed means observed previously or reproduced here; it does not mean fixed.

## Confirmed findings

### DA-01 — P1: the last catalog program absorbs the department appendix

**Trigger / impact:** Split the cached official catalog. The final RWID minor extends through pages 119–158, including roughly 40 pages of unrelated department information. Those passages acquire the wrong program label and compete as relevant context. The previous live sample selected this polluted source for unrelated religion, Italian, robotics, and computational-linguistics questions.

**Evidence:** [catalog_pdf.py:67](../../backend/app/programs/catalog_pdf.py#L67) ends the last program at the end of the entire input, without detecting another section type. The cached [RWID document](../../data/programs/catalog/writing-rhetoric-and-information-design-rwid-minor.md) labels pages 119–158 and begins “African American Studies Department” at line 42. A synthetic last-program input followed by an unrelated department reproduces the same inclusion.

**Next action:** Detect the program/department boundary from catalog structure. Preserve the appendix under appropriate department identities; regenerate the affected source and program metadata using origin-aware replacement. Do not delete historical evidence or infer prerequisite facts.

### DA-02 — P1: cross-listed descriptions consume the embedding quota

**Trigger / impact:** Course chunks with the same description but different code/title headers remain distinct. They can fill the four embedding-only slots while a more specific match is discarded. In the earlier live question about Jewish law and institutions in 100–500 CE, REL 340 was present in storage and ranked sixth semantically, but the final context retained three broad survey aliases and dropped it. JS 340, another exact match, ranked fifteenth.

**Evidence:** [fusion.py:150](../../backend/app/retrieval/fusion.py#L150) deduplicates complete stripped text, including course headers. [fusion.py:159](../../backend/app/retrieval/fusion.py#L159) then caps embedding-only candidates whenever any surviving keyword/prerequisite candidate exists. The configured quota is four in [retrieval.toml](../../backend/configs/retrieval.toml). An offline reproduction retained three duplicate bodies with distinct headers and excluded candidate six.

**Next action:** Evaluate description-aware diversity while retaining actual course/citation identities and explicit aliases. Compare the frozen broad question sample before changing quotas or ranking; raising one quota to rescue one question is not sufficient evidence.

### DA-03 — P2: lexical boilerplate can win despite weak topical relevance

**Trigger / impact:** Generic words such as “work,” “first,” or “fit” survive the conversational-word filter. OR matching can rank Honor Code, Incomplete Work, or polluted department material above course evidence. These unrelated chunks occupied context slots in the earlier robotics, satellite-imagery, Russian-history, and justice questions.

**Evidence:** [keyword.py:16](../../backend/app/retrieval/seams/keyword.py#L16) filters a fixed scaffold list; [keyword.py:57](../../backend/app/retrieval/seams/keyword.py#L57) joins the rest with OR. [keyword.sql:5](../../backend/app/db/queries/keyword.sql#L5) uses full-text rank without a topical sufficiency gate. [fusion.py:32](../../backend/app/retrieval/fusion.py#L32) normalizes the best result to 1 even when all lexical matches are weak. This limitation is already acknowledged in [config.py](../../backend/app/retrieval/config.py#L55); min-score filtering does not solve it.

**Disposition:** Confirmed answer-quality failure, not proof that OR matching or min-max normalization is inherently wrong. No new lexical DB benchmark was run here.

**Next action:** Correct source pollution first, then compare lexical scoring and mixed-context policies on the same varied cases. Measure topical coverage and irrelevant slots; do not just expand a stop-word list for each failed prompt.

### DA-04 — P1: valid citation markers do not enforce supported claims

**Trigger / impact:** A completed answer with an in-range citation can assert facts absent from that passage. The earlier Italian answer called ITAL 301W a “writing-intensive version,” although none of its supplied passages established that suffix meaning. Other answers padded shortlists with program-only titles without consistently disclosing missing descriptions.

**Evidence:** [answer.py:113](../../backend/app/generation/answer.py#L113) checks paragraph citation presence and valid reference numbers. [answer.py:266](../../backend/app/generation/answer.py#L266) checks completion/refusal status, not factual support. The prompt explicitly forbids suffix inference, but that is a model instruction rather than an enforced invariant. [claim_review.py:3](../../backend/app/generation/claim_review.py#L3) states that optional claim assessment does not run during ordinary query handling.

**Reproduction:** With context “CS 100: Introduction to programming,” the fake completed output “CS 100 guarantees a six-figure job. [1]” was accepted by `answer_from_context`. The synthetic guarantee illustrates validator limits; it was not a real-provider answer.

**Next action:** Add semantic acceptance examples and enforce tractable course identity/coverage facts where possible. Evaluate any optional assessment separately; another model's approval is not deterministic proof of support. Keep citation integrity validation.

### DA-05 — P1: a SQL exception poisons the shared connection instead of isolating one seam

**Trigger / impact:** A retrieval seam encounters a PostgreSQL statement error, such as a statement timeout or invalid SQL/vector operation. `_run` records `state='failed'`, but the shared transaction remains aborted. Subsequent seam, chunk-detail, or trace statements fail, so the advertised seam isolation does not hold for SQL errors.

**Evidence:** [retrieve.py:30](../../backend/app/retrieval/retrieve.py#L30) catches exceptions without a transaction/savepoint boundary or rollback. [retrieve.py:42](../../backend/app/retrieval/retrieve.py#L42) runs all seams on that same connection. Existing [test_retrieve.py:110](../../backend/tests/test_retrieve.py#L110) and [test_retrieve.py:133](../../backend/tests/test_retrieve.py#L133) exercise Python exceptions rather than aborted SQL transactions.

**Reproduction:** In a disposable migrated Postgres instance, an injected keyword seam executed `SELECT 1 / 0`. Seam states were keyword failed, embed dormant, prereq dormant; the next SQL statement raised `InFailedSqlTransaction`. An explicit rollback restored the connection. No development rows were touched.

**Next action:** Isolate SQL-bearing seams with appropriate transaction/savepoint ownership. Do not blindly roll back the caller's unrelated work. Add a real Postgres regression proving retrieval/trace behavior after one statement failure.

### DA-06 — P1: catalog replacement can destroy a still-current topic build

**Trigger / impact:** Replace an atlas with one that removes a course used as a cluster medoid. Course deletion cascades into that cluster and its descendants. If it is the root medoid, every cluster disappears. The build remains marked current, and surviving courses return no recommendations. With stable codes but changed descriptions, the old centroid/membership snapshot can also remain current until an explicit rebuild; source freshness is not bound to build freshness.

**Evidence:** [importer.py:164](../../backend/app/atlas/importer.py#L164) deletes absent courses. The immutable baseline's [medoid FK:2780](../../backend/app/db/baseline/032_schema.sql#L2780) and [parent FK:2788](../../backend/app/db/baseline/032_schema.sql#L2788) both cascade deletes. The import path does not retire the old topic build. [simgraph.sql:55](../../backend/app/db/queries/simgraph.sql#L55) serves by `is_current` and graph version without an atlas revision fence.

**Reproduction:** Existing planted-vector helpers imported 14 fake courses and built six clusters. Reimporting 13 courses without root medoid CS 170 left zero clusters, a still-current build, and zero recommendations. Disposable DB only. The changed-description variant is established by data flow, not separately reproduced.

**Next action:** Bind topic builds to their input snapshot and retire/rebuild them coherently on catalog replacement. Preserve immutable historical build identity without live-course deletion silently destroying it. Any schema repair must be a new migration; do not edit the applied baseline or archive. Parser-only requisite refresh should preserve an unchanged topic build.

### DA-07 — P3: a later ambiguous chunk index discards earlier complete evidence

**Trigger / impact:** `_requirements_are_cited` receives a complete consecutive evidence run followed, after an index gap, by two different texts for one index. It checks the new index's ambiguity before accepting the previous completed run, and returns false. That unnecessarily blocks a valid prerequisite sequence.

**Evidence:** [catalog.py:160](../../backend/app/advising/catalog.py#L160) gates recognition of the preceding run on `len(alternatives)` belonging to the next index.

**Reproduction:** Chunks 0/1 containing “Prerequisites: MATH 111” / “and MATH 112” return true. Adding conflicting unrelated chunks at index 3 changes the result to false. Normal retrieval from unique current chunk rows should not create this duplicate-index input, so this is a defensive helper edge case, not an observed production failure.

**Next action:** Validate the completed run independently before resetting it, while retaining rejection of ambiguous stitched evidence. Add a focused pure regression if this helper is changed.

## Design question, not a confirmed defect

### DA-08 — Department centroid gating can hide a strong small topic

[walk.py:56](../../backend/app/simgraph/walk.py#L56) selects departments by their aggregate centroid and top-margin/floor thresholds, then searches all descendants only inside selected departments. The descendant search correctly removes intermediate-topic centroid barriers, but a diverse department can still hide a strong small topic if its department centroid misses that first gate.

This is a deliberate configured search policy, not proof of a bug. Decide whether goal discovery should also admit departments via a sufficiently strong topic match. A stored-vector, broad-interest comparison is needed before changing it. No extra graph rebuild or live goal benchmark was performed in this audit.

## Things that look odd but are correct

- **Stored course vectors are not course-similarity edges.** [department_tree](../../backend/app/simgraph/build.py#L135) partitions by fixed department before clustering; [view.py](../../backend/app/simgraph/view.py) emits unweighted containment links. Temporary within-department cosine calculations implement the user's intended hierarchy. Do not restore lateral related-course links.
- **An ambiguous/unparsed course can help retrieval without becoming a planner edge.** [prereq.py](../../backend/app/retrieval/seams/prereq.py) uses explicit mentions to locate evidence. [importer.py:89](../../backend/app/atlas/importer.py#L89) writes executable requisite trees only for fully parsed records; [sequence.py](../../backend/app/advising/sequence.py) blocks unresolved facts. That distinction is intentional.
- **Unknown conditions block even if a human might guess the answer.** Senior standing, permission, grades, unresolved alternatives, and incomplete citation coverage are not model-repair opportunities. Completed courses remain disclosed student assertions. Dependency stages are not verified semester schedules.
- **Parser refresh preserves vectors and graph identity.** [refresh_atlas_requisites](../../backend/app/atlas/importer.py#L94) requires byte-identical current input, checks manual changes, updates parser metadata, and advances the source revision when facts change. That invalidates durable source snapshots without unnecessary embedding or topic rebuilding.
- **Failed replacement retains last-good data.** [pipeline.py:106](../../backend/app/ingest/pipeline.py#L106) prepares outside publication, then fences source/origin publication and atomically replaces. SQL deliberately keeps an existing indexed source indexed on failed attempts. This is correct; DA-06 concerns topic-build lifecycle after successful catalog replacement.

## Coverage and limits

Reviewed ingestion extraction/chunking/publication, atlas import/parser refresh and prerequisite retrieval, program splitting/import/requirement parsing, retrieval seams/config/fusion/citation attachment/trace, answer prompt/citation/completion validation, advising context/evidence stitching/planner, department-topic construction/view/walk, and related named SQL/tests. This was a focused path review, not a line-by-line certification of every script, parser branch, or generation artifact/batch feature; those workflow areas are in the companion audit.

The prior 12 live questions were a small exploratory draw, with six description-anchored questions and six career interests. They are not a statistical accuracy estimate. Earlier results are labelled as such; no rerun or new paid provider sweep was done here. No implementation fix, schema migration, development DB repair, re-ingestion, or rebuild was performed.
