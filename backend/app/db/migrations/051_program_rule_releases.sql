-- Program rule releases and review workflow (Feature 12).

CREATE TABLE public.program_rule_releases (
    release_id uuid PRIMARY KEY,
    program_id uuid NOT NULL REFERENCES public.programs(program_id) ON DELETE CASCADE,
    collection_id uuid NOT NULL REFERENCES public.collections(collection_id) ON DELETE CASCADE,
    source_id uuid NOT NULL REFERENCES public.sources(source_id) ON DELETE CASCADE,
    source_index_revision integer NOT NULL,
    source_sha256 text NOT NULL,
    release_version integer NOT NULL,
    status text DEFAULT 'draft' NOT NULL,
    rules_tree jsonb NOT NULL,
    rules_digest text NOT NULL,
    coverage_summary jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_by_user_id uuid REFERENCES public.app_users(user_id) ON DELETE SET NULL,
    approved_by_user_id uuid REFERENCES public.app_users(user_id) ON DELETE SET NULL,
    approved_at timestamp with time zone,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT program_rule_releases_version_unique UNIQUE (program_id, release_version),
    CONSTRAINT program_rule_releases_status_check
        CHECK (status IN ('draft', 'reviewing', 'approved', 'published', 'archived', 'rejected')),
    CONSTRAINT program_rule_releases_digest_check
        CHECK (length(rules_digest) = 64)
);

CREATE INDEX program_rule_releases_program_idx ON public.program_rule_releases (program_id, status);
CREATE INDEX program_rule_releases_source_idx ON public.program_rule_releases (source_id);

CREATE TABLE public.program_rule_reviews (
    review_id uuid PRIMARY KEY,
    release_id uuid NOT NULL REFERENCES public.program_rule_releases(release_id) ON DELETE CASCADE,
    reviewer_user_id uuid REFERENCES public.app_users(user_id) ON DELETE SET NULL,
    action text NOT NULL,
    review_notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT program_rule_reviews_action_check
        CHECK (action IN ('submitted', 'approved', 'rejected', 'published', 'archived'))
);

CREATE INDEX program_rule_reviews_release_idx ON public.program_rule_reviews (release_id, created_at DESC);

ALTER TABLE public.programs
    ADD COLUMN IF NOT EXISTS current_rule_release_id uuid REFERENCES public.program_rule_releases(release_id) ON DELETE SET NULL;

