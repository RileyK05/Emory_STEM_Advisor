-- A cancelled refresh keeps its staged edition and that edition's revision, so
-- the next refresh stages a later revision. Edition revisions only increase;
-- the published editions are a subsequence of them.

ALTER TABLE public.catalog_refresh_batches
    DROP CONSTRAINT catalog_refresh_batches_revision_check,
    ADD CONSTRAINT catalog_refresh_batches_revision_check
        CHECK (staged_revision > base_revision);

-- At most one refresh stages per collection.
CREATE UNIQUE INDEX catalog_refresh_batches_one_staging
    ON public.catalog_refresh_batches (collection_id) WHERE status = 'staging';
