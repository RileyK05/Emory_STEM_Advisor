# Feature review — 2026-10-18

A review of candidate additions, not an implementation plan and not a design
contract. Scope calls here are recommendations for discussion; only accepted
items should move into `docs/plans/` or `docs/architecture.md`.

I reviewed the architecture contract, current routes, frontend surfaces, the
audit reports, and the remaining-scope sections. The project is strongest at
grounded retrieval, deterministic prerequisites, artifacts, research advising,
compaction, and the new Admin/Debug/ops base. The largest product gap is the
bridge from “grounded advice” to a **verified academic record and a
deterministic degree/semester plan**.

## My top six

1. **Academic record + transcript import** — the foundation for everything below.
2. **Deterministic degree audit** — satisfied / outstanding / unresolved, no model judgment.
3. **Program comparison / “what-if”** — reuse program requirements and the record.
4. **Source and catalog operations** — refresh diff, provenance review, replacement lifecycle.
5. **Governance** — finer roles, retention/export/delete, cohort flags.
6. **Debug/eval loop** — turn replay, prompt versioning, eval management, feedback promotion.

---

## Student-facing additions

| # | Addition | Why it matters | Effort | My call |
|---|---|---|---|---|
| 1 | **Academic record + transcript import** | Manual entry/CSV first, transcript PDF later; every course carries verified/unverified status and provenance. Required for a real audit. | M–L | **IN — first** |
| 2 | **Deterministic degree audit** | Show satisfied, outstanding, and unresolved conditions per program. Use parsed rules + record; the model explains, never decides. | L | **IN — after 1** |
| 3 | **Program comparison / what-if** | Compare 2–3 majors/minors against completed and in-progress courses: overlap, remaining requirements, likely terms. | M | **IN** |
| 4 | **Semester plan builder** | Turn dependency stages into a term-by-term plan with load, sequencing, and student constraints. | L | **IN, data-gated** — needs actual offerings |
| 5 | **Advising summary / plan export** | One-click artifact: current goal, decisions, open questions, sources, next steps, citations. Reuses the artifact system. | S–M | **IN — quick win** |
| 6 | **Conversation memory inspector** | Show the compacted summary, covered turns, exact source/artifact pointers, and a “forget/reset memory” action. | S–M | **IN** |
| 7 | **Source freshness / applicability banner** | Show catalog edition, program/cohort applicability, and stale-rule warnings on answers and plans. | S–M | **IN** |
| 8 | **Student feedback + triage queue** | Thumbs/reason on answers; route bad or refused answers into the gap/eval queue with the trace attached. | M | **IN** |
| 9 | **Advisor caseload / shared plans** | Advisor sees assigned students, plans, flags, and appointment prep with explicit consent. | M–L | **Needs product decision**; do after roles/retention |

The important boundary: **semester planning is not truthful without term
offerings and section data.** Until that data contract exists, keep it as a
deterministic “sequence/staging” plan, not a registration plan.

## Data and content operations

| # | Addition | Why it matters | Effort | My call |
|---|---|---|---|---|
| 10 | **Catalog refresh diff + approval** | Scrape a new edition, diff courses/prereqs/programs, show additions/removals/conflicts, require human approval, publish atomically. Never model-generated. | L | **IN — high ops value** |
| 11 | **Source/provenance lifecycle UI** | Review title, institution, issuing unit, document roles, applicability; deprecate/replace with impact preview. | M–L | **IN** |
| 12 | **Program requirement review UI** | Show raw requirement lines, extracted course mentions, parser status, unresolved items; bind to the exact source. | M | **IN** |
| 13 | **Duplicate/conflict detection** | Same-named sources, conflicting policies, stale editions, overlapping program rules. | M | **IN LATER** |
| 14 | **Gap resolution → eval case** | A human resolution or student feedback can become a frozen eval candidate. | S–M | **IN** |

## Governance and admin

| # | Addition | Why it matters | Effort | My call |
|---|---|---|---|---|
| 15 | **Finer roles** — student / advisor / admin / auditor | Current `student|admin` is too coarse for real support and compliance. Add audited, time-boxed read-only support access. | M–L | **IN, before pilot** |
| 16 | **Retention, export, delete** | Student data export/delete; conversation/audit retention policy; FERPA-shaped controls. | M–L | **IN, before production** |
| 17 | **Feature flags / cohort rollout** | Enable research, tools, compaction, or new prompts per workspace or cohort. | M | **IN** |
| 18 | **Audit filters, pagination, export** | Current audit is a basic table. Add action/actor/date filters and JSON/CSV export. | S–M | **IN — quick win** |
| 19 | **Usage and quality dashboard** | Per program/collection: refusals, gaps, latency, provider cost, cache hits, feedback. | M | **IN** |
| 20 | **Bulk user operations** | CSV cohort import, bulk role/status changes, session revocation. | S–M | **IN LATER** |

## Debug and evaluation

| # | Addition | Why it matters | Effort | My call |
|---|---|---|---|---|
| 21 | **Turn replay + trace timeline** | Re-run the exact snapshot with the same or new overrides; inspect retrieval seams, selected chunks, prompt, usage, and timings. | M | **IN** |
| 22 | **Prompt versioning, diff, promote** | Version the system prompt and named presets; diff a session prompt against the default; promote with approval. | M | **IN** |
| 23 | **Eval suite management UI** | Add/edit frozen cases, run a config, show recall/regression, block promotion on a regression. | M–L | **IN** |
| 24 | **Cache and cost telemetry** | Aggregate `cached_prompt_tokens`, spend, and cache-hit rate per model/operation. | S–M | **IN — quick win** |
| 25 | **Side-by-side model/config comparison** | Run one query against two models or retrieval configs and diff answers, citations, latency, and cost. | L | **OUT FOR NOW** — depends on per-request model selection |

## Platform and release

| # | Addition | Why it matters | Effort | My call |
|---|---|---|---|---|
| 26 | **Blob storage + private networking** | Required before horizontal correctness and school hosting. Already in the Azure handoff. | L | **IN — release gate** |
| 27 | **Per-student rate limits / quotas** | Admission caps exist; add daily token/query/spend budgets and abuse controls. | M | **IN — release gate** |
| 28 | **Observability alerts** | Queue depth, pool saturation, provider errors, latency, cache, and cost alerts. | M | **IN — release gate** |
| 29 | **Admin/Debug browser tests in required CI** | Current CI covers Main; the new bundles have typecheck/build but no behavior tests. | S | **IN — quick win** |
| 30 | **Accessibility and i18n pass** | Keyboard/focus/screen-reader and localization readiness. | M–L | **IN LATER** |

## What I would keep out for now

- Autonomous background advising that acts on registration, email, or SIS data.
- Native mobile app or offline PWA.
- Public share links / student-to-student social features.
- Real-time registration writes or SIS integration.
- Automated transfer-credit / AP / IB equivalency decisions.
- An LLM judge as the release authority or as proof of factual support.
- Script-enabled HTML/code execution beyond the existing isolated sandbox.
- Generic CRUD for every database table; the curated Admin surfaces are safer.
- Multi-institution tenancy.
- Course availability promises without a confirmed offerings data source.

## One sequencing note

Before adding the degree audit, I’d re-triage the audit findings — especially the
program-split pollution, cross-listed retrieval quota, catalog-replacement/topic-build
lifecycle, and same-name citation navigation. Several pool findings may already
be closed by the recent work, but an audit/plan built on a polluted program
boundary or a stale topic build would undermine trust in the new feature.
