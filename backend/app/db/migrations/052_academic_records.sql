-- Academic records, course attempts, import batches, and verification events (Feature 1).

CREATE TABLE public.student_academic_records (
    record_id uuid PRIMARY KEY,
    workspace_key text NOT NULL UNIQUE,
    record_revision integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT student_academic_records_workspace_key_check
        CHECK (length(workspace_key) >= 1 AND length(workspace_key) <= 200),
    CONSTRAINT student_academic_records_revision_check
        CHECK (record_revision >= 1)
);

CREATE TABLE public.academic_record_attempts (
    attempt_id uuid PRIMARY KEY,
    record_id uuid NOT NULL REFERENCES public.student_academic_records(record_id) ON DELETE CASCADE,
    workspace_key text NOT NULL,
    institution text DEFAULT 'Emory University' NOT NULL,
    submitted_course_code text NOT NULL,
    resolved_course_code text,
    course_title text,
    term text NOT NULL,
    attempted_credits numeric(4,2) DEFAULT 0.0 NOT NULL,
    earned_credits numeric(4,2) DEFAULT 0.0 NOT NULL,
    grade text,
    status text NOT NULL,
    provenance text DEFAULT 'student_manual' NOT NULL,
    is_verified boolean DEFAULT false NOT NULL,
    verification_method text,
    verified_by_user_id uuid REFERENCES public.app_users(user_id) ON DELETE SET NULL,
    verified_at timestamp with time zone,
    verification_notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT academic_record_attempts_status_check
        CHECK (status IN ('completed', 'in_progress', 'planned', 'withdrawn', 'failed', 'repeated', 'transfer', 'test_credit')),
    CONSTRAINT academic_record_attempts_provenance_check
        CHECK (provenance IN ('student_manual', 'csv_import', 'transcript_upload', 'institutional_sync')),
    CONSTRAINT academic_record_attempts_verification_method_check
        CHECK (verification_method IS NULL OR verification_method IN ('manual_staff', 'official_transcript', 'sis_feed')),
    CONSTRAINT academic_record_attempts_credits_check
        CHECK (attempted_credits >= 0.0 AND earned_credits >= 0.0)
);

CREATE INDEX academic_record_attempts_record_idx ON public.academic_record_attempts (record_id, term);
CREATE INDEX academic_record_attempts_workspace_idx ON public.academic_record_attempts (workspace_key);

CREATE TABLE public.academic_record_import_batches (
    batch_id uuid PRIMARY KEY,
    record_id uuid NOT NULL REFERENCES public.student_academic_records(record_id) ON DELETE CASCADE,
    workspace_key text NOT NULL,
    expected_record_revision integer NOT NULL,
    status text DEFAULT 'preview' NOT NULL,
    filename text NOT NULL,
    raw_content text NOT NULL,
    parsed_rows jsonb DEFAULT '[]'::jsonb NOT NULL,
    row_errors jsonb DEFAULT '[]'::jsonb NOT NULL,
    duplicate_candidates jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    committed_at timestamp with time zone,
    CONSTRAINT academic_record_import_batches_status_check
        CHECK (status IN ('preview', 'committed', 'rejected', 'failed'))
);

CREATE INDEX academic_record_import_batches_record_idx ON public.academic_record_import_batches (record_id, created_at DESC);

CREATE TABLE public.academic_record_verification_events (
    event_id uuid PRIMARY KEY,
    record_id uuid NOT NULL REFERENCES public.student_academic_records(record_id) ON DELETE CASCADE,
    attempt_id uuid REFERENCES public.academic_record_attempts(attempt_id) ON DELETE SET NULL,
    event_type text NOT NULL,
    actor_user_id uuid REFERENCES public.app_users(user_id) ON DELETE SET NULL,
    actor_role text NOT NULL,
    details jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT academic_record_verification_events_type_check
        CHECK (event_type IN ('attempt_created', 'attempt_updated', 'attempt_verified', 'verification_invalidated', 'attempt_deleted', 'batch_committed'))
);

CREATE INDEX academic_record_verification_events_record_idx ON public.academic_record_verification_events (record_id, created_at DESC);
