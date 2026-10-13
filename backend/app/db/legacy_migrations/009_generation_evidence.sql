-- Preserve generated evidence independently of mutable indexed chunks.
CREATE TABLE model_calls (
    model_call_id uuid PRIMARY KEY,
    trace_id uuid NOT NULL REFERENCES retrieval_traces ON DELETE CASCADE,
    provider text NOT NULL,
    model_id text,
    status text NOT NULL CHECK (status IN ('completed', 'failed')),
    finish_reason text,
    request_id text,
    prompt_tokens bigint,
    completion_tokens bigint,
    usage_source text NOT NULL CHECK (usage_source IN ('provider', 'estimated', 'unknown')),
    latency_ms integer NOT NULL,
    error_type text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX model_calls_trace_idx ON model_calls (trace_id);

CREATE TABLE responses (
    response_id uuid PRIMARY KEY,
    trace_id uuid NOT NULL REFERENCES retrieval_traces ON DELETE CASCADE,
    status text NOT NULL CHECK (status IN ('answer', 'refusal', 'failed')),
    text text NOT NULL,
    reason text,
    prompt_version text NOT NULL,
    context_snapshot jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX responses_trace_idx ON responses (trace_id);

CREATE TABLE citations (
    response_id uuid NOT NULL REFERENCES responses ON DELETE CASCADE,
    ordinal integer NOT NULL,
    chunk_id uuid NOT NULL,
    source_id uuid NOT NULL,
    source_name text NOT NULL,
    locator_label text NOT NULL,
    text_snapshot text NOT NULL,
    PRIMARY KEY (response_id, ordinal)
);
