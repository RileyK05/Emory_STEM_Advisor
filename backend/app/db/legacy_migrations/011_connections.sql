CREATE TABLE connection_profiles (
    profile_id uuid PRIMARY KEY,
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
    provider text NOT NULL CHECK (provider IN (
        'openai', 'anthropic', 'google', 'openai-compatible', 'hf-local',
        'openai-decisions', 'cloudflare-clef'
    )),
    model_id text NOT NULL CHECK (length(model_id) BETWEEN 1 AND 200),
    base_url text,
    account_id text,
    secret_ciphertext bytea,
    provider_options jsonb NOT NULL DEFAULT '{}'::jsonb,
    roles text[] NOT NULL DEFAULT ARRAY[]::text[],
    capabilities text[] NOT NULL DEFAULT ARRAY[]::text[],
    data_scopes text[] NOT NULL DEFAULT ARRAY[]::text[],
    enabled boolean NOT NULL DEFAULT true,
    priority integer NOT NULL DEFAULT 100,
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (roles <@ ARRAY['standard', 'complex', 'fast_generation', 'decision']::text[]),
    CHECK (capabilities <@ ARRAY[
        'structured_output', 'tools', 'streaming', 'vision', 'batch', 'decision'
    ]::text[]),
    CHECK (data_scopes <@ ARRAY[
        'institutional_corpus', 'user_supplied_data', 'private_workspace'
    ]::text[]),
    CHECK ((provider = 'cloudflare-clef') = (account_id IS NOT NULL)),
    CHECK (provider <> 'hf-local' OR secret_ciphertext IS NULL)
);
CREATE UNIQUE INDEX connection_profiles_name_lower_idx ON connection_profiles (lower(name));
CREATE INDEX connection_profiles_routes_idx ON connection_profiles (enabled, priority, profile_id);

CREATE TABLE connection_profile_evaluations (
    evaluation_id uuid PRIMARY KEY,
    profile_id uuid NOT NULL REFERENCES connection_profiles(profile_id) ON DELETE CASCADE,
    profile_revision integer NOT NULL CHECK (profile_revision > 0),
    provider text NOT NULL,
    model_id text NOT NULL,
    suite_hash text NOT NULL CHECK (length(suite_hash) = 64),
    status text NOT NULL CHECK (status IN ('passed', 'failed')),
    metrics jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX connection_profile_eval_current_idx
    ON connection_profile_evaluations (profile_id, profile_revision, suite_hash, recorded_at DESC);
