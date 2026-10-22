-- Source review proposals, lifecycle events, and invalidation outbox (Feature 11).

ALTER TABLE public.source_provenance
    ADD COLUMN IF NOT EXISTS lifecycle_status text DEFAULT 'active' NOT NULL,
    ADD CONSTRAINT source_provenance_lifecycle_status_check
        CHECK (lifecycle_status IN ('staged', 'active', 'deprecated', 'archived'));

CREATE TABLE public.source_review_proposals (
    proposal_id uuid PRIMARY KEY,
    source_id uuid NOT NULL REFERENCES public.sources(source_id) ON DELETE CASCADE,
    expected_metadata_revision integer NOT NULL,
    proposed_by_user_id uuid REFERENCES public.app_users(user_id) ON DELETE SET NULL,
    status text DEFAULT 'pending' NOT NULL,
    source_category text NOT NULL,
    classification_origin text DEFAULT 'verified_curator' NOT NULL,
    publisher text,
    source_url text,
    edition text,
    geography text,
    occupation_id text,
    published_at date,
    acquired_at date,
    attribution text,
    title text,
    institution text,
    issuing_unit text,
    document_roles jsonb DEFAULT '[]'::jsonb NOT NULL,
    applicability jsonb DEFAULT '{}'::jsonb NOT NULL,
    applicability_status text DEFAULT 'unknown' NOT NULL,
    lifecycle_status text DEFAULT 'active' NOT NULL,
    replacement_intent text,
    review_notes text,
    rejection_reason text,
    reviewed_by_user_id uuid REFERENCES public.app_users(user_id) ON DELETE SET NULL,
    reviewed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT source_review_proposals_status_check
        CHECK (status IN ('pending', 'approved', 'rejected', 'superseded')),
    CONSTRAINT source_review_proposals_category_check
        CHECK (source_category IN ('institutional_catalog', 'institutional_department', 'institutional_policy', 'institutional_publication', 'career_reference', 'user_supplied', 'unclassified')),
    CONSTRAINT source_review_proposals_applicability_status_check
        CHECK (applicability_status IN ('reviewed_default', 'historical', 'unknown')),
    CONSTRAINT source_review_proposals_lifecycle_status_check
        CHECK (lifecycle_status IN ('staged', 'active', 'deprecated', 'archived'))
);

CREATE INDEX source_review_proposals_source_idx ON public.source_review_proposals (source_id);
CREATE INDEX source_review_proposals_status_idx ON public.source_review_proposals (status);

CREATE TABLE public.source_lifecycle_events (
    event_id uuid PRIMARY KEY,
    source_id uuid NOT NULL REFERENCES public.sources(source_id) ON DELETE CASCADE,
    event_type text NOT NULL,
    proposal_id uuid REFERENCES public.source_review_proposals(proposal_id) ON DELETE SET NULL,
    metadata_revision integer NOT NULL,
    actor_user_id uuid REFERENCES public.app_users(user_id) ON DELETE SET NULL,
    details jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT source_lifecycle_events_type_check
        CHECK (event_type IN ('proposal_created', 'proposal_approved', 'proposal_rejected', 'status_changed', 'deprecated', 'archived', 'activated', 'invalidated'))
);

CREATE INDEX source_lifecycle_events_source_idx ON public.source_lifecycle_events (source_id, created_at DESC);

CREATE TABLE public.source_invalidation_outbox (
    outbox_id uuid PRIMARY KEY,
    source_id uuid NOT NULL REFERENCES public.sources(source_id) ON DELETE CASCADE,
    event_type text NOT NULL,
    payload jsonb NOT NULL,
    published boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE INDEX source_invalidation_outbox_pending_idx ON public.source_invalidation_outbox (created_at) WHERE NOT published;
