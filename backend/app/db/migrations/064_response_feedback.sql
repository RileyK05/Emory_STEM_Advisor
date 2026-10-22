-- Owner-scoped feedback revisions and redacted eval candidates.
-- A candidate stores only the redacted prompt and expected answer.

CREATE TABLE public.response_feedback (
    feedback_id uuid PRIMARY KEY,
    workspace_key text NOT NULL,
    response_id uuid NOT NULL,
    revision integer NOT NULL,
    vote text NOT NULL,
    reason_code text NOT NULL,
    comment text,
    is_current boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT response_feedback_revision_check CHECK (revision >= 1),
    CONSTRAINT response_feedback_vote_check CHECK (vote IN ('up', 'down')),
    CONSTRAINT response_feedback_reason_check CHECK (char_length(reason_code) BETWEEN 1 AND 80),
    CONSTRAINT response_feedback_identity UNIQUE (workspace_key, response_id, revision)
);

CREATE UNIQUE INDEX response_feedback_current
    ON public.response_feedback (workspace_key, response_id) WHERE is_current;

CREATE TABLE public.eval_candidates (
    candidate_id uuid PRIMARY KEY,
    redacted_prompt text NOT NULL,
    redacted_expected text NOT NULL,
    source_feedback_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT eval_candidates_prompt_check CHECK (char_length(redacted_prompt) BETWEEN 1 AND 4000),
    CONSTRAINT eval_candidates_expected_check CHECK (char_length(redacted_expected) BETWEEN 1 AND 4000)
);
