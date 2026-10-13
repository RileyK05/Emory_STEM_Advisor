CREATE TABLE auth_login_transactions (
    state_hash bytea PRIMARY KEY,
    nonce text NOT NULL,
    code_verifier text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
);
CREATE INDEX auth_login_transactions_expiry_idx ON auth_login_transactions (expires_at);

CREATE TABLE auth_sessions (
    session_hash bytea PRIMARY KEY,
    issuer text NOT NULL,
    subject text NOT NULL,
    workspace_key text NOT NULL,
    display_name text,
    email text,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz
);
CREATE INDEX auth_sessions_workspace_idx ON auth_sessions (workspace_key);
CREATE INDEX auth_sessions_expiry_idx ON auth_sessions (expires_at) WHERE revoked_at IS NULL;
