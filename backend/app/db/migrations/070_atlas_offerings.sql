-- Term-scoped Atlas offerings are separate from the master course and
-- prerequisite graph. Rows are retained with their exact indexed source.

CREATE TABLE public.atlas_offering_snapshots (
    source_id uuid NOT NULL REFERENCES public.sources(source_id) ON DELETE CASCADE,
    capture_set_hash text NOT NULL,
    career text NOT NULL,
    term text NOT NULL,
    index_evidence_hash text NOT NULL,
    announced_count integer NOT NULL,
    result_row_count integer NOT NULL,
    eligible_row_count integer NOT NULL,
    held_row_count integer NOT NULL,
    search_criteria text NOT NULL,
    capture_hashes jsonb NOT NULL,
    observed_from timestamp with time zone NOT NULL,
    observed_until timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT atlas_offering_snapshots_pk PRIMARY KEY (source_id, capture_set_hash),
    CONSTRAINT atlas_offering_snapshots_capture_hash_check
        CHECK (capture_set_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT atlas_offering_snapshots_career_check CHECK (btrim(career) <> ''),
    CONSTRAINT atlas_offering_snapshots_term_check CHECK (btrim(term) <> ''),
    CONSTRAINT atlas_offering_snapshots_index_hash_check
        CHECK (index_evidence_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT atlas_offering_snapshots_count_check
        CHECK (announced_count > 0 AND result_row_count >= announced_count
               AND eligible_row_count >= 0 AND held_row_count >= 0
               AND eligible_row_count + held_row_count = result_row_count
               AND observed_until >= observed_from),
    CONSTRAINT atlas_offering_snapshots_hashes_check
        CHECK (jsonb_typeof(capture_hashes) = 'array')
);

CREATE TABLE public.atlas_offering_rows (
    source_id uuid NOT NULL,
    capture_set_hash text NOT NULL,
    row_ordinal integer NOT NULL,
    result_cursor integer NOT NULL,
    raw_course_code text NOT NULL,
    title text NOT NULL,
    class_number text,
    group_identity text NOT NULL,
    is_grouped boolean NOT NULL,
    matched_identity text NOT NULL,
    srcdb text NOT NULL,
    credits text,
    description text NOT NULL,
    restrictions text,
    notes text,
    notices jsonb NOT NULL,
    raw_metadata jsonb NOT NULL,
    evidence_hash text NOT NULL,
    review_status text NOT NULL,
    observed_at timestamp with time zone NOT NULL,
    capture_envelope_hash text NOT NULL,
    CONSTRAINT atlas_offering_rows_pk PRIMARY KEY (source_id, capture_set_hash, row_ordinal),
    CONSTRAINT atlas_offering_rows_cursor_unique UNIQUE (source_id, capture_set_hash, result_cursor),
    CONSTRAINT atlas_offering_rows_snapshot_fk FOREIGN KEY (source_id, capture_set_hash)
        REFERENCES public.atlas_offering_snapshots(source_id, capture_set_hash) ON DELETE CASCADE,
    CONSTRAINT atlas_offering_rows_ordinal_check CHECK (row_ordinal >= 0),
    CONSTRAINT atlas_offering_rows_cursor_check CHECK (result_cursor >= 0),
    CONSTRAINT atlas_offering_rows_identity_check CHECK (
        btrim(raw_course_code) <> '' AND btrim(title) <> '' AND btrim(group_identity) <> ''
    ),
    CONSTRAINT atlas_offering_rows_group_check CHECK (
        (is_grouped AND class_number IS NULL) OR (NOT is_grouped AND class_number IS NOT NULL)
    ),
    CONSTRAINT atlas_offering_rows_notices_check CHECK (jsonb_typeof(notices) = 'array'),
    CONSTRAINT atlas_offering_rows_metadata_check CHECK (jsonb_typeof(raw_metadata) = 'object'),
    CONSTRAINT atlas_offering_rows_review_status_check
        CHECK (review_status IN ('eligible', 'held_placeholder')),
    CONSTRAINT atlas_offering_rows_hash_check CHECK (
        evidence_hash ~ '^[0-9a-f]{64}$' AND capture_envelope_hash ~ '^[0-9a-f]{64}$'
    )
);

CREATE INDEX atlas_offering_rows_code_idx
    ON public.atlas_offering_rows (source_id, raw_course_code, class_number);

-- Preserve selected source envelopes separately from canonical citable Markdown.
CREATE TABLE public.atlas_offering_capture_files (
    source_id uuid NOT NULL,
    capture_set_hash text NOT NULL,
    capture_hash text NOT NULL,
    raw_path text NOT NULL,
    CONSTRAINT atlas_offering_capture_files_pk
        PRIMARY KEY (source_id, capture_set_hash, capture_hash),
    CONSTRAINT atlas_offering_capture_files_snapshot_fk
        FOREIGN KEY (source_id, capture_set_hash)
        REFERENCES public.atlas_offering_snapshots(source_id, capture_set_hash) ON DELETE CASCADE,
    CONSTRAINT atlas_offering_capture_files_hash_check CHECK (
        capture_set_hash ~ '^[0-9a-f]{64}$' AND capture_hash ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT atlas_offering_capture_files_path_check CHECK (btrim(raw_path) <> '')
);

CREATE INDEX atlas_offering_capture_files_path_idx
    ON public.atlas_offering_capture_files (raw_path);
