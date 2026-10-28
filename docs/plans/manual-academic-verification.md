# Manual academic data and advisor verification plan

Status: in progress. This plan defines the work; individual checks and their
limits are recorded privately under `data/manual-checks/`. As of 2026-10-27,
coverage is partial: saved Atlas captures have been compared for a subset of
courses, several live advisor questions have been inspected, and program source
and identity issues remain open. These checks do not establish complete or
current online coverage. The [architecture](../architecture.md) is the design contract;
the [advising quality plan](advising-quality-and-data-management.md) and
[round 4 remediation](round4-remediation.md) contain related unfinished work.
Resolve their applicable issues as part of this review without assuming that
either a prior audit or an automated test establishes current correctness.

## Goal and scope

Establish, with human inspection and recorded evidence, what the advisor knows,
whether the academic records match their authoritative sources, and whether a
real configured model can actually use those records through the student-facing
workflow. Cover **every active course and every active program**, including majors,
minors, degrees, concentrations and tracks that are in scope; identify official
items missing from the application as well. Include all schools and campuses,
including Oxford. A missing official item, inaccessible source, unclear rule or
unsupported feature is an explicit gap, not a pass. Historical records are
checked for citation continuity, not silently treated as current rules.

This is a large, iterative project. An exhaustive source review and an exhaustive
model-access check are separate tasks: checking a database row or retrieval alone
does not prove the model received or correctly used it. Conversely, a plausible
model answer does not prove an academic record is correct. Record each result
separately. A parser refresh was applied to the original Atlas data and its
unintended authority-label changes were subsequently restored; the recorded
source revision is 4. Later parser work has only been rehearsed on copies. The
limited live reviews did not exercise the full browser and worker workflow.

## 1. Freeze the review and establish the inventory

1. Name an owner and reviewers with authority to judge academic requirements;
   assign batches by school, campus, department and subject. Have a second
   reviewer resolve ambiguous or consequential findings. Agree on the intended
   catalog edition, academic populations and Atlas terms before grading; do not
   call an unspecified term or cohort verified.
2. Record the application revision including local changes, schema head, source
   revisions/hashes, collection, configuration, provider/model and time of each
   run. Record pending remediation and unavailable functionality. Use a dedicated
   review environment or explicitly identify the existing one; check backups and
   permissions before any mutation. Use synthetic student data.
3. Export a reproducible inventory of active application courses and programs
   and their identities, authority, source, applicability, lifecycle, parser and
   review status. Separately enumerate the official source universe, including
   relevant school catalogs, degree variants, tracks and Atlas search results.
   Preserve exact campus/suffix identities and official source boundaries.
   Compare both directions: in-app but not found, and official but absent in-app.
   An Atlas term's offerings are not the universe of all valid courses. Record
   coverage limitations where a source cannot provide an exhaustive list.
4. Create a local, gitignored review ledger and immutable evidence bundle under
   `data/manual-checks/` (or another approved private data directory). Do not
   commit transcripts, captures, student data, credentials or run output. Each
   row needs a stable identity, school/campus, edition/term, source URL and
   locator, capture date and hash or stored evidence reference, app/source
   revisions, reviewer, result, discrepancy, issue and retest links. Track
   inventory completeness and counts by batch; preserve original findings.
   Restrict access and agree on retention for real-provider traces and outputs.

Use statuses **not started**, **in progress**, **pass**, **fail**, **blocked**,
**not applicable** and **needs academic decision**, with a reason for the last
three. Never count blocked or missing evidence as passed. Maintain separate
source, application display, model access, answer quality and retest statuses.

## 2. Check every course against official evidence

For each exact course identity, a reviewer opens the public Course Atlas UI and
compares the applicable captured offering and registration restrictions with the
application record: code, campus/suffix, title, description, credits, raw
restrictions and their scope. Preserve raw captures and the exact displayed
wording; use public UI only, not hidden APIs. The Atlas is primary for course
requirements where it conflicts with the College Catalog. Check catalog evidence
for records not observed in an applicable Atlas capture and label those as
catalog fallback, not Atlas-verified. Do not infer that a course is invalid
because it is absent from one term or the College master; Oxford is distinct.

Inspect the deterministic parser result against the exact wording, including
AND/OR, alternatives, co-requisites, permission, grades, exclusions, campus
codes and unresolved conditions. Compare the displayed graph and sequence
result with that evidence, including reverse dependencies. Record parser
ambiguity, missing targets and source conflicts; do not invent edges or allow an
LLM to create, repair or suggest graph facts. Also check retired/renamed and
duplicate identities, cross-listed courses where evidenced, and historical
citations. Lack of an offering does not prove a course is discontinued.

Log each discrepancy with its exact evidence and impact. Fix parser behavior or
use the supported reviewed correction/hold workflow as appropriate; never edit
graph tables directly. Prepare and review an Atlas-primary reconciliation before
any publication, preserve fallback labels and source history, and do not run a
plain catalog import over an Atlas-primary master. Recheck affected graph edges,
dependent programs, retrieval and old citations after publication.

## 3. Check every program and its rules

For each official and in-app program identity, inspect the catalog of record
and relevant department material, recording exact edition, school, degree,
campus, track, cohort and applicability. Compare title, degree, credits and
every requirement line with the application and its exact source locator.
Check cores, alternatives, choose-N and elective constraints, prerequisites to
program entry, grades, credit totals, exceptions and track-specific differences.
Treat department supplements and conflicts according to their documented scope;
do not infer equivalence, substitution, double counting or applicability.

