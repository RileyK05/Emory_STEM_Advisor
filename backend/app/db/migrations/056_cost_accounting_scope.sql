-- Standard and native-batch tariffs are separate; never assume a batch discount.
ALTER TABLE model_price_schedules
    ADD COLUMN billing_mode text NOT NULL DEFAULT 'standard'
        CHECK (billing_mode IN ('standard', 'batch'));
CREATE INDEX model_price_schedules_mode_lookup_idx
    ON model_price_schedules (provider, model_id, billing_mode, effective_from DESC);

-- Pin batch pricing to admission to provider I/O, rather than a later poll date.
ALTER TABLE provider_batches ADD COLUMN pricing_at timestamptz;

-- Earlier native-batch costs used ordinary request tariffs and cannot be verified.
UPDATE model_calls m SET schedule_id = NULL, estimated_cost = NULL, cost_status = 'unknown'
WHERE EXISTS (SELECT 1 FROM provider_batch_items i WHERE i.operation_id = m.operation_id);
