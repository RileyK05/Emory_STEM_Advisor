-- Frozen excerpts survive source reindexing; the run fails closed if its current
-- indexed source binding disappears, while new indexes remain free to replace chunks.
ALTER TABLE artifact_run_plan_excerpts
    DROP CONSTRAINT artifact_run_plan_excerpts_chunk_id_fkey;
ALTER TABLE artifact_run_plan_excerpts
    ADD COLUMN locator_id uuid;
ALTER TABLE artifact_run_plan_excerpts
    ADD CONSTRAINT artifact_run_plan_excerpts_locator_id_present
    CHECK (locator_id IS NOT NULL) NOT VALID;

CREATE OR REPLACE FUNCTION protect_artifact_run_excerpt() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.job_id, NEW.excerpt_id, NEW.source_id, NEW.chunk_id, NEW.locator_id,
           NEW.source_name, NEW.locator_label, NEW.text_snapshot, NEW.excerpt_fingerprint)
       IS DISTINCT FROM
       ROW(OLD.job_id, OLD.excerpt_id, OLD.source_id, OLD.chunk_id, OLD.locator_id,
           OLD.source_name, OLD.locator_label, OLD.text_snapshot, OLD.excerpt_fingerprint) THEN
        RAISE EXCEPTION 'artifact run excerpt is immutable';
    END IF;
    RETURN NEW;
END $$;
