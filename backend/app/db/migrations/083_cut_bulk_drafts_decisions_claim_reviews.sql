-- Bulk drafts, yes/no decisions and claim reviews had no caller in the product.
ALTER TABLE model_calls DROP COLUMN decision_id, DROP COLUMN claim_review_id;
DROP TABLE provider_batch_items;
DROP TABLE provider_batches;
DROP TABLE claim_review_units;
DROP TABLE claim_reviews;
DROP TABLE decisions;
