-- Exact retrieval excerpts and known invalid attempts for dependent artifact runs.
CREATE TABLE artifact_run_plan_excerpts (
    job_id uuid NOT NULL,
    excerpt_id integer NOT NULL CHECK (excerpt_id BETWEEN 1 AND 500),
    source_id uuid NOT NULL,
    chunk_id uuid NOT NULL REFERENCES chunks ON DELETE RESTRICT,
    source_name text NOT NULL CHECK (length(source_name) BETWEEN 1 AND 500),
    locator_label text NOT NULL CHECK (length(locator_label) BETWEEN 1 AND 500),
    text_snapshot text NOT NULL CHECK (length(text_snapshot) BETWEEN 1 AND 100000),
    excerpt_fingerprint char(64) NOT NULL CHECK (excerpt_fingerprint ~ '^[a-f0-9]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, excerpt_id),
    UNIQUE (job_id, chunk_id),
    FOREIGN KEY (job_id, source_id)
        REFERENCES artifact_run_plan_sources(job_id, source_id) ON DELETE CASCADE
);

ALTER TABLE artifact_run_units DROP CONSTRAINT artifact_run_units_status_check;
ALTER TABLE artifact_run_units ADD CONSTRAINT artifact_run_units_status_check
    CHECK (status IN ('pending','calling','needs_repair','validated','unknown','failed'));

CREATE TABLE artifact_run_unit_attempts (
    job_id uuid NOT NULL,
    unit_id uuid NOT NULL,
    attempt_no integer NOT NULL CHECK (attempt_no > 0),
    outcome text NOT NULL CHECK (outcome IN ('invalid','validated','failed','unknown')),
    candidate_text text,
    candidate_sha256 char(64) NOT NULL CHECK (candidate_sha256 ~ '^[a-f0-9]{64}$'),
    candidate_truncated boolean NOT NULL DEFAULT false,
    validation_issues jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, unit_id, attempt_no),
    FOREIGN KEY (job_id, unit_id, attempt_no)
        REFERENCES artifact_run_unit_calls(job_id, unit_id, attempt_no) ON DELETE CASCADE,
    CHECK (candidate_text IS NULL OR octet_length(candidate_text) <= 4000000),
    CHECK (jsonb_typeof(validation_issues) = 'array'),
    CHECK (octet_length(validation_issues::text) <= 16000),
    CHECK ((outcome = 'invalid') = (jsonb_array_length(validation_issues) > 0))
);
CREATE INDEX artifact_run_unit_attempts_state_idx
    ON artifact_run_unit_attempts(job_id, unit_id, outcome, attempt_no);

CREATE FUNCTION protect_artifact_run_excerpt() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.job_id, NEW.excerpt_id, NEW.source_id, NEW.chunk_id,
           NEW.source_name, NEW.locator_label, NEW.text_snapshot, NEW.excerpt_fingerprint)
       IS DISTINCT FROM
       ROW(OLD.job_id, OLD.excerpt_id, OLD.source_id, OLD.chunk_id,
           OLD.source_name, OLD.locator_label, OLD.text_snapshot, OLD.excerpt_fingerprint) THEN
        RAISE EXCEPTION 'artifact run excerpt is immutable';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER artifact_run_excerpt_immutable
    BEFORE UPDATE ON artifact_run_plan_excerpts
    FOR EACH ROW EXECUTE FUNCTION protect_artifact_run_excerpt();

CREATE FUNCTION protect_artifact_run_attempt() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.job_id, NEW.unit_id, NEW.attempt_no, NEW.outcome,
           NEW.candidate_text, NEW.candidate_sha256, NEW.candidate_truncated,
           NEW.validation_issues)
       IS DISTINCT FROM
       ROW(OLD.job_id, OLD.unit_id, OLD.attempt_no, OLD.outcome,
           OLD.candidate_text, OLD.candidate_sha256, OLD.candidate_truncated,
           OLD.validation_issues) THEN
        RAISE EXCEPTION 'artifact run attempts are immutable';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER artifact_run_attempt_immutable
    BEFORE UPDATE ON artifact_run_unit_attempts
    FOR EACH ROW EXECUTE FUNCTION protect_artifact_run_attempt();
