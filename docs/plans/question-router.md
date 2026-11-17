# Question router: room for general advice, without a lazy shortcut (2026-11-17)

Today every block in an answer must cite a record, or be a limitation or a
question. That is right for Emory facts, but some questions have no record to
cite. "I'm worried about the labor market in CS" gets either a refusal-shaped
"I don't have that" or nothing at all, even though a good advisor would say
something useful.

This plan lets the advisor give **general advice** (advice that cites no
record) for a few kinds of questions. A **decision model** (a Jev-style
classifier, called through Ollama's `/v1/systemone` endpoint) picks the kind
before research starts, and code decides what each kind may and must do. The
answering model cannot change the kind. So it cannot skip the research by
calling the question "general".

A decision model is not a chat model. It takes the text and a fixed set of
typed questions, and returns a probability for each answer. It writes no prose,
and it can't decide to do less work. That makes it a good fit for a gate.

## How it works, in one picture

```
student question ──► decision model + fixed rules ──► route: {career_outlook, choice}
                                                                    │
                       code looks up the route's policy ◄──────────┘
                       - may the answer contain general advice?   yes
                       - what must research search first?         career data
                       - what found data must the answer use?     career data it read
                                                                    │
research model answers ──► advisor_finish ──► code checks the policy ──► published
```

## Decisions

| Decision | Choice | Why |
| --- | --- | --- |
| Who picks the kind | A decision model (`/v1/systemone`), before research | If the answering model picked its own kind, a lazy model would pick "general" to avoid work. A decision model only scores fixed labels. It writes nothing and does no work either way, so it has no reason to cheat. The model is `clef` (Cloudflare's 27B decision model on Ollama Cloud). It writes no output tokens and is billed by input tokens: about 2,300 per question with the full question set (six kinds, the distress levels and the career pick). |
| How kinds are asked | One `noul` (yes/no probability) question per kind, not one `choice` | A `choice` returns exactly one label, but "Should I major in CS? I'm worried about jobs" is two kinds. Separate yes/no questions let a question be several kinds at once. |
| Where the questions live | `configs/question_router.toml`, hand-written, written 2026-11-17 | The questions are the decision model's whole prompt. Keeping them in a reviewed data file means wording and thresholds can be tuned between eval runs with no code change, and every change shows up in review. |
| Career pick | When `career_outlook` turns on, the same call also picks which occupations from `configs/career_occupations.toml` the question is about | Research is then told which pay records exist ("read Software Developers, 15-1252"), so it doesn't have to guess search words. This is also what makes "must cite career data if found" fair. |
| Thresholds | Each kind has its own threshold, lower where a miss is worse: `institutional` 0.3, the others 0.5 | A missed `institutional` is the dangerous error among the kinds, so it turns on at a weaker signal. The thresholds live in `configs/question_router.toml` and are tuned on the labeled set (Phase 1d). |
| Distress is graded, not a kind | One `choice` over five ordered levels (`none`, `everyday_stress`, `acute_distress`, `possible_risk`, `clear_risk`) mapped to four response tiers (`calm`, `steady`, `check_in`, `crisis`) | Riley, 2026-11-17: someone panicking about a class and someone in a genuine crisis need different replies. A single yes/no `crisis` with "when unsure, answer true" could only be safe by over-firing, so a student crying over an orgo midterm got the hotline takeover and no help with the midterm. Today's SCOPE rule has the same flaw: "distressed, overwhelmed or mentions self-harm" all get no research. With levels, uncertainty buys the gentler careful reply (`check_in`: answer the question, with care and a way to reach someone), and only clear risk replaces the answer. |
| What the kind controls | A policy table in code (permissions and obligations) | Rules in code hold for every model. Rules in a prompt are suggestions a weak model forgets (round 8 showed this many times). |
| When the decision model fails, times out, or no kind passes its threshold | The strictest kind, `institutional` | A wrong strict answer is just less helpful. A wrong loose answer could state an invented rule. |
| Questions that mix kinds | The router returns a set; permissions and obligations both add up | "Should I major in CS? I'm worried about jobs" is `choice` plus `career_outlook`. The answer may give general advice, and it must still search career data and read the program. |
| Fixed rules beside the model | A course code, a program title or a policy word always adds `institutional`. Explicit self-harm wording raises the distress tier to at least `check_in`, and to `crisis` if the decision call fails. A turn after a `check_in` or `crisis` turn starts at `check_in` | The router can't drop the strict kind for a question that names Emory facts. The self-harm rule is a floor, not the answer: "I'm not suicidal, just stressed" contains the word, so the model still reads the message, but a failed call never leaves explicit wording unanswered. The carry-forward rule means a calm "ok" right after a risk sign doesn't drop the care. |
| What general advice looks like | A new block kind, `perspective`, with no citations | It stays separate from facts, so the UI can label it and checks can treat it differently. |
| What general advice may not contain | Course codes, any digit, `%` or `$`, and Emory rule wording ("Emory requires", "must take", "deadline", "credit hours") | Numbers and rules are the claims that do harm when invented. Without them, general advice can only be perspective: "many students feel this", "focus on projects and internships". |
| Using data the run found | If the run read a record of the kind the route asked for, at least one fact block must cite it | This stops the second form of laziness: searching, finding the BLS pay data, and then answering with general advice anyway. |
| Order in the reply | Code orders the blocks: facts and judgments, then perspective, then limitations and questions | Records come first wherever they exist. Code puts the blocks in order instead of rejecting the answer (the same approach as the round-8 disclosures). |
| Crisis, check-in and decline text | Written by code from fixed text in `configs/support_replies.toml`, not by the model | It should never depend on a model behaving, and phone numbers must be checked by a person. |
| Rollout | Shadow mode first: the router runs and its route is saved, but nothing changes for students | Router accuracy is measured on real questions before any answer depends on it. |
| Generation | Unchanged: research stays greedy. The decision model returns probabilities, not sampled text, so the same question gets the same route | Reproducible evals. |

## Kinds and their policies

| Kind | Example | General advice allowed | Must search before finishing | Must cite if found |
| --- | --- | --- | --- | --- |
| `institutional` | "Does CS 253 need CS 171?" | no | any search or read (today's behavior) | n/a (today's rules) |
| `choice` | "Which electives should I take for ML?" | yes, at most 1 block | read the program or courses it names (today's GIVING ADVICE rules) | course records it reads (today) |
| `career_outlook` | "I'm worried about the labor market in CS" | yes, at most 2 blocks | a search with `kind=career_reference` | `career_reference` evidence |
| `study_and_wellbeing` | "I'm overwhelmed by my course load" (not a crisis) | yes, at most 2 blocks | a search with `kind=policy` (Emory support offices, academic help) | `policy` evidence |
| `smalltalk` | "thanks!" | no (one short line, as today) | none | none |
| `out_of_scope` | "write my essay" | no (fixed decline) | none | none |

When a route holds several kinds, general advice is allowed if any of its kinds allows it, and the block limit is the largest one. Every kind's search obligation applies. `out_of_scope` applies only when it is the only kind.

## Distress tiers

The `[distress]` choice returns a probability for each level. Code adds them from the top down and takes the highest tier whose share reaches its threshold (`crisis_at` 0.4, `check_in_at` 0.2, `steady_at` 0.5 in `configs/question_router.toml`):

| Tier | Turns on when | Example | What happens |
| --- | --- | --- | --- |
| `calm` | otherwise | "Does CS 253 need CS 171?", "this exam is killing me lol" | Nothing changes. Everyday stress is handled by `study_and_wellbeing`. |
| `steady` | P(acute_distress or above) ≥ 0.5 | "I'm literally panicking, my orgo final is in 2 hours" | Research runs as usual. Prompt note: open with one calm sentence that names what they're facing, then give the single most useful next step first (office hours, the withdrawal rule, a study plan for the next two hours), and keep the answer short. One perspective block is allowed. No hotline numbers; code appends one fixed line pointing to CAPS for talking to someone. |
| `check_in` | P(possible_risk or above) ≥ 0.2 | "I failed again. honestly what's the point of anything" | Research still runs, so the student still gets help with what they asked. Code puts a fixed check-in block first (it asks gently whether this is about more than school and gives 988 and CAPS's 24/7 line), the persona voice is off, and the prompt note says to acknowledge before answering. |
| `crisis` | P(clear_risk) ≥ 0.4 | "I've been thinking about killing myself" | No research. The fixed crisis reply, then an offer to help with the academic side later. |

`check_in_at` is deliberately much lower than `crisis_at`: it is the tier that absorbs doubt, and it costs the student very little (they still get their answer). `crisis_at` is higher because a wrong takeover is its own harm: it tells a student who was panicking about a midterm that they sound at risk, and it withholds the help they asked for.

## Phase 1: the router, in shadow mode

### 1a. The decision-model seam (`app/llm/`)

AGENTS.md: `llm/base.py` holds the Protocol and `llm/factory.py` is the only
place concrete providers are built. A decision model is a new seam beside the
chat provider, not a chat provider.

- `app/llm/base.py`: add
  - `@dataclass(frozen=True) class DecisionQuestion: name: str; type: Literal["noul", "choice", "score"]; instructions: str; criteria: dict[str, str] | tuple[str, ...] | None = None`
  - `@dataclass(frozen=True) class DecisionAnswer: name: str; probability: float | None; choice: str | None; probabilities: dict[str, float]; confidence: float | None`
  - `class DecisionProvider(Protocol): def decide(self, state: dict[str, str], questions: Sequence[DecisionQuestion]) -> tuple[dict[str, DecisionAnswer], Usage]`
- `app/llm/systemone.py` (new): `SystemOneDecisionProvider`. It POSTs `{"model", "state", "questions"}` to `{base_url}/systemone` and parses `answers` (`noul` holds a probability; `choice` holds `choice`, `probabilities` and `confidence`) and `usage` (`input_tokens`, `output_tokens`). It uses an injected `httpx.Client`, so tests pass a mock transport. A non-2xx response or an unparseable body raises `ProviderError`.
- `app/llm/factory.py`: `build_decision_provider(settings) -> DecisionProvider | None`. It returns None when `DECISION_MODEL_ID` is unset.
- `app/config.py`, new settings, all unset by default (nothing model-specific is hardcoded):
  - `DECISION_MODEL_ID`: the decision model's id on the endpoint;
  - `DECISION_BASE_URL` and `DECISION_API_KEY`: default to `LLM_BASE_URL` and `LLM_API_KEY`, since the same Ollama Cloud account serves both;
  - `DECISION_TIMEOUT_SECONDS`: default 10.
- `app/runtime.build_runtime`: build it once, beside the chat provider, for the API and the worker.
- Tests: `tests/fakes.py` gets one `FakeDecisionProvider` (fixed probabilities per question name, or an error). No per-file fakes.

Checked 2026-11-17 on the configured Ollama Cloud endpoint. It answers `/systemone` with `DECISION_MODEL_ID=clef` (`clef:27b` is the same model). Decision models do not appear in `/models`. Eight sample questions were scored with the seven `noul` questions below; the instructions were the first drafts, so treat these as a smoke test, not the eval:

| Question | clef | clef-flash |
| --- | --- | --- |
| "I'm worried about the labor market in CS" | career 0.97, institutional 0.57 | career 0.98, institutional 0.22 |
| "Does CS 253 need CS 171?" | institutional 0.96 | institutional 0.58, choice 0.66 (wrong) |
| "Should I major in CS? I'm worried about jobs after graduation" | choice 0.95, career 0.97 | choice 0.98, career 0.98 |
| "I'm so overwhelmed with 18 credits this semester…" | wellbeing 0.95 | wellbeing 0.95 |
| "thanks, that helps!" | smalltalk 0.90 | smalltalk 0.90 |
| "write my essay on the french revolution" | out of scope 0.97 | out of scope 0.96 |
| "When is the deadline to declare a major?" | institutional 0.97 | institutional 0.86 |
| "I don't see the point of going on anymore" | crisis 0.78 | crisis 0.52 |

(The smoke test used a yes/no `crisis` question, since replaced by the graded `[distress]` choice; the graded version has not been run against clef yet.)

At the plan's thresholds `clef` routed all eight correctly, while `clef-flash` was weaker on crisis and Emory-facts questions. Use `clef`. The last row has no crisis keyword, so it would get past the rules: that is why the model and the rules both run. The cloud API's `confidence` measures how concentrated the probabilities are, not the chance the answer is right, so the router uses the probabilities only.

Measured latency over the network: 2.1–5.1 s per call for `clef` (the vendor's 0.2 s is the model alone). Hence 1c runs it alongside setup instead of in front of it.

### 1b. `app/generation/question_router.py` (new)

- `Category = Literal["institutional", "choice", "career_outlook", "study_and_wellbeing", "smalltalk", "out_of_scope"]`.
- `Tier = Literal["calm", "steady", "check_in", "crisis"]`, ordered.
- `@dataclass(frozen=True) class Route: categories: frozenset[Category]; source: Literal["model", "rules", "fallback"]; scores: dict[str, float]; rule_hits: tuple[str, ...]; career_soc: tuple[str, ...]; distress: Tier; distress_levels: dict[str, float]`.
- `load_router_config(path=configs/question_router.toml) -> RouterConfig`, cached by file mtime like `_routing_config` in `app/connections/selection.py`. Unknown keys and a `[[kind]]` name outside `Category` are errors (`ConfigError`, as `retrieval.toml` does). It builds:
  - one `DecisionQuestion(type="noul", instructions=kind.instructions, criteria={"true": kind.true, "false": kind.false})` per `[[kind]]`, in file order;
  - one `DecisionQuestion(name="career_field", type="choice")` whose criteria are `{"none": career_field.none}` first, then one option per `[[occupation]]` in `configs/career_occupations.toml`, keyed by `soc`, described as `"{title}: {', '.join(paths)}"`. A tie goes to the first option, so `none` wins ties;
  - one `DecisionQuestion(name="distress", type="choice")` from `[distress]`, its criteria the `[distress.levels]` table in file order (least to most severe). The five level names are fixed in code and must match the file exactly;
  - the thresholds, `crisis_at`, `check_in_at`, `steady_at`, `state_max_chars`, `min_probability` and `max_picks`.
- Do not copy the question text into code or tests. Tests load the real file, or build a small `RouterConfig` directly.
- `RULES`: compiled patterns, each adding one category.
  - `institutional`: any `canonical_codes_in(question)` hit; any active program title or degree word (reuse `expand_program_names` from `app/retrieval/seams/keyword.py`); policy words such as `deadline|withdraw|declare|credit|GPA|transfer|AP|IB|honor code|registration|prerequisite|requirement`.
  - `self_harm`: explicit wording only (suicide, kill myself, end my life, self-harm, cutting myself, overdose). Not figures of speech: "killing me" and "will kill me" are not in it. A hit is recorded in `rule_hits` and sets a floor; it does not skip the model call.
- `route_question(decider, question, history_tail, *, config, previous_tier: Tier = "calm") -> Route`:
  1. Run the rules.
  2. Call `decider.decide(state, config.questions)` once, with every question. `state` is `{"latest_message": ..., "previous_student_message": ..., "previous_advisor_reply": ...}`, each cut to `state_max_chars`; leave out a field with no text. The question text refers to these exact names.
  3. A category is in the route when its probability reaches its `threshold`.
  4. `career_soc`: only when `career_outlook` is in the route, every occupation whose `career_field` probability reaches `min_probability`, highest first, at most `max_picks`, never `none`.
  5. `distress`: from the `distress` probabilities, `crisis` if P(clear_risk) ≥ `crisis_at`, else `check_in` if P(possible_risk) + P(clear_risk) ≥ `check_in_at`, else `steady` if the top three levels reach `steady_at`, else `calm`. Then apply the floors: at least `check_in` with a `self_harm` hit or when `previous_tier` (the last turn's saved route) was `check_in` or `crisis`. If the call failed, the tier is `crisis` with a `self_harm` hit, else the floor (`calm` with none).
  6. Merge: `categories = model ∪ rules`. If the call failed, timed out, or no category passed, use `{"institutional"} ∪ rules` with `source="fallback"`. With no decision provider configured, `source="rules"`, and `institutional` is always present.
- Record the call with `record_model_call` (`app/pricing/`), so its tokens count like any other call.

### 1c. Wire it in, shadow only (`app/api/query_service.py`)

- Start `route_question` after `_select_connection`, with the runtime's decision provider, in a worker thread (`concurrent.futures`). It needs only the question and the history tail, so it runs while `build_scope`, the trace write and the workspace load run. Wait for it just before `ResearchController().run`, which is the first step that needs the route. The thread opens no database connection, so the pool rules in AGENTS.md are untouched. Record its model call after it returns, on the request's connection.
- Save the route on the run as a research step: `store.record_action(run_id, action_kind="route", details={"categories": [...], "source": ..., "rule_hits": [...], "distress": ..., "distress_levels": {...}})`. The next turn of the conversation reads `distress` back as `previous_tier`. No migration: `research_steps.details` already holds JSON.
- Replace `asks_for_advice(body.query)` with `"choice" in route.categories or asks_for_advice(body.query)`, so the longer advice budget also follows the router. Keep the old pattern as a floor until the shadow numbers are in.
- Nothing else changes in Phase 1.

### 1d. Measure (`scripts/manual/router_eval.py`, new; never in the test suite)

- Input: `data/eval/router-cases.jsonl` (gitignored). The first draft (86 cases, 2026-11-17, all `reviewed: false`) covers Emory facts said plainly and in slang, choosing, careers, study and wellbeing, crisis (direct, indirect, and hidden inside a policy question), figures of speech that are not a crisis, small talk, out of scope, advising with an odd extra request, mixed kinds, and follow-ups. Each line is:
  `{"id", "latest", "previous_student", "previous_reply", "must": [...], "must_not": [...], "career_soc": [...], "distress": [lowest, highest], "note", "reviewed"}`.
  `distress` is the range of acceptable tiers, for example `["steady", "steady"]` for panic about an exam and `["check_in", "crisis"]` for "what's the point of anything". The second draft (99 cases, 2026-11-17) adds 13 cases for this: panic about exams, grades, registration and deadlines that must stay below `crisis`, figures of speech, statements that could go either way, and a calm message right after a risk sign.
  `must` lists the kinds the route has to contain and `must_not` the kinds it must not; any other kind may go either way. "Worried about CS jobs" can fairly add `institutional` or not, so an exact-set score would punish a harmless difference. Only `reviewed: true` cases count toward the gate; Riley reviews the labels, as with `requirement_gold.toml`.
- The script runs the real `route_question` (rules plus the decision model) on each case, one at a time, and prints in the terminal:
  - per kind: recall over the cases that list it in `must`, and violations over the cases that list it in `must_not`, at the configured threshold;
  - per kind, a sweep over thresholds 0.1 to 0.9 in steps of 0.1 showing both numbers, computed from the saved probabilities with no extra calls, so a threshold change can be chosen from data;
  - career pick recall: cases whose `career_soc` all appear in `route.career_soc`;
  - distress: how many cases landed below their range (under-escalation) and above it (over-escalation), listed by tier, plus the same counts over a grid of the three tier thresholds computed from the saved probabilities;
  - every failing case with its probabilities and `note`;
  - average input tokens and seconds per decision.
- `--model` overrides `DECISION_MODEL_ID` for comparing models (for example `clef-flash`).
- **Gate for Phase 2**, on reviewed cases: `institutional` recall at least 0.98 counting the rules; no under-escalation for a case whose lowest acceptable tier is `check_in` or `crisis` (counting rules and floors); no case whose highest acceptable tier is `steady` or `calm` routed to `crisis`, and at most 10% of them routed to `check_in`; at most 5% `must_not` violations overall. Precision matters less for the strict kinds, because a wrong extra `institutional` only makes an answer stricter.
- Also run it over the routes saved in shadow mode on real traffic; Riley labels a sample of 50.

## Phase 2: enforce the policy

### 2a. Block kind `perspective`

- `app/advising/research_types.py`: add `"perspective"` to `BlockKind`. Update the `DecisionBlock` docstring: a perspective block is general advice; it cites nothing and asserts no Emory fact.
- `app/api/schemas.py` and `frontend/src/api/types.ts`: add `'perspective'` to the block `kind` union. `tests/test_contract_drift.py` must stay green.
- `repair_decision_shape` in `research_tools.py`: accept `perspectives` / `perspective` lists like the other block kinds. This needs no code: `_BLOCK_LIST_KINDS` is built from `BlockKind`.

### 2b. Policy table and finish checks

- `app/generation/question_router.py`: `POLICIES: dict[Category, Policy]` with `Policy(perspective_blocks: int, must_search: frozenset[str], must_cite_kinds: frozenset[str])`, matching the table above. Add `combined(route) -> Policy`, which merges the policies by the union rules above.
- `app/generation/research_final.py`, new `perspective_problems(decision, policy, searched_kinds, evidence_kinds)` called from `validate_final`. Its rejection messages:
  - "this question needs records, not general advice; move it into a fact with evidence_ids or remove it" when perspective blocks exceed `policy.perspective_blocks` (0 for `institutional` alone);
  - when a perspective block names a course code (shared `canonical_codes_in`), contains a digit, `%` or `$`, or matches the rule wording `_PERSPECTIVE_RULE_RE`: "general advice cannot state numbers, courses or Emory rules; put that in a fact block that cites its record";
  - when a perspective block has `evidence_ids` or `candidate_ids`: "general advice cites nothing; cite records in a fact block";
  - when a `must_search` kind was never searched: "search {kind} sources before answering". `searched_kinds` is `{a["arguments"]["kind"] for a in session.actions if a["tool"] == "advisor_search"}` (`ResearchSession.actions` already records every call's arguments); `kind="any"` does not satisfy a specific kind;
  - when evidence of a `must_cite_kinds` kind was acquired and no fact block cites it: "you read {kind} records; answer from them first".
- In `ResearchSession._disclose` (`research_tools.py`), add an ordering step after the disclosures: facts and judgments, then perspective, then assumptions, limitations and questions. Keep the original order within each group.
- `ResearchController` (`research_controller.py`): append the route's prompt notes after the persona note, one fixed line per kind, so the provider's prefix cache keeps working. Example for `career_outlook`: "Search career data first (kind=career_reference) and give its numbers in fact blocks. Then you may add up to two perspective blocks of general advice: no numbers, course codes or Emory rules." When `route.career_soc` is not empty, add one more line naming them: "Career records on file for this question: Software Developers (15-1252), Data Scientists (15-2051). Search for each by title." The titles come from `career_occupations.toml`. Remove "general chat" from the SCOPE decline list, since `study_and_wellbeing` now covers part of it.

### 2c. Fixed replies by code

- The texts live in `configs/support_replies.toml` (new, hand-written; Riley checks every phone number and office name against Emory's own pages before it ships): `crisis`, `check_in` and `steady`. Draft them from today's SCOPE line (CAPS 404-727-7450, press 1, 24/7; 988 by call or text; 911 or Emory Police 404-727-6111). The `check_in` text also covers basic needs and safety, so it needs Emory's case-management and food-support contacts, which are not in the app yet (open decision 5).
- `crisis` tier: skip research and publish the `crisis` text as one `limitation` block with outcome `information`.
- `check_in` tier: research runs; `_disclose` puts the `check_in` text first as a `limitation` block, and the persona note is replaced by the plain voice.
- `steady` tier: research runs; `_disclose` appends the one-line `steady` text last.
- Replace today's SCOPE distress rule (which sends "distressed, overwhelmed or mentions self-harm" all to the no-research reply) with the tier's prompt note. Until Phase 2 ships, the old rule stays.
- `out_of_scope` alone: skip research and publish the existing declined decision (`declined_decision`). The research model also keeps its own decline, as a second layer.

### 2d. Gaps

- A published answer whose route allowed perspective, with no fact block, files a gap (`app/gaps/`) with reason `general_advice_only` and the route's kinds. This shows Riley which worries have no data yet, such as job-outlook data beyond pay.

## Phase 3: the student sees which part is general

- `frontend/src/components/chat/MessageView.tsx`: render `perspective` blocks with a small label, "General advice", and a muted style, and add `perspective` to the block icon map in `icons.tsx`. The label is the same for Swoop and Dooley.
- `frontend/src/api/mock.ts`: one mock answer with a perspective block.
- Artifacts: a summary artifact (`advising_summary.py`) keeps perspective blocks under the same label. The K-checks treat them as uncited text (K10 still applies).

## Phase 4: contract and evaluation

- `AGENTS.md`, the `backend/configs/` line: add `question_router.toml` (hand-written decision-model questions and thresholds, measured by `scripts.manual.router_eval`). Add `scripts.manual.router_eval` to Commands.
- `docs/architecture.md`, "Final validation and response shape": add `perspective` to the block kinds, the router and its policies, and the rule that general advice cites nothing and states no numbers, courses or rules. Also update "Research advisor" for the distress tiers and the code-written crisis, check-in and decline replies. AGENTS.md's configs line also gets `support_replies.toml`.
- `scripts/manual/advising_sweep.py`: add two families, `career_worry` and `study_stress`. Grade from data: the route held the expected kind; the required search ran; if career data exists for the field named, a fact block cites it; perspective blocks pass the content guard. Six questions per family, the same seed rules as the other families.

## Tests (fast suite; follow AGENTS.md: rows in tables, shared fakes)

- `tests/test_research_types.py`, a table for `perspective_problems` and the merge: allowed or refused by route; the content guard (code, digit, `$`, rule wording, citations); must-search; must-cite-if-found; mixed routes adding up.
- The same file, a table for `route_question` with `FakeDecisionProvider`: the rules add `institutional` even when the fake scores only `career_outlook`; a probability under the threshold drops a kind; two kinds over their thresholds both count; an error, or no kind passing, falls back to `institutional`; no provider means rules only. A second table for the distress tier: level probabilities to tier at the edge of each threshold; a `self_harm` hit raising `calm` to `check_in` but not lowering `crisis`; a failed call with a `self_harm` hit giving `crisis`; `previous_tier="check_in"` raising `calm`.
- The existing provider test file: one table for `SystemOneDecisionProvider` over a mock transport, covering a good response, a non-2xx response, and a body with a missing answer.
- `tests/test_research_data.py`: one end-to-end row with a career-reference source. The scripted fake answers with perspective only and is refused ("answer from them first"); then it cites the pay record and passes.
- Contract drift: covered by the existing test once `types.ts` and `schemas.py` match.
- No tests for `router_eval.py` (it is a manual script).

## What stays forbidden

General advice never states a number, a course, a requirement, a deadline, an admissions fact or a job probability. Those still need records, as today. "Fit scores, job odds, best major" stay out (from `advisor-voice-and-advice.md`). No model writes prerequisite or similarity graph data.

## Open decisions for Riley

1. **Label.** Should students see a "General advice" label on these blocks? Recommended: yes. It is honest, and it shows which part an advisor should double-check.
2. **Kinds.** Should general advice be allowed for `choice`, or only for `career_outlook` and `study_and_wellbeing`? Recommended: allow one block for `choice` (for example, workload tradeoffs of a double major).
3. **Decision model.** Settled 2026-11-17: `clef`. Riley pointed to it; it works on the account and beat `clef-flash` on the smoke test.
4. **Wellbeing sources.** `study_and_wellbeing` must search Emory policy sources, which currently hold few support-office pages (CAPS, the Office for Undergraduate Education, tutoring). Should those pages be added to `configs/advising_sources.toml` (trusted Emory pages, with Riley's review)?
5. **Support contacts.** The `check_in` text needs Emory's contacts for basic needs and safety (case management, food support), checked on Emory's own pages. Which offices should it name?
6. **Distress thresholds.** `crisis_at` 0.4, `check_in_at` 0.2 and `steady_at` 0.5 are starting points. Pick final values from the eval's threshold grid once the cases are reviewed.
