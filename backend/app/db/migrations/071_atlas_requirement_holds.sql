-- Curator-reviewed conflicts can block one course's master requirements while
-- preserving the immutable atlas source and its citations.
CREATE TABLE public.atlas_requirement_holds (
    hold_id uuid PRIMARY KEY,
    source_id uuid NOT NULL REFERENCES public.sources(source_id) ON DELETE CASCADE,
    course_code text NOT NULL,
    master_file_hash text NOT NULL,
    conflict_reason text NOT NULL,
    evidence_capture_sha256 text NOT NULL,
    evidence_capture_path text NOT NULL,
    evidence_source_id uuid REFERENCES public.sources(source_id) ON DELETE SET NULL,
    evidence_index_revision integer,
    evidence_capture_set_hash text,
    status text DEFAULT 'active' NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz DEFAULT now() NOT NULL,
    released_by text,
    released_at timestamptz,
    release_note text,
    CONSTRAINT atlas_requirement_holds_master_hash_check
        CHECK (master_file_hash ~ '^[a-f0-9]{64}$'),
    CONSTRAINT atlas_requirement_holds_capture_hash_check
        CHECK (evidence_capture_sha256 ~ '^[a-f0-9]{64}$'),
    CONSTRAINT atlas_requirement_holds_code_check CHECK (btrim(course_code) <> ''),
    CONSTRAINT atlas_requirement_holds_reason_check CHECK (btrim(conflict_reason) <> ''),
    CONSTRAINT atlas_requirement_holds_path_check CHECK (btrim(evidence_capture_path) <> ''),
    CONSTRAINT atlas_requirement_holds_status_check CHECK (status IN ('active', 'released')),
    CONSTRAINT atlas_requirement_holds_release_check CHECK (
        (status = 'active' AND released_by IS NULL AND released_at IS NULL)
        OR (status = 'released' AND released_by IS NOT NULL AND released_at IS NOT NULL)
    ),
    CONSTRAINT atlas_requirement_holds_offering_revision_check CHECK (
        evidence_index_revision IS NULL OR evidence_index_revision > 0
    ),
    CONSTRAINT atlas_requirement_holds_capture_set_hash_check CHECK (
        evidence_capture_set_hash IS NULL OR evidence_capture_set_hash ~ '^[a-f0-9]{64}$'
    )
);

CREATE UNIQUE INDEX atlas_requirement_holds_one_active_course_idx
    ON public.atlas_requirement_holds (source_id, course_code, master_file_hash)
    WHERE status = 'active';

CREATE INDEX atlas_requirement_holds_evidence_idx
    ON public.atlas_requirement_holds (evidence_source_id, evidence_capture_set_hash)
    WHERE evidence_source_id IS NOT NULL;
