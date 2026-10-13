# Systematic audit — 2026-10-10

Requested read-only sweep by three smaller Luna agents plus the coordinating agent. Only audit Markdown was added; implementation, migrations, development data, keys, and graph state were not changed. These are review findings, not a new design contract or a claim that the project is finished.

The user's subsequent decisions and the full proposed implementation sequence are in [repair-plan.md](repair-plan.md). That plan adopts top-down department/topic search, explicit cross-list identity, and renewed human review when depended-on evidence changes. The design questions below describe the audit's earlier state; use the repair plan for the current direction.

## Start here

| File | Scope |
| --- | --- |
| [Data and advising](data-and-advising.md) | Ingestion, catalog programs, retrieval, answer grounding, deterministic prerequisites, department/topic graph |
| [Frontend](frontend.md) | API/client/state, connection forms, sources/citations, conversation and artifact flows |
| [Runtime](runtime.md) | Connection ownership, model routing, API/auth/scoping, runtime/deployment boundaries |
| [Workflows](workflows.md) | Sampled artifact/job/tool/budget/sandbox/conversation/gap transitions |

**12 findings are marked confirmed:** seven data/advising issues, three frontend issues, and two runtime issues. Some are previously observed answer-quality failures; others were reproduced here or established by control-flow inspection. The defensive evidence-stitching case is not known to occur in normal retrieval. Two additional items are design questions, not established bugs. Each report also identifies intentional behavior to preserve.

## Fix order and evidence

| Priority | ID | Finding | Evidence level |
| --- | --- | --- | --- |
| First | DA-06 | Atlas removal can cascade-delete all topic clusters while their build remains current | New disposable-DB reproduction: 13 courses survive, zero clusters/recommendations |
| First | DA-01 | Final catalog program swallows unrelated department appendix | Cached source inspection, earlier live failures, new synthetic reproduction |
| First | DA-05 | SQL seam errors leave the shared retrieval transaction aborted | New disposable-DB reproduction |
| First | DA-02 | Cross-listed descriptions exhaust embedding-only quota and hide specific courses | Earlier REL 340 failure traced through storage/ranking/fusion; new pure reproduction |
| First | DA-04 | Citation syntax validation accepts unsupported factual claims | Earlier Italian suffix claim; new fake-provider reproduction |
| Next | RT-01 | Abandoning a conversation job self-starves a one-slot default DB pool | Connection ownership inspection; API reproduction still needed |
| Next | RT-02 | Tool calls from synchronous queries/conversations self-starve the same one-slot pool | Connection ownership inspection; API reproduction still needed |
| Next | UI-02 | Switching credential replacement targets retains the previous draft | Form state and submit-path inspection |
| Next | UI-01 | Provider changes retain incompatible hidden fields/roles | Frontend state and backend validation inspection |
| Next | DA-03 | Lexical boilerplate occupies context despite poor topical relevance | Earlier diverse live cases and current scoring/filter inspection |
| Next | UI-03 | Same-named sources make citation navigation highlight the wrong row | Allowed source identity + lookup inspection; synthetic Node example |
| Low | DA-07 | A later ambiguous chunk index erases recognition of an earlier complete requisite evidence run | New pure reproduction; defensive input edge case |

DA-01/02/03 overlap in the relevance failure chain. Correct the polluted source boundary before attributing all lexical failures to ranking. DA-04 remains an independent generation limitation even with perfect retrieval. RT-01/02 share connection-lifecycle mechanics; address that ownership coherently rather than just raising pool capacity.

## Questions that require a decision or broader evidence

- **DA-08:** Should a strong small topic admit a department that its aggregate centroid excluded? Current department gating is configured behavior, not an established defect.
- **WF-01:** Does a human gap resolution capture point-in-time source validity, or require validity fenced through commit? The contract does not establish the stronger interpretation.

## Preserve while refining

- Fixed departments → learned within-department topics → courses; containment links only. Embeddings serve semantic lookup. No lateral related-course graph.
- Deterministic prerequisite parsing/planning; no model-created graph facts. Ambiguity, permission, grades, and missing evidence remain blockers.
- Current-source/workspace scoping, stable content identity, historical evidence, atomic source replacement, expected revisions, lease/cancellation fences, and conservative budget reservations.
- Unknown provider outcomes are not blindly replayed. Artifact revisions remain immutable. HTML stays inert; Office calculation/rendering requires the existing isolation gates.

These safeguards are explained in the reports so another model does not “fix” them away.

## Validation and limits

The coordinator checked the agents' findings against relevant code, corrected status/scope overclaims, and verified the new pure/disposable-DB reproductions. The runtime agent's focused offline selection finished with 70 passes and 19 DB tests intentionally deselected; it does not reproduce the two pool bugs. The frontend agent ran a synthetic name-lookup example, not browser tests. The workflow agent performed static path review only.

No new paid/live-model sweep, full backend/frontend suite, deployed sandbox/Office check, or production-provider batch check was performed. The earlier 12 live questions are exploratory evidence, not a statistical accuracy score. Areas were assigned systematically, but the code reviews are focused samples, not exhaustive line-by-line certification. No proposed fixes were implemented as part of this audit.
