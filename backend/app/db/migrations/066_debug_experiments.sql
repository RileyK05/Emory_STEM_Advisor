-- Authorized debug experiments. Replay of a saved conversation request does
-- not insert a row here, and the experiment id is not the conversation id.

CREATE TABLE public.debug_experiments (
    experiment_id uuid PRIMARY KEY,
    workspace_key text NOT NULL,
    source_conversation_id uuid NOT NULL,
    source_request_id uuid NOT NULL,
    snapshot jsonb NOT NULL,
    state text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT debug_experiments_state_check CHECK (state IN ('recorded', 'non_replayable')),
    CONSTRAINT debug_experiments_workspace_key_check CHECK (
        char_length(workspace_key) BETWEEN 1 AND 200
    ),
    CONSTRAINT debug_experiments_not_conversation CHECK (
        experiment_id <> source_conversation_id
    )
);
