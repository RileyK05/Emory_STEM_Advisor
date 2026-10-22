-- Deterministic degree and program audits, rule evaluations, and allocations (Feature 2).

CREATE TABLE public.degree_audits (
    audit_id uuid PRIMARY KEY,
    workspace_key text NOT NULL,
    program_id uuid NOT NULL REFERENCES public.programs(program_id) ON DELETE CASCADE,
    rule_release_id uuid NOT NULL REFERENCES public.program_rule_releases(release_id) ON DELETE CASCADE,
    record_id uuid NOT NULL REFERENCES public.student_academic_records(record_id) ON DELETE CASCADE,
    record_revision integer NOT NULL,
    rules_digest text NOT NULL,
    overall_status text NOT NULL,
    is_stale boolean DEFAULT false NOT NULL,
    completed_credits numeric(5,2) DEFAULT 0.0 NOT NULL,
    in_progress_credits numeric(5,2) DEFAULT 0.0 NOT NULL,
    unverified_credits numeric(5,2) DEFAULT 0.0 NOT NULL,
    results_summary jsonb DEFAULT '{}'::jsonb NOT NULL,
    blockers jsonb DEFAULT '[]'::jsonb NOT NULL,
    assumptions jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT degree_audits_overall_status_check
        CHECK (overall_status IN ('satisfied', 'in_progress_satisfied', 'outstanding', 'unresolved'))
);

CREATE INDEX degree_audits_workspace_idx ON public.degree_audits (workspace_key, created_at DESC);
CREATE INDEX degree_audits_program_idx ON public.degree_audits (program_id);
CREATE INDEX degree_audits_record_idx ON public.degree_audits (record_id);

CREATE TABLE public.audit_rule_evaluations (
    evaluation_id uuid PRIMARY KEY,
    audit_id uuid NOT NULL REFERENCES public.degree_audits(audit_id) ON DELETE CASCADE,
    node_id text NOT NULL,
    parent_node_id text,
    node_type text NOT NULL,
    title text NOT NULL,
    status text NOT NULL,
    allocated_attempt_ids jsonb DEFAULT '[]'::jsonb NOT NULL,
    allocated_credits numeric(5,2) DEFAULT 0.0 NOT NULL,
    required_count integer,
    satisfied_count integer,
    unresolved_reason text,
    raw_position integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT audit_rule_evaluations_status_check
        CHECK (status IN ('satisfied', 'in_progress_satisfied', 'outstanding', 'unresolved'))
);

CREATE INDEX audit_rule_evaluations_audit_idx ON public.audit_rule_evaluations (audit_id);

