# Draft answerability calibration set

These 30 manually written questions use only the dated excerpts in
`../retrieval_eval_2026_10_08/`. All labels are **pending human review**. They are
not institutional advice, approved answers, or a released evaluation benchmark.
This set does not change the existing 48-case baseline or retrieval configuration.

`support` identifies excerpt sections supporting the answerable portion of a
question. `related_sections` identifies tempting topical matches that do not
answer an unanswerable question; these must never be counted as supporting
evidence. Labels concern the supplied corpus, not whether the information might
exist elsewhere. No prerequisite nodes or edges are created by these records.

The fixed partition contains 20 calibration cases (8 answerable, 10 unanswerable,
2 partial) and 10 holdout cases (4 answerable, 4 unanswerable, 2 partial). Queries
are new paraphrases against the same corpus; the holdout is not independent of
its source families. Its labels are visible in this repository, so a human must
review the labels and freeze the partition before relying on it as a release
gate. Record any inspection or tuning against the holdout and obtain a new
untouched set before claiming independent validation.

All questions retrieve against the full nonempty corpus. An empty source
selection cannot manufacture a successful refusal. Report results separately
for answerable, partial, topical unanswerable, and unrelated questions. A raw
embedding floor can reject weak matches; it cannot establish answerability from
cosine similarity. Partial questions should retrieve supporting sections and
explicitly withhold the unsupported portion during generation, which a
retrieval-only run does not test.

The manual diagnostic runner must use cached Granite through the provider
factory, disable network downloads, preserve corpus/model/config hashes, and
keep threshold sweeps outside production configuration. Do not select a floor
from this draft or treat retrieval of a related passage as a correct answer.

Run from `backend/`:

```powershell
..\.venv\Scripts\python -m scripts.manual.retrieval_diagnostic --output ../docs/evidence/retrieval-answerability-diagnostic-2026-10-08.json
```

The recorded October 8 run retrieves supporting sections for all 16 answerable
or partial cases and returns passages for all 14 unanswerable cases. All ten
topical negatives retrieve their named distractors. Its simulated raw-score
floors illustrate lost positive coverage without resolving topical
answerability. The holdout has now been inspected; preserve that fact when
reporting these results. Generation correctness and human adjudication remain
unverified.
