-- Feedback triage transitions and redacted candidate promotion.
-- A candidate still has no student workspace, comment, or history columns.

ALTER TABLE public.response_feedback
    ADD COLUMN issue_type text,
    ADD COLUMN triage_status text NOT NULL DEFAULT 'open',
    ADD COLUMN triage_revision integer NOT NULL DEFAULT 0,
    ADD CONSTRAINT response_feedback_issue_type_check CHECK (
        issue_type IS NULL OR issue_type IN (
            'missing_evidence', 'provider', 'explanation', 'privacy', 'ui'
        )
    ),
    ADD CONSTRAINT response_feedback_triage_status_check CHECK (
        triage_status IN ('open', 'reviewed', 'withdrawn')
    ),
    ADD CONSTRAINT response_feedback_triage_revision_check CHECK (triage_revision >= 0);

CREATE TABLE public.feedback_triage_events (
    event_id uuid PRIMARY KEY,
    feedback_id uuid NOT NULL REFERENCES public.response_feedback (feedback_id),
    issue_type text NOT NULL,
    from_status text NOT NULL,
    to_status text NOT NULL,
    actor_user_id uuid,
    note text,
    expected_revision integer NOT NULL,
    gap_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT feedback_triage_events_issue_check CHECK (issue_type IN (
        'missing_evidence', 'provider', 'explanation', 'privacy', 'ui'
    )),
    CONSTRAINT feedback_triage_events_from_check CHECK (
        from_status IN ('open', 'reviewed', 'withdrawn')
    ),
    CONSTRAINT feedback_triage_events_to_check CHECK (
        to_status IN ('open', 'reviewed', 'withdrawn')
    ),
    CONSTRAINT feedback_triage_events_note_check CHECK (
        note IS NULL OR char_length(note) <= 1000
    )
);

CREATE INDEX feedback_triage_events_feedback_idx
    ON public.feedback_triage_events (feedback_id, created_at);

ALTER TABLE public.eval_candidates
    ADD COLUMN candidate_digest character(64),
    ADD COLUMN source_id uuid,
    ADD COLUMN source_index_revision bigint,
    ADD COLUMN promotion_state text NOT NULL DEFAULT 'current',
    ADD COLUMN review_state text NOT NULL DEFAULT 'reviewed',
    ADD COLUMN gap_id uuid,
    ADD CONSTRAINT eval_candidates_digest_check CHECK (
        candidate_digest IS NULL OR candidate_digest ~ '^[a-f0-9]{64}$'
    ),
    ADD CONSTRAINT eval_candidates_promotion_state_check CHECK (
        promotion_state IN ('current', 'stale')
    ),
    ADD CONSTRAINT eval_candidates_review_state_check CHECK (
        review_state IN ('reviewed', 're_reviewed')
    ),
    ADD CONSTRAINT eval_candidates_index_check CHECK (
        source_index_revision IS NULL OR source_index_revision >= 0
    );

CREATE UNIQUE INDEX eval_candidates_current_digest
    ON public.eval_candidates (candidate_digest)
    WHERE promotion_state = 'current' AND candidate_digest IS NOT NULL;
