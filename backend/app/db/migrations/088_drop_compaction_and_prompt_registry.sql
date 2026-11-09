-- Conversation compaction and the answer-prompt registry belonged to the
-- one-shot answer path that research replaced. Nothing writes these tables.
DROP TABLE conversation_summaries;
DROP TABLE prompt_promotions;
DROP TABLE eval_suite_fixtures;
DROP TABLE prompt_versions;
