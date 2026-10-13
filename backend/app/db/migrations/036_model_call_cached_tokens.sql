-- Provider-reported prompt tokens served from the provider's prompt cache.
--
-- NULL means the provider did not report it (unknown), never "no cache hit".
-- Accounting still uses prompt_tokens; this column only makes cache reuse of a
-- repeated prefix (such as the query-planning catalog directory) observable.

ALTER TABLE model_calls ADD COLUMN cached_prompt_tokens bigint
    CHECK (cached_prompt_tokens IS NULL OR cached_prompt_tokens >= 0);
