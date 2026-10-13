# Retrieval baseline fixture

All cases are manually written and **pending human review**. The markdown files are short paraphrases of linked Emory sources, with exact source URLs, retrieval date, catalog term where applicable, and support line ranges in `sources.json`. They are not full page captures or approved answers. The official 2026–2027 course prerequisite fixture is linked and hashed for raw-field provenance.

Run the manual audit from `backend/` with:

`..\.venv\Scripts\python -m scripts.manual.retrieval_baseline`

The runner turns on Hugging Face offline mode, uses the configured embedder factory, builds fresh temporary Postgres/data directories, and deletes the temporary directory when it exits. Granite weights must already be cached. The evaluation set is intentionally kept out of pytest.

The JSONL follows the current named-source recall format for positive, comparison, multi-source, and selected-scope cases. Selected-scope cases use the same multi-source collection and pass source IDs for allowed files directly to retrieve; this is a manual-run extension because standard EvalCase has no source-scope field. Expected-empty and unrelated cases have an empty expected list; the manual runner excludes those from recall, calls retrieve with an explicitly empty source-ID list for empty-scope cases, and records refusal and returned-chunk counts. These observations are diagnostic, not a calibrated abstention metric. Do not pass all records to `run_eval` as if empty expectations were correct-answer labels.

This is a retrieval-quality baseline only. It does not test generated-answer correctness, citation precision, live schedules or OPUS enrollment, individual student plans, or institutional readiness. Recheck changing policies and program details with Emory before use. No prerequisite nodes or edges are created or implied by this fixture.
