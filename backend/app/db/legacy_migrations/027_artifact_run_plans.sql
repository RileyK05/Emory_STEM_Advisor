CREATE TABLE artifact_run_plans (
    job_id uuid PRIMARY KEY REFERENCES jobs ON DELETE CASCADE,
    workspace_key text NOT NULL CHECK (length(workspace_key) BETWEEN 1 AND 200),
    collection_id uuid NOT NULL REFERENCES collections ON DELETE RESTRICT,
    artifact_kind text NOT NULL CHECK (artifact_kind IN ('document','workbook','html')),
    status text NOT NULL CHECK (status IN ('ready','running','validated','failed','unknown')),
    input_fingerprint char(64) NOT NULL CHECK (input_fingerprint ~ '^[a-f0-9]{64}$'),
    frozen_inputs jsonb NOT NULL,
    quotas jsonb NOT NULL,
    target_artifact_id uuid,
    target_revision_id uuid,
    profile_id uuid REFERENCES connection_profiles ON DELETE RESTRICT,
    profile_revision integer,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((profile_id IS NULL) = (profile_revision IS NULL)),
    CHECK (profile_revision IS NULL OR profile_revision > 0),
    CHECK ((target_artifact_id IS NULL) = (target_revision_id IS NULL)),
    CHECK (octet_length(frozen_inputs::text) <= 2000000),
    CHECK (octet_length(quotas::text) <= 4000)
);
CREATE INDEX artifact_run_plans_workspace_created_idx
    ON artifact_run_plans (workspace_key, created_at DESC);

CREATE TABLE artifact_run_plan_sources (
    job_id uuid NOT NULL REFERENCES artifact_run_plans(job_id) ON DELETE CASCADE,
    source_id uuid NOT NULL REFERENCES sources ON DELETE RESTRICT,
    collection_id uuid NOT NULL REFERENCES collections ON DELETE RESTRICT,
    file_hash text NOT NULL CHECK (length(file_hash) BETWEEN 1 AND 256),
    index_revision bigint NOT NULL CHECK (index_revision >= 0),
    PRIMARY KEY (job_id, source_id)
);

CREATE TABLE artifact_run_units (
    unit_id uuid NOT NULL,
    job_id uuid NOT NULL REFERENCES artifact_run_plans(job_id) ON DELETE CASCADE,
    ordinal integer NOT NULL CHECK (ordinal BETWEEN 0 AND 99),
    unit_key text NOT NULL CHECK (length(unit_key) BETWEEN 1 AND 120),
    unit_kind text NOT NULL CHECK (unit_kind IN ('section','worksheet','repair','assembly')),
    status text NOT NULL CHECK (status IN ('pending','calling','validated','unknown','failed')),
    input_fingerprint char(64) NOT NULL CHECK (input_fingerprint ~ '^[a-f0-9]{64}$'),
    input_snapshot jsonb NOT NULL,
    profile_id uuid REFERENCES connection_profiles ON DELETE RESTRICT,
    profile_revision integer,
    checkpoint jsonb,
    checkpoint_fingerprint char(64),
    failure_code text CHECK (failure_code IS NULL OR length(failure_code) BETWEEN 1 AND 80),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, unit_id),
    UNIQUE (job_id, ordinal),
    UNIQUE (job_id, unit_key),
    CHECK ((profile_id IS NULL) = (profile_revision IS NULL)),
    CHECK (profile_revision IS NULL OR profile_revision > 0),
    CHECK ((checkpoint IS NULL) = (checkpoint_fingerprint IS NULL)),
    CHECK (checkpoint_fingerprint IS NULL OR checkpoint_fingerprint ~ '^[a-f0-9]{64}$'),
    CHECK ((status = 'validated') = (checkpoint IS NOT NULL)),
    CHECK (octet_length(input_snapshot::text) <= 2000000),
    CHECK (checkpoint IS NULL OR octet_length(checkpoint::text) <= 4000000)
);
CREATE INDEX artifact_run_units_status_idx ON artifact_run_units (job_id, status, ordinal);

