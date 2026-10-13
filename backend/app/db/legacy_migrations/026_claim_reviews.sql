-- Model claim assessments are separate from human verification and from the
-- deterministic prerequisite/similarity graph.
CREATE TABLE claim_reviews (
    review_id uuid PRIMARY KEY,
    response_id uuid NOT NULL REFERENCES responses ON DELETE CASCADE,
    workspace_key text NOT NULL CHECK (length(workspace_key) BETWEEN 1 AND 200),
    operation_id uuid NOT NULL UNIQUE REFERENCES operations,
    profile_id uuid NOT NULL REFERENCES connection_profiles ON DELETE RESTRICT,
    profile_revision integer NOT NULL CHECK (profile_revision > 0),
    provider text NOT NULL,
    model_id text NOT NULL,
    review_version integer NOT NULL CHECK (review_version > 0),
    request_fingerprint char(64) NOT NULL CHECK (request_fingerprint ~ '^[a-f0-9]{64}$'),
    binding_fingerprint char(64) NOT NULL CHECK (binding_fingerprint ~ '^[a-f0-9]{64}$'),
    input_snapshot jsonb NOT NULL,
    state text NOT NULL CHECK (state IN ('pending','completed','refused','invalid','failed','unknown','abandoned')),
    usage_source text NOT NULL DEFAULT 'unknown' CHECK (usage_source IN ('provider','estimated','unknown')),
    prompt_tokens integer CHECK (prompt_tokens IS NULL OR prompt_tokens >= 0),
    completion_tokens integer CHECK (completion_tokens IS NULL OR completion_tokens >= 0),
    failure_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((prompt_tokens IS NULL) = (completion_tokens IS NULL)),
    CHECK (octet_length(input_snapshot::text) <= 1000000)
);
CREATE INDEX claim_reviews_workspace_created_idx
    ON claim_reviews (workspace_key, created_at DESC);

CREATE TABLE claim_review_units (
    review_id uuid NOT NULL REFERENCES claim_reviews ON DELETE CASCADE,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    answer_start integer NOT NULL CHECK (answer_start >= 0),
    answer_end integer NOT NULL CHECK (answer_end > answer_start),
    answer_text text NOT NULL CHECK (length(answer_text) > 0),
    coverage_kind text NOT NULL CHECK (coverage_kind IN ('claim','unreviewed_gap')),
    model_assessment text NOT NULL CHECK (model_assessment IN (
        'supported','contradicted','insufficient_evidence','conflicting_evidence',
        'uncertain','abstained','unreviewed','invalid'
    )),
    confidence double precision CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    evidence_spans jsonb NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (review_id, ordinal),
    CHECK ((coverage_kind = 'unreviewed_gap' AND model_assessment = 'unreviewed')
        OR coverage_kind = 'claim'),
    CHECK (octet_length(evidence_spans::text) <= 200000)
);

ALTER TABLE model_calls ADD COLUMN claim_review_id uuid REFERENCES claim_reviews ON DELETE CASCADE;
CREATE INDEX model_calls_claim_review_idx ON model_calls (claim_review_id)
    WHERE claim_review_id IS NOT NULL;
