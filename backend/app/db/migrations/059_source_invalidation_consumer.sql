-- Lease and completion columns for the source invalidation outbox.
-- Consumption is idempotent: a completed row is not claimed again.

ALTER TABLE public.source_invalidation_outbox
    ADD COLUMN lease_token uuid,
    ADD COLUMN leased_until timestamp with time zone,
    ADD COLUMN attempts integer DEFAULT 0 NOT NULL,
    ADD COLUMN consumed_at timestamp with time zone,
    ADD COLUMN last_error text,
    ADD CONSTRAINT source_invalidation_outbox_attempts_check CHECK (attempts >= 0);

CREATE INDEX source_invalidation_outbox_lease_idx
    ON public.source_invalidation_outbox (leased_until)
    WHERE NOT published;