CREATE TABLE artifact_run_unit_sources (
    job_id uuid NOT NULL,
    unit_id uuid NOT NULL,
    source_id uuid NOT NULL,
    PRIMARY KEY (job_id, unit_id, source_id),
    FOREIGN KEY (job_id, unit_id) REFERENCES artifact_run_units(job_id, unit_id) ON DELETE CASCADE,
    FOREIGN KEY (job_id, source_id) REFERENCES artifact_run_plan_sources(job_id, source_id) ON DELETE RESTRICT
);

CREATE TABLE artifact_run_unit_dependencies (
    job_id uuid NOT NULL,
    unit_id uuid NOT NULL,
    depends_on_unit_id uuid NOT NULL,
    PRIMARY KEY (job_id, unit_id, depends_on_unit_id),
    CHECK (unit_id <> depends_on_unit_id),
    FOREIGN KEY (job_id, unit_id) REFERENCES artifact_run_units(job_id, unit_id) ON DELETE CASCADE,
    FOREIGN KEY (job_id, depends_on_unit_id) REFERENCES artifact_run_units(job_id, unit_id) ON DELETE CASCADE
);

CREATE TABLE artifact_run_unit_calls (
    job_id uuid NOT NULL,
    unit_id uuid NOT NULL,
    attempt_no integer NOT NULL CHECK (attempt_no > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, unit_id, attempt_no),
    UNIQUE (job_id, attempt_no),
    FOREIGN KEY (job_id, unit_id) REFERENCES artifact_run_units(job_id, unit_id) ON DELETE CASCADE,
    FOREIGN KEY (job_id, attempt_no) REFERENCES operation_calls(operation_id, attempt_no) ON DELETE RESTRICT
);

CREATE FUNCTION protect_artifact_run_plan_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.job_id, NEW.workspace_key, NEW.collection_id, NEW.artifact_kind,
           NEW.input_fingerprint, NEW.frozen_inputs, NEW.quotas, NEW.target_artifact_id,
           NEW.target_revision_id, NEW.profile_id, NEW.profile_revision)
       IS DISTINCT FROM
       ROW(OLD.job_id, OLD.workspace_key, OLD.collection_id, OLD.artifact_kind,
           OLD.input_fingerprint, OLD.frozen_inputs, OLD.quotas, OLD.target_artifact_id,
           OLD.target_revision_id, OLD.profile_id, OLD.profile_revision) THEN
        RAISE EXCEPTION 'artifact run plan snapshot is immutable';
    END IF;
    IF OLD.status IN ('validated','failed','unknown') AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'artifact run plan terminal state is immutable';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER artifact_run_plan_snapshot_immutable
    BEFORE UPDATE ON artifact_run_plans
    FOR EACH ROW EXECUTE FUNCTION protect_artifact_run_plan_snapshot();

CREATE FUNCTION protect_artifact_run_unit_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.unit_id, NEW.job_id, NEW.ordinal, NEW.unit_key, NEW.unit_kind,
           NEW.input_fingerprint, NEW.input_snapshot, NEW.profile_id, NEW.profile_revision)
       IS DISTINCT FROM
       ROW(OLD.unit_id, OLD.job_id, OLD.ordinal, OLD.unit_key, OLD.unit_kind,
           OLD.input_fingerprint, OLD.input_snapshot, OLD.profile_id, OLD.profile_revision) THEN
        RAISE EXCEPTION 'artifact run unit snapshot is immutable';
    END IF;
    IF OLD.status IN ('validated','unknown','failed') AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'artifact run unit terminal state is immutable';
    END IF;
    IF OLD.checkpoint IS NOT NULL AND ROW(NEW.checkpoint, NEW.checkpoint_fingerprint)
       IS DISTINCT FROM ROW(OLD.checkpoint, OLD.checkpoint_fingerprint) THEN
        RAISE EXCEPTION 'artifact run unit checkpoint is immutable';
    END IF;
    IF NEW.checkpoint IS NOT NULL AND NEW.status <> 'validated' THEN
        RAISE EXCEPTION 'checkpoint requires validated state';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER artifact_run_unit_snapshot_immutable
    BEFORE UPDATE ON artifact_run_units
    FOR EACH ROW EXECUTE FUNCTION protect_artifact_run_unit_snapshot();
