# Advisor voice and real advice (2026-11-13)

Two changes to the chat advisor:

1. **Advice, not just lookup.** For "What CS electives should I take?" the
   advisor groups the options by interest ("for ML/AI…, for systems…, for
   production software…"), says who each group suits, and gives a pick or two
   with a reason. Every course it names is one it read, and one that is
   currently offered.
2. **Voice follows the theme.** Swoop (light) and Dooley (dark) answer in their
   own voices. The facts, citations and checks stay identical.

Advice is part of the product: data-driven guidance from the advisor's point of
view. It stays grounded in the same way facts are, through the records it cites.

## Why the advisor reads like a search engine today

- `validate_final` (`app/generation/research_final.py`) rejects a `judgment`
  block unless it names a candidate recorded with `advisor_update_research`.
  That costs at least one extra round.
- `normalize_decision` turns a judgment with evidence but no candidate into a
  `fact`. Facts must be supported word for word by their passages, so an
  opinion ("CS 334 is the best start for ML") then fails, or gets cut down to a
  restatement.
- `outcome=recommendation` requires a candidate as well.
- SYSTEM caps every reply at 250 words and "2-4 short blocks". Grouped advice
  for three interests needs about 300-400 words.
- To group 20 electives by interest, the model would have to read all 20
  descriptions (five `read_courses` calls). Nothing tells it which ones belong
  together.

Baseline numbers are in `cost-and-scaling.md`: about 300 rejected finishes per
two weeks were "a judgment block without a candidate".

## Decisions

| Decision | Choice | Why |
| --- | --- | --- |
| How opinions are allowed | A `judgment` needs evidence; a candidate becomes optional | The candidate bookkeeping is right for comparing whole programs, but is pure overhead for "take these if you like X". |
| What keeps opinions grounded | Every course a judgment names counts as recommended, so the existing checks apply: it must be read, exist and be currently offered | The grounding stays in code, not in the prompt. A judgment can't slip in a course the run never opened. |
| Where interest groups come from | A new deterministic tool, `advisor_group_courses`, returns each course's topic from the current similarity graph | The groups are data, not the model's guess, and the model only needs to read the two or three best courses per group. |
| Reply length | Grouped advice may reach 400 words; the byte cap goes from 3,200 to 4,000 | Still phone-sized. Plain lookups keep the current short rules. |
| Where the voice lives | A short style note added to the end of the system prompt, chosen by a per-turn `persona` field | One rulebook for both voices, so facts can't differ. Two fixed prompt variants keep the provider's prefix cache working. |
| Where the voice does not go | Artifacts (documents, sheets, pages), declines, and distress replies | Files get printed and shared, and serious moments need a plain voice. |
| What stays forbidden | Fit scores, job odds, "best major", invented prerequisites, retired courses | Unchanged; they are opinions with no data behind them. |
| Generation | Stays greedy | Voice comes from instructions, not sampling, so evals stay reproducible. |

## Phase 1: advice (backend only)

### 1a. Judgments without candidates (`app/generation/research_final.py`)

- In `validate_final`, require `evidence_ids` on a judgment; drop the
  candidate requirement. Keep validating `candidate_ids` when present.
- In `normalize_decision`, stop converting a candidate-less judgment into a
  fact. Keep dropping citations that were never supplied.
- For `outcome == "recommendation"`, require at least one judgment block;
  candidates stay optional. Program candidates keep the "read and cite program"
  check.
- Add every course code a judgment block names (use the course-code extraction
  the identity check already uses) to `course_recommendations`. The existing
  loop then rejects unread, unknown and no-longer-offered courses.
- Update the SYSTEM lines that say "judgment only for candidate preferences"
  and "Use judgment only for candidate preferences".
- Tests: in the existing final-validation tables (search `tests/` for "judgment
  requires a candidate"), flip the candidate-less judgment row to accepted, and
  add rows for a judgment naming an unread course (rejected) and a retired
  course (rejected). The new rows must fail before the change.

### 1b. `advisor_group_courses` (`app/generation/research_tools.py`)

- Input: 1-24 course codes. Output: `{"groups": [{"topic": label, "courses":
  [{"code", "title"}]}]}` from the current `sim_builds` row, using each
  course's deepest `topic` cluster in `sim_cluster_members`/`sim_clusters`.
  Codes with no topic go in an `ungrouped` list. Add
  `discovery_only: true` and the limitation "Topics are similarity groups from
  course descriptions, not requirements; read a course before describing it."
- Add a `-- name: course_topics` query to `db/queries/simgraph.sql`. No inline
  SQL.
- Register it next to `suggest_courses`. Its description: "Sort course codes
  into topic groups from the topic graph, to organize options by interest."
- Test: one DB test with a tiny simgraph build (reuse the simgraph test
  helpers) showing two courses in one topic, one in another, and an unknown
  code ungrouped.

### 1c. Prompt (`research_controller.py` SYSTEM)

Add a GIVING ADVICE section. Proposed text:

```
GIVING ADVICE
- When the student asks what to take or choose, advise; do not just list. Give your view, grounded in what you read.
- For electives or options, find what counts (read the program), sort it with advisor_group_courses, then read the two or three strongest courses in the groups that fit the student.
- Organize by interest: one block per group, naming who it suits ("If you want ML or AI work...") and one or two courses with a reason drawn from their descriptions. Note a prerequisite chain when it matters.
- End with a short pick when the student gave no interest yet: which group or course is the most broadly useful start, and why.
- Judgment blocks hold your advice and cite the course records behind it. Every course a judgment names must be read and currently offered.
- Grouped advice may reach 400 words. Lookups stay short.
```

Change "Keep the whole reply under 250 words" to apply to lookups, and say
grouped advice may reach 400. Bump `RESEARCH_PROMPT_VERSION`.

### 1d. Limits (`app/advising/research_types.py`)

- `MAX_DECISION_TEXT_BYTES` 3,200 → 4,000; the rejection message says "under
  250 words for a lookup, 400 for grouped advice".

## Phase 2: voice

### 2a. Request field

- `QueryRequest.persona: Literal["swoop", "dooley"] | None = None`
  (`app/api/schemas.py`), and `persona?: 'swoop' | 'dooley' | null` on the
  matching TypeScript interfaces in `frontend/src/api/types.ts`.
  `test_contract_drift.py` must stay green.
- The conversation route already saves `body.model_dump(exclude_unset=True)`
  in the request snapshot. In `app/conversations/worker.py`, pass
  `persona=request_data.get("persona")` when rebuilding `QueryRequest`, as
  `makeArtifact` is.
- `app/api/query_service.py`: pass `persona=getattr(body, "persona", None)` to
  `ResearchController.run`, which stores it on the session like
  `artifact_choice`.

### 2b. Prompt assembly (`research_controller.py`)

- The system prompt is built in two places (around lines 616 and 1054). Replace
  both with one helper `_system_prompt(session)` returning
  `SYSTEM + WORKSPACE_SYSTEM? + VOICE.get(session.persona, "")`.
- `VOICE` holds two short notes. Proposed text:

```
VOICE: SWOOP
You are Swoop, Emory's eagle: upbeat, encouraging, quick. Sound like a friendly senior who loves helping. At most one light touch of personality per reply, never inside a fact, a number or a requirement.

VOICE: DOOLEY
You are Lord Dooley, Emory's skeletal spirit: dry, witty, a little mysterious, an old soul who has watched a century of students. At most one wry touch per reply, never inside a fact, a number or a requirement.
```

- Both end with the same rule: "Declines, distress replies and bad news are
  plain and kind, with no personality. The voice never changes what you say,
  only how."
- Do not give catchphrases; the echo check rejects replies that repeat the
  instructions.

### 2c. Frontend

- `useSavedChat.ts` sends `persona: theme === 'dark' ? 'dooley' : 'swoop'`
  with each turn (read `useTheme()` in the chat component and pass it in).
- `api/mock.ts`: accept the field; optionally vary the canned greeting.
- Each turn uses the theme at send time; earlier replies keep their voice.
- No artifact pipeline change.

## Phase 3: check it live

Run on the dev data with the production model, before and after, both voices:

1. "What CS electives should I take?"
2. "I like biology and coding. What should I take next year?"
3. "Which math courses help most for machine learning?"
4. "QTM or CS for data science?"
5. "What can I take after CS 253?" (a lookup: must stay short)
6. "When is the add/drop deadline?" (a lookup)
7. A distress message, and an off-topic request (voice must be absent)

Pass when:

- Questions 1-4 are organized by interest, with a reason per pick.
- Every named course was read and is currently offered.
- There are no scores or odds.
- Lookups stay short.
- The facts match across the two voices.
- The voice shows but stays light.

Then run `scripts.manual.advising_sweep` to confirm factual accuracy did not
drop. Compare rejected finishes and rounds per run against the baseline in
`cost-and-scaling.md`.

## Open

- Riley to approve or rewrite the two voice descriptions.
- Whether `read_courses` should take six courses instead of four, decided from
  the phase 3 round counts.

## Results (2026-11-14, live on the dev data)

Built on top of the plan, from what live runs showed:

- A judgment's named courses that a cited course record names besides its own
  (a prerequisite) are grounded by that read; a program list's mention is not.
- Numbers and program-inclusion claims in judgments face the same checks as
  facts.
- `advisor_group_courses` drops courses no checked Atlas term shows, and takes
  a subject with a level range, since programs often name "three 300-level CS
  courses" rather than codes.
- `advisor_read_courses` is a queue: up to 24 codes, most wanted first, eight
  read per call, the rest returned as `queued_codes`.
- A rejected finish opens the unread courses its judgments name, so the next
  round keeps the advice instead of dropping it.
- Replies are capped at 450 words (about 400), including the salvage path.
- Malformed calls draw on their own allowance of two before the two content
  corrections.
- Advice questions (`asks_for_advice`) get the `RESEARCH_ADVICE_*` budget:
  high effort, 24 calls, 20 tools, 540 s, 360k tokens. Lookups keep the
  quick budget. The prompt asks the model to plan before its first search.

Live comparison on eight advice questions (four questions, both voices) plus
one lookup:

| | minimax-m3.1-flash, medium | deepseek-v4.1-flash, high |
| --- | --- | --- |
| Grouped or ordered advice | 3 of 4 in the last round | 7 of 8 |
| Time per advice answer | 60-150 s | 29-107 s |
| Lost advice to format slips | yes (before the allowance) | no |
| Lookup (add/drop) | 10 s | 6 s, medium effort |

The voice still varies: Dooley's "two roads, one destination" lands, and some
replies carry almost none. The one miss on DeepSeek was Swoop's
biology-and-coding answer, which listed requirements instead of picks.

Voices toned down (research-56): the notes no longer ask for an opening line
in voice or for the voice to carry through the reply; each allows at most one
light touch, often none. Swoop is described as a helpful upperclassman rather
than a cheerleader (Emory describes Swoop by school spirit, not a written
character). Dooley keeps dry, playful irony, true to the Lord of Misrule
tradition, without theatrical openers or bone and ghost talk. On a rerun of
eight answers, Dooley's asides were small ("the least agonizing path", "full
but honest"), Swoop read almost plain, and both bio-and-coding answers gave
picks. Dooley's electives answer asked the student's interest instead of
grouping.
