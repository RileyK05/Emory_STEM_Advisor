-- Immutable prompt versions and human promotion. A model does not approve them.

CREATE TABLE public.prompt_versions (
    version_id uuid PRIMARY KEY,
    name text NOT NULL,
    template_text text NOT NULL,
    content_digest character(64) NOT NULL,
    review_state text NOT NULL DEFAULT 'draft',
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT prompt_versions_name_check CHECK (char_length(name) BETWEEN 1 AND 120),
    CONSTRAINT prompt_versions_text_check CHECK (char_length(template_text) BETWEEN 1 AND 20000),
    CONSTRAINT prompt_versions_digest_check CHECK (content_digest ~ '^[a-f0-9]{64}$'),
    CONSTRAINT prompt_versions_review_check CHECK (review_state IN ('draft', 'approved')),
    CONSTRAINT prompt_versions_digest_unique UNIQUE (content_digest)
);

CREATE TABLE public.eval_suite_fixtures (
    fixture_id uuid PRIMARY KEY,
    suite_name text NOT NULL,
    fixture_digest character(64) NOT NULL,
    fixture_text text NOT NULL,
    present boolean NOT NULL DEFAULT true,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT eval_suite_fixtures_name_check CHECK (char_length(suite_name) BETWEEN 1 AND 120),
    CONSTRAINT eval_suite_fixtures_digest_check CHECK (fixture_digest ~ '^[a-f0-9]{64}$'),
    CONSTRAINT eval_suite_fixtures_text_check CHECK (char_length(fixture_text) BETWEEN 1 AND 20000)
);

CREATE UNIQUE INDEX eval_suite_fixtures_current
    ON public.eval_suite_fixtures (suite_name)
    WHERE present;

CREATE TABLE public.prompt_promotions (
    promotion_id uuid PRIMARY KEY,
    version_id uuid NOT NULL REFERENCES public.prompt_versions (version_id),
    suite_name text NOT NULL,
    fixture_digest character(64) NOT NULL,
    status text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT prompt_promotions_status_check CHECK (status = 'published'),
    CONSTRAINT prompt_promotions_suite_check CHECK (char_length(suite_name) BETWEEN 1 AND 120),
    CONSTRAINT prompt_promotions_digest_check CHECK (fixture_digest ~ '^[a-f0-9]{64}$')
);

CREATE UNIQUE INDEX prompt_promotions_version_suite
    ON public.prompt_promotions (version_id, suite_name, fixture_digest);
