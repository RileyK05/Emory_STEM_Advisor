CREATE TABLE decisions (
    decision_id uuid PRIMARY KEY,
    workspace_key text NOT NULL CHECK (length(workspace_key) BETWEEN 1 AND 200),
    operation_id uuid NOT NULL UNIQUE REFERENCES operations,
    profile_id uuid NOT NULL REFERENCES connection_profiles ON DELETE RESTRICT,
    profile_revision integer NOT NULL CHECK (profile_revision > 0),
    provider text NOT NULL,
    model_id text NOT NULL,
    request_fingerprint char(64) NOT NULL CHECK (request_fingerprint ~ '^[a-f0-9]{64}$'),
    request_json jsonb NOT NULL,
    state text NOT NULL CHECK (state IN ('pending','completed','refused','invalid','failed','unknown','abandoned')),
    prediction boolean,
    probability double precision CHECK (probability IS NULL OR (probability >= 0 AND probability <= 1)),
    confidence double precision CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    refused boolean NOT NULL DEFAULT false,
    usage_source text NOT NULL DEFAULT 'unknown' CHECK (usage_source IN ('provider','estimated','unknown')),
    prompt_tokens integer CHECK (prompt_tokens IS NULL OR prompt_tokens >= 0),
    completion_tokens integer CHECK (completion_tokens IS NULL OR completion_tokens >= 0),
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((prompt_tokens IS NULL) = (completion_tokens IS NULL))
);
CREATE INDEX decisions_workspace_created_idx ON decisions (workspace_key, created_at DESC);

-- A standalone decision has no retrieval trace, but its provider attempt is
-- still auditable by its decision and shared operation IDs.
ALTER TABLE model_calls ALTER COLUMN trace_id DROP NOT NULL;
ALTER TABLE model_calls ADD COLUMN decision_id uuid REFERENCES decisions ON DELETE CASCADE;
CREATE INDEX model_calls_decision_idx ON model_calls (decision_id) WHERE decision_id IS NOT NULL;
