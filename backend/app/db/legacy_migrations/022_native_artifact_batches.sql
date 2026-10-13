ALTER TABLE provider_batch_items
    ADD COLUMN consumer_data jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN publication_state text NOT NULL DEFAULT 'pending'
        CHECK (publication_state IN ('pending', 'published', 'withheld')),
    ADD COLUMN published_artifact_id uuid,
    ADD COLUMN published_revision_id uuid,
    ADD CONSTRAINT provider_batch_item_publication_ids_check
        CHECK ((published_artifact_id IS NULL) = (published_revision_id IS NULL)),
    ADD CONSTRAINT provider_batch_item_publication_state_check
        CHECK ((publication_state = 'published') = (published_artifact_id IS NOT NULL));

ALTER TABLE provider_batch_items
    ADD CONSTRAINT provider_batch_item_consumer_data_size_check
        CHECK (octet_length(consumer_data::text) <= 2_000_000);
