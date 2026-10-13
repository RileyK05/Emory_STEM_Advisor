-- Research data layer, part 2.
--
-- 1. Supporting chunks for a program's header fields (title, degree, hours, courses
-- required, department), the counterpart of program_item_evidence for the
-- requirement lines. Research reads present a header value as a cited fact only
-- when it has rows here; otherwise the value is a discovery lead.
--
-- Same rules as program_item_evidence: no foreign key to chunks or sources, so
-- evidence survives reindexing and removal; spans are offsets into the chunk text.

CREATE TABLE public.program_field_evidence (
    program_id uuid NOT NULL,
    field text NOT NULL,
    ordinal integer NOT NULL,
    chunk_id uuid NOT NULL,
    locator_id uuid,
    source_id uuid,
    source_index_revision bigint,
    span_start integer,
    span_end integer,
    text_digest text,
    CONSTRAINT program_field_evidence_field_check CHECK ((field = ANY (ARRAY[
        'title'::text, 'degree'::text, 'hours'::text, 'courses_required'::text,
        'department'::text]))),
    CONSTRAINT program_field_evidence_ordinal_check CHECK ((ordinal >= 0)),
    CONSTRAINT program_field_evidence_span_check CHECK (((span_start IS NULL) OR (span_start >= 0))),
    CONSTRAINT program_field_evidence_span_order_check
        CHECK (((span_start IS NULL) OR (span_end IS NULL) OR (span_end >= span_start)))
);

ALTER TABLE ONLY public.program_field_evidence
    ADD CONSTRAINT program_field_evidence_pkey PRIMARY KEY (program_id, field, ordinal);

ALTER TABLE ONLY public.program_field_evidence
    ADD CONSTRAINT program_field_evidence_program_fkey
    FOREIGN KEY (program_id) REFERENCES public.programs(program_id) ON DELETE CASCADE;


-- A tool execution records at most one research step, so a replayed tool result
-- restores the same step and evidence IDs instead of issuing new ones.
CREATE UNIQUE INDEX research_steps_tool_execution_uq
    ON public.research_steps (run_id, tool_execution_id)
    WHERE (tool_execution_id IS NOT NULL);
