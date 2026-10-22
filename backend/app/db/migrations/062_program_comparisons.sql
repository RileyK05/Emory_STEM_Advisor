-- Immutable program-comparison scenarios. Overlays are not academic-record attempts.

CREATE TABLE public.program_comparisons (
    comparison_id uuid PRIMARY KEY,
    workspace_key text NOT NULL,
    record_id uuid NOT NULL REFERENCES public.student_academic_records(record_id),
    record_revision integer NOT NULL,
    program_set_digest text NOT NULL,
    overlay jsonb NOT NULL,
    overlay_digest text NOT NULL,
    sharing_policy text NOT NULL,
    mention_kind text NOT NULL,
    shared_mentions jsonb NOT NULL,
    credit_savings numeric(6,2),
    coverage_comparable boolean NOT NULL,
    ranking_blocked_reason text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT program_comparisons_revision_check CHECK (record_revision >= 1),
    CONSTRAINT program_comparisons_digest_check CHECK (length(overlay_digest) = 64),
    CONSTRAINT program_comparisons_program_digest_check CHECK (length(program_set_digest) = 64),
    CONSTRAINT program_comparisons_sharing_check CHECK (sharing_policy IN ('unknown', 'evidenced')),
    CONSTRAINT program_comparisons_unknown_savings_check
        CHECK (sharing_policy <> 'unknown' OR credit_savings IS NULL)
);

CREATE UNIQUE INDEX program_comparisons_identity
    ON public.program_comparisons (
        workspace_key, record_id, record_revision, program_set_digest, overlay_digest
    );

CREATE TABLE public.program_comparison_members (
    comparison_id uuid NOT NULL REFERENCES public.program_comparisons(comparison_id),
    position integer NOT NULL,
    program_id uuid NOT NULL REFERENCES public.programs(program_id),
    rule_release_id uuid NOT NULL REFERENCES public.program_rule_releases(release_id),
    audit_id uuid NOT NULL REFERENCES public.degree_audits(audit_id),
    overlay_status text NOT NULL,
    unresolved_count integer NOT NULL,
    PRIMARY KEY (comparison_id, position),
    CONSTRAINT program_comparison_members_position_check CHECK (position BETWEEN 1 AND 3),
    CONSTRAINT program_comparison_members_unresolved_check CHECK (unresolved_count >= 0)
);
