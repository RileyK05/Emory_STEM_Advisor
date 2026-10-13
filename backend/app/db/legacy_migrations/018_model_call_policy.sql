-- Requested generation policy is distinct from observed SDK usage.
ALTER TABLE model_calls
    ADD COLUMN sampling_policy text CHECK (sampling_policy IN ('greedy', 'model_default')),
    ADD COLUMN reasoning_effort text,
    ADD COLUMN requested_output_tokens integer CHECK (requested_output_tokens > 0),
    ADD COLUMN requested_timeout_seconds double precision CHECK (requested_timeout_seconds > 0),
    ADD COLUMN requested_temperature double precision,
    ADD COLUMN profile_id uuid,
    ADD COLUMN profile_revision integer CHECK (profile_revision > 0);
