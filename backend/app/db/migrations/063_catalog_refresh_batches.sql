-- Staged catalog editions. Cancelling a refresh does not retire the published edition
-- or its topic build.

CREATE TABLE public.catalog_editions (
    edition_id uuid PRIMARY KEY,
    collection_id uuid NOT NULL REFERENCES public.collections(collection_id),
    revision integer NOT NULL,
    edition_key text NOT NULL,
    source_ids uuid[] NOT NULL,
    topic_build_id uuid,
    is_current boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT catalog_editions_revision_check CHECK (revision >= 1),
    CONSTRAINT catalog_editions_revision_unique UNIQUE (collection_id, revision)
);

CREATE UNIQUE INDEX catalog_editions_current
    ON public.catalog_editions (collection_id) WHERE is_current;

CREATE TABLE public.catalog_refresh_batches (
    batch_id uuid PRIMARY KEY,
    collection_id uuid NOT NULL REFERENCES public.collections(collection_id),
    edition_key text NOT NULL,
    status text NOT NULL,
    base_revision integer NOT NULL,
    staged_revision integer NOT NULL,
    staged_manifest jsonb NOT NULL,
    compatible boolean NOT NULL,
    staged_edition_id uuid NOT NULL REFERENCES public.catalog_editions(edition_id),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    cancelled_at timestamp with time zone,
    published_at timestamp with time zone,
    CONSTRAINT catalog_refresh_batches_status_check
        CHECK (status IN ('staging', 'cancelled', 'published')),
    CONSTRAINT catalog_refresh_batches_revision_check
        CHECK (staged_revision = base_revision + 1)
);
