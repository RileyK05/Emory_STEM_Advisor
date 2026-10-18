# Advisor working space

Requested 2026-10-16. Implementation plan; `docs/architecture.md` remains the
design contract.

## Problem and intended behavior

The research advisor can already choose scoped tools and revise a comparison,
but its working state is confined to one answer. Saved conversations offer a
short history transcript and eight warm citations. Consequently an older source
can disappear from the model's usable context even though its exact passage is
still saved, and the next investigation loses its plan and open questions.

Give the existing research tool loop a conversation-private working space:

1. Extend its bounded notebook with explicit tasks and concise notes. Tasks may
   be pending, active, done or blocked. Notes distinguish hypotheses,
   student reports and evidence summaries. These are work products, not a log of
   private reasoning or institutionally verified facts.
2. Start each saved investigation with the latest *published* research notebook
   from that conversation and collection. Keep its provenance and label it as
   prior model assessments. Rebind program handles to the new run. Replace old
   evidence numbers with scoped recall handles; never let an old `e1` refer to a
   different passage in the next run. Source changes invalidate dependent notes.
3. Add a paged recall tool over earlier cited passages and published research
   reads. Search the archive on demand, then reopen selected handles from the
   current pinned corpus. Reissue evidence using exact database chunk identities,
   indexes and metadata. This makes passages beyond the warm cap recoverable and
   brings earlier evidence into the research path without treating history as
   authority. Discovery alone never grants citation authority.
4. Include the prior notebook and a compact recall index in rebuilt requests.
   Keep planning and source selection under model control, retain concise task
   progress in the notebook, and reserve finalization capacity under the existing
   operation/tool budgets. Repeated recall reads must reactivate cached evidence.
   Replace the research transcript wall with an explicit whole-turn recent view
   and paged/searchable conversation reads, including cursors inside long turns.
5. Integrate both synchronous and streamed saved turns through the shared
   research entry point. Reuse existing run/state/evidence storage; only published
   turns seed later investigations. Failed, abandoned and other-workspace work
   cannot become a checkpoint. No applied migration needs modification.

## Boundaries

The existing `ADVISING_RESEARCH_ENABLED` opt-in selects this workflow. The
flag-off answer path remains available. This is conversation continuity, not
global student memory, autonomous background work, unrestricted filesystem or
network access, or permission to modify corpus/graph data. Source selection,
workspace ownership, immutable scope, cancellation, provider selection and shared
budgets still govern every read and publication. A previous plan or preparation
check cannot establish current course readiness; deterministic checks run again.
Typed limitations can remain uncited. Citation validation still does not prove
semantic entailment; this change does not claim to solve that separate problem.

## Verification

Extend the existing research data/type tests for notebook validation, publication
and workspace fences, cross-turn recall beyond eight passages, explicit empty or
narrowed selections, changed source revisions, and a scripted multi-turn tool
investigation. Exercise prompt compaction and cached evidence reactivation in
their existing tests. Run focused real-Postgres tests, Ruff, then required broader
backend checks. Document the resulting contract and configuration in the existing
architecture and README.
