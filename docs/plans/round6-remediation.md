# Round 6 remediation

Written 2026-11-12 from `REPORT-round6.md` (tested at `1b2ad31`). The report's
fix order is the order below. This is the implementation status, not a claim
that the live sample has been rerun.

## Done

| Finding | Change |
|---|---|
| EMO-077 plan requests became checklists | The artifact job carries the student's own turn (`requestText`). A plan request is decided from those words, never the research brief, and a plan outline can no longer fall back to a free-form checklist. A full program plan must select at least six of the program's courses. |
| EMO-089 "Term N" was a prerequisite layer | `advising.sequence.pack_terms` packs the dependency stages into real terms with a credit cap (default 16), course cap (default 5), the requested number of terms, offered Fall/Spring seasons, and lecture/lab corequisite bundles. Student caps ("12 credits", "3 courses a term", "4 semesters", "3-year") are read from the request. A course that cannot fit gets an explicit refusal instead of a silent drop. |
| EMO-080 one-program planner | The outline can name `programs` as well as `program`; their allowed codes are unioned. A real course outside the first program (premed, a minor, a second major) is kept as an additional course and excluded from K5's program check. |
| EMO-075 plan rename aliases | `plan_sequence` resolves QTM 150 to DATASCI 150 when matching prerequisites, and a selected old code is canonicalised before lookup. |
| EMO-085 attached-artifact prompt bound | The research prompt receives a compact projection of each attached revision (titles, headings, list items, table/sheet rows; no evidence, page layout or full spec). The full revision stays on the server. |
| EMO-082 / EMO-088 no handoff | Artifact-ask detection now covers "Plan the rest of my…", "Plan four years", "Premed plan for…", "Lay/map out…", "I need a schedule", and "what should my semesters look like". A reply cannot say an artifact is "being written/made" when no artifact is expected. |
| EMO-083 fake code or rider killed the artifact | A fake code in a degree-named request is dropped from the plan instead of suppressing the artifact; a code-only fake request still makes none. The research rule distinguishes an advising request with an injection rider (ignore the rider) from a wholly out-of-scope request. |
| EMO-081 summary suffix and list | A missing code with one current suffix variant is printed as that variant (PHYS 495 → PHYS 495R); a run of missing codes collapses to one phrase. |
| EMO-090 non-canonical printed codes | Artifact blocks, workbook cells and HTML are rewritten to the Atlas identity (BIO 141 → BIOL 141, QTM 150 → DATASCI 150). |
| EMO-086 hidden call-limit failure | Outline and section exhaustion now return `outline_exhausted` / `section_exhausted` with honest student messages. |
| EMO-087 deadline discards the answer | A research deadline now says the question took too long, not that it could not be answered. The last valid draft is still salvaged when one exists. |
| EMO-084 bare `ValueError` | Assembly/validation failures are caught, logged with a traceback, and stored as `invalid_output` with a safe message. |
| EMO-076 program grading rules | Program grading lines read by research are recorded with their evidence; an answer that discusses S/U, letter grades or grading must cite the program's own line. |
| EMO-079 answer guard | The echo check normalises NFKC/confusables/zero-width characters and uses 5-word shingles with an overlap ratio; AWS, JWT, GitHub, Slack and Google key shapes are refused; a block that starts with TODO/TBD/lorem is a stub; script/event markup is refused. |
| EMO-063 shouted prose | `WITH 88` and `ROOM 204` no longer mint fake subjects; the all-caps path still accepts a made-up subject such as `XYZ 101`. |
| EMO-078 stale integration tests | The reindex test asserts the safe stored message; the failed-draft test arms three provider failures, matching the new retry budget. |

## Verification

- Full backend suite: 1,737 passed, 2 skipped; `ruff check .` clean.
- Frontend `typecheck`, `format:check`, unit suite, and `test:integration` (5/5 with the Chrome path).
- Offline artifact benchmark 70/70.
- Live smoke on `deepseek-v4.1-flash`: A01 (Biology BS plan), A02 (plan with completed courses), A03 (Oxford plan), A04 (checklist), A09 (workbook tracker), A10 (comparison workbook), A13 (HTML page), A14 (HTML requirements), and A29 (fake program) all pass. A01 published with every content check green; its fastest smoke was turn 38.7 s / artifact run 18.2 s, while later provider-latency runs took 96-104 s and missed only K1's 90 s turn bar.
- A26 (fake course named in the request) still fails frozen K4 because the answer names `BIOL 999`; that is the known K4 wording conflict, unchanged. A27/A28 (non-advising page/spreadsheet with malicious content) remain declined by scope rather than producing a corpus-cited artifact.

## Remaining

- EMO-087 returns a clear timeout message, but a deadline before any validated
  draft has no partial answer to publish.
- EMO-089 still cannot turn words like "Intro Psych" into completed courses;
  only explicit course codes are treated as completed.
- EMO-080 depends on the outline model actually selecting the second program's
  and premed courses; the pipeline now accepts them.
- A26 needs the user's K4 wording decision: the frozen check rejects any reply
  that names a fake code, while the finish gate requires naming it. A27/A28
  need a scope decision about non-advising page/spreadsheet artifacts.
- The live seed-11 suite and the frozen benchmark have not been rerun on this
  code.
