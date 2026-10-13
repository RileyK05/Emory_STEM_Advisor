-- Private workspaces are enforced at lookup; generated artifacts are not sources.
CREATE TABLE artifacts (
    artifact_id uuid PRIMARY KEY,
    workspace_key text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('document','workbook','html')),
    head_revision_id uuid NOT NULL,
    original_filename text,
    original_sha256 text,
    original_path text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX artifacts_workspace_idx ON artifacts (workspace_key, created_at);
CREATE TABLE artifact_revisions (
    revision_id uuid PRIMARY KEY,
    artifact_id uuid NOT NULL REFERENCES artifacts ON DELETE CASCADE,
    parent_revision_id uuid,
    draft_id text,
    spec jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (artifact_id, revision_id),
    UNIQUE (artifact_id, draft_id),
    FOREIGN KEY (artifact_id, parent_revision_id)
        REFERENCES artifact_revisions (artifact_id, revision_id),
    CHECK (octet_length(spec::text) <= 16000000)
);
ALTER TABLE artifacts ADD CONSTRAINT artifacts_head_revision_fk
    FOREIGN KEY (artifact_id, head_revision_id)
    REFERENCES artifact_revisions (artifact_id, revision_id)
    DEFERRABLE INITIALLY DEFERRED;