Review extraction and exact-source binding, including tables and page breaks;
check that citations open the correct text. Compare parsed rules and published
reviewed releases with the source and test the audit using hand-calculated
synthetic records: satisfied, missing, alternative, partial and ambiguous cases.
Unresolved evidence or rules remain unavailable or clearly limited, not a
verified degree audit. Check missing and retired programs and variants as well
as active ones. Record who made academic decisions, the evidence and the release
review. Recheck downstream program comparisons after any rule publication.

## 4. Prove real model access for each reviewed item

Use the actual configured provider and the real API, worker, retrieval and
student-facing frontend in a controlled environment. First run a small pilot to
confirm the selected route, credentials, indexed current sources, worker and
trace visibility. Record actual provider/model, application and source revisions,
run time, configured budgets, failures and cost/usage where available. Set a
run-wide call/token/time budget before a batch; obtain authorization for any
paid spend or production changes when needed. Never record secrets. A successful
connection test or HTTP 200 alone is not an access or correctness pass.

For **each active course and program**, issue at least one identifiable live
question through the normal workflow. Ask for a specific fact from that item's
reviewed source, with enough identity and scope to distinguish similar records.
For courses include a source-supported description or restriction; for programs
include a specific requirement and variant. Where applicability or data is
unknown, the expected answer is a qualified limitation. Batch execution may
assist logistics, but retain the actual prompt, delivered answer, citation
locators, relevant retrieval/tool trace and reviewer verdict per item. Inspect
the evidence delivered to the model or its actual tool results, and open the
cited passage. If instrumentation cannot establish delivery or tool access,
mark access **unproven** and resolve the observability gap. Check the answer
against the independently reviewed source, not just citation presence. Mark
timeouts, guard withholds, wrong sources, missing items and unsupported claims
separately; do not quietly retry until a pass. Preserve the first attempt and
link every retest to the fix and version.

The existing `scripts.smoke.live_advising` and manual diagnostics may support
the pilot and representative cases; inspect their actual options and output
before use. They do not by themselves cover every record or replace frontend
and human semantic review. Any new reusable runner should use existing provider
and runtime seams, be bounded and manual, and not join the fast test suite.

## 5. Test real advising workflows and boundaries

After item-level checks, manually run representative, source-reviewed cases
across schools, campuses, departments, editions, common and unusual rules. The
scenario set must include:

- Exact course lookup, Oxford versus Atlanta, prerequisite alternatives,
  unresolved restrictions, missing prerequisites, reverse dependencies and
  sequencing with completed versus planned coursework. Verify actual tool
  execution and that stages do not imply registration clearance or offerings.
- Each program family and degree/track variant, close comparisons, shared and
  differing requirements, incomplete evidence, and synthetic degree audits.
  Check recommendations distinguish student preferences from sourced facts.
- Multi-part and long conversations, corrected student assertions, citation
  opening, reload/recovery and a separate-chat privacy check. Verify that each
  requested part is answered or specifically reported unavailable.
- Source selection, uploads and replacement, historical citations, conflicting
  sources and unknown applicability; missing or fictional courses, unsupported
  transfer equivalence, current seats, offering guarantees and other questions
  beyond available evidence. Verify useful limitations and resistance to
  instructions embedded in source content.
- Appropriate role and workspace access, failure/timeout behavior, and the
  actual HTTP frontend rather than mock mode. Exercise representative supported
  browsers and accessibility interactions. Keep findings scoped to the tested
  environment; do not claim an untested deployment works.

For each scenario, independently record expected facts and source references
before viewing the answer, then grade correctness, completeness, grounding,
applicability, usability and appropriate uncertainty. Review consequential or
disputed judgments with an academic subject-matter reviewer. Include both
ordinary student wording and targeted edge cases; existing audit cases are
starting points, not a substitute for current review.

## 6. Triage, fix, retest and report

Prioritize wrong prerequisites, unsupported degree or eligibility claims and
wrong citations ahead of cosmetic problems. For each issue record severity,
affected identities and users, evidence, owner, disposition and dependencies.
Distinguish source acquisition, parser, source binding, retrieval, model,
interface and policy failures. Hold or clearly limit unsafe records while fixes
are pending using existing supported controls; do not claim they are verified.
Fix shared causes in batches, run appropriate offline regression checks, then
repeat the exact live checks against the new version. Re-review all affected
records, not just the example that exposed a shared defect. Invalidate prior
passes when source, parser, rule release, retrieval, model or relevant UI changes;
record the affected scope and recheck it. Preserve historical evidence.

Report counts and denominators separately for official inventory coverage,
source review, model access, answer quality and scenario review, broken down by
school/campus/edition and blocked or unknown status. Record all unresolved
high-impact findings and source limitations. A release decision is a human
decision based on this evidence, not a blanket claim that all future answers or
future catalog editions are correct.

## Completion gates

- The official and application inventories are reconciled within explicitly
  documented source boundaries; every item and gap has an owner and status.
- Every active course and program has an independently evidenced source review
  and an individually recorded real-model access/answer review, or is explicitly
  blocked and excluded from any claim of verified coverage.
- Applicable deterministic graph and program-rule checks match reviewed
  evidence, and consequential ambiguity is held or visibly unavailable.
- Representative end-to-end scenarios, citation inspection, privacy and failure
  checks pass on the identified configuration; fixes have regression checks and
  live retests. Open findings and inaccessible functionality remain visible.
- The final review records exact versions, dates, coverage, remaining blockers,
  responsible reviewers and the scope of any human release decision. Repeat
  review for changed sources, terms, rules or relevant system behavior.
