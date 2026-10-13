-- Private immutable turns and citation-bearing units for human review.
CREATE TABLE conversations (
    conversation_id uuid PRIMARY KEY,
    workspace_key text NOT NULL,
    title text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
    last_sequence bigint NOT NULL DEFAULT 0,
    pending_request_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX conversations_workspace_idx ON conversations (workspace_key, updated_at DESC);

CREATE TABLE conversation_requests (
    conversation_id uuid NOT NULL REFERENCES conversations ON DELETE CASCADE,
    request_id uuid NOT NULL,
    fingerprint text NOT NULL,
    state text NOT NULL CHECK (state IN ('pending', 'completed', 'failed', 'abandoned')),
    input_snapshot jsonb NOT NULL,
    result jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    PRIMARY KEY (conversation_id, request_id)
);

CREATE TABLE conversation_turns (
    conversation_id uuid NOT NULL REFERENCES conversations ON DELETE CASCADE,
    sequence bigint NOT NULL CHECK (sequence > 0),
    request_id uuid NOT NULL,
    role text NOT NULL CHECK (role IN ('user', 'assistant')),
    text text NOT NULL,
    status text NOT NULL CHECK (status IN ('input', 'answer', 'refusal', 'history', 'failed', 'abandoned')),
    evidence jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (conversation_id, sequence),
    FOREIGN KEY (conversation_id, request_id) REFERENCES conversation_requests,
    UNIQUE (conversation_id, request_id, role)
);

CREATE TABLE claims (
    claim_id uuid PRIMARY KEY,
    response_id uuid NOT NULL REFERENCES responses ON DELETE CASCADE,
    ordinal integer NOT NULL,
    text text NOT NULL,
    citation_numbers jsonb NOT NULL,
    review_status text NOT NULL DEFAULT 'unreviewed' CHECK (review_status IN ('unreviewed', 'supported', 'unsupported', 'uncertain')),
    UNIQUE (response_id, ordinal)
);
