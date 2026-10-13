-- Immutable, workspace-private summary checkpoints over saved conversation turns.
CREATE TABLE conversation_summaries (
    summary_id uuid PRIMARY KEY,
    conversation_id uuid NOT NULL REFERENCES conversations ON DELETE CASCADE,
    workspace_key text NOT NULL CHECK (length(workspace_key) BETWEEN 1 AND 200),
    expected_sequence bigint NOT NULL CHECK (expected_sequence >= 0),
    candidate_covered_through bigint NOT NULL CHECK (candidate_covered_through > 0),
    covered_through bigint CHECK (covered_through IS NULL OR covered_through > 0),
    request_fingerprint char(64) NOT NULL CHECK (request_fingerprint ~ '^[a-f0-9]{64}$'),
    input_fingerprint char(64) NOT NULL CHECK (input_fingerprint ~ '^[a-f0-9]{64}$'),
    input_snapshot jsonb NOT NULL,
    reference_metadata jsonb NOT NULL,
    state text NOT NULL CHECK (state IN ('pending', 'completed', 'refused', 'failed', 'stale')),
    summary_text text,
    failure_code text,
    profile_id uuid,
    profile_revision integer CHECK (profile_revision IS NULL OR profile_revision > 0),
    operation_id uuid NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((state = 'completed') = (summary_text IS NOT NULL)),
    CHECK ((state = 'completed') = (covered_through IS NOT NULL)),
    CHECK (covered_through IS NULL OR covered_through = candidate_covered_through),
    CHECK (octet_length(input_snapshot::text) <= 1000000),
    CHECK (octet_length(reference_metadata::text) <= 1000000)
);
CREATE INDEX conversation_summaries_latest_idx
    ON conversation_summaries (conversation_id, covered_through DESC)
    WHERE state = 'completed';
CREATE INDEX conversation_summaries_workspace_idx
    ON conversation_summaries (workspace_key, conversation_id, created_at DESC);
