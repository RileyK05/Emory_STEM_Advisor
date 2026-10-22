-- Immutable academic-record snapshots and reviewed import digests.

CREATE TABLE public.academic_record_revisions (
    record_id uuid NOT NULL REFERENCES public.student_academic_records(record_id) ON DELETE CASCADE,
    revision integer NOT NULL,
    attempts jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    PRIMARY KEY (record_id, revision),
    CONSTRAINT academic_record_revisions_revision_check CHECK (revision >= 1)
);

ALTER TABLE public.academic_record_import_batches
    ADD COLUMN preview_digest text,
    ADD COLUMN row_decisions jsonb DEFAULT '[]'::jsonb NOT NULL,
    ADD COLUMN committed_count integer DEFAULT 0 NOT NULL,
    ADD CONSTRAINT academic_record_import_batches_digest_check
        CHECK (preview_digest IS NULL OR length(preview_digest) = 64),
    ADD CONSTRAINT academic_record_import_batches_committed_count_check
        CHECK (committed_count >= 0);
