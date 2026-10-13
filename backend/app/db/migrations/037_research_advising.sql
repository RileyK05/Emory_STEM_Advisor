-- Migration 037: research-advisor contract scaffolding (Milestone A).
--
-- Four additive groups, none of which changes existing behavior with the
-- feature flag off:
--
--   1. retrieval_traces.kind        -- 'retrieval' for existing rows, 'research'
--                                     for research root traces.
--   2. Program -> source binding    -- programs rows currently carry catalog/page
--                                     metadata only, so no program fact can point
--                                     at the source revision it was parsed from.
--                                     These columns make that binding possible and
--                                     make its absence explicit ('unbound') instead
--                                     of guessable. Legacy rows stay unbound: there
--                                     is no provenance to backfill from, and this
--                                     project does not guess citation-bearing facts.
--   3. source_provenance            -- controlled, curator/importer-assigned metadata
--                                     for curated external references. A document
--                                     cannot declare itself an official Emory source.
--   4. research_*                   -- durable run/evidence/decision records for the
--                                     research path. Evidence snapshots deliberately
--                                     have no live chunk foreign key so they survive
--                                     reindexing and source removal.
--
-- Applied by app.db.migrate on top of baseline 032 or on top of the legacy
-- 001-032 history; both paths must end with an identical schema catalog
-- (scripts.schema_baseline).


-- 1. Trace kind ---------------------------------------------------------------

ALTER TABLE public.retrieval_traces
    ADD COLUMN kind text DEFAULT 'retrieval' NOT NULL;

ALTER TABLE public.retrieval_traces
    ADD CONSTRAINT retrieval_traces_kind_check
    CHECK ((kind = ANY (ARRAY['retrieval'::text, 'research'::text])));

CREATE INDEX retrieval_traces_kind_idx ON public.retrieval_traces USING btree (kind);


-- 2. Program -> source binding ------------------------------------------------

-- NULL on any of these means "not bound": the projection has no verified source
-- evidence. binding_status makes that state readable without inspecting nulls.
ALTER TABLE public.programs
    ADD COLUMN source_id uuid,
    ADD COLUMN source_index_revision bigint,
    ADD COLUMN source_sha256 text,
    ADD COLUMN origin_key text,
    ADD COLUMN parsed_content_digest text,
    ADD COLUMN binding_status text DEFAULT 'unbound' NOT NULL,
    ADD COLUMN bound_at timestamp with time zone;

ALTER TABLE public.programs
    ADD CONSTRAINT programs_binding_status_check
    CHECK ((binding_status = ANY (ARRAY['bound'::text, 'unbound'::text, 'evidence_unresolved'::text])));

-- A bound program must name the source version it came from.
ALTER TABLE public.programs
    ADD CONSTRAINT programs_bound_requires_source_check
    CHECK (((binding_status <> 'bound'::text) OR (source_id IS NOT NULL)));

ALTER TABLE public.programs
    ADD CONSTRAINT programs_source_index_revision_check
    CHECK (((source_index_revision IS NULL) OR (source_index_revision >= 0)));

-- Exact-SHA ingest identity, not a filename/title match. source_id may dangle
-- after a source is removed, so this is deliberately NOT a foreign key: a bound
-- program keeps its provenance record instead of being deleted or left invalid.
CREATE INDEX programs_source_id_idx ON public.programs USING btree (source_id);
CREATE INDEX programs_binding_status_idx ON public.programs USING btree (binding_status);


-- Ordered supporting chunks for one parsed requirement item. An item with no
-- rows here is a discovery lead only: it must not be presented as a cited
-- requirement. chunk_id/locator_id carry no foreign key so the evidence survives
-- reindexing and source removal (same rule as citations/excerpt snapshots).
CREATE TABLE public.program_item_evidence (
    program_id uuid NOT NULL,
    "position" integer NOT NULL,
    ordinal integer NOT NULL,
    chunk_id uuid NOT NULL,
    locator_id uuid,
    source_id uuid,
    source_index_revision bigint,
    span_start integer,
    span_end integer,
    text_digest text,
    CONSTRAINT program_item_evidence_span_check CHECK (((span_start IS NULL) OR (span_start >= 0))),
    CONSTRAINT program_item_evidence_span_order_check
        CHECK (((span_start IS NULL) OR (span_end IS NULL) OR (span_end >= span_start)))
);

ALTER TABLE ONLY public.program_item_evidence
    ADD CONSTRAINT program_item_evidence_pkey PRIMARY KEY (program_id, "position", chunk_id, ordinal);

ALTER TABLE ONLY public.program_item_evidence
    ADD CONSTRAINT program_item_evidence_item_fkey
    FOREIGN KEY (program_id, "position") REFERENCES public.program_items(program_id, "position") ON DELETE CASCADE;

CREATE INDEX program_item_evidence_chunk_idx ON public.program_item_evidence USING btree (chunk_id);


-- 3. Source provenance --------------------------------------------------------

-- Controlled provenance for curated documents. source_category is assigned by an
-- explicitly verified importer/curator; an ordinary upload stays 'user_supplied'.
CREATE TABLE public.source_provenance (
    source_id uuid NOT NULL,
    source_category text DEFAULT 'unclassified' NOT NULL,
    classification_origin text DEFAULT 'legacy_unclassified' NOT NULL,
    publisher text,
    source_url text,
    edition text,
    geography text,
    occupation_id text,
    published_at date,
    acquired_at date,
    attribution text,
    content_hash text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT source_provenance_category_check CHECK ((source_category = ANY (ARRAY[
        'institutional_catalog'::text, 'institutional_department'::text,
        'institutional_policy'::text, 'career_reference'::text,
        'user_supplied'::text, 'unclassified'::text]))),
    CONSTRAINT source_provenance_origin_check CHECK ((classification_origin = ANY (ARRAY[
        'verified_import'::text, 'verified_curator'::text, 'legacy_unclassified'::text]))),
    CONSTRAINT source_provenance_hash_check CHECK (((length(content_hash) = 64)))
);

ALTER TABLE ONLY public.source_provenance
    ADD CONSTRAINT source_provenance_pkey PRIMARY KEY (source_id);

ALTER TABLE ONLY public.source_provenance
    ADD CONSTRAINT source_provenance_source_fkey
    FOREIGN KEY (source_id) REFERENCES public.sources(source_id) ON DELETE CASCADE;

CREATE INDEX source_provenance_category_idx ON public.source_provenance USING btree (source_category);


-- 4. Research persistence -----------------------------------------------------

CREATE TABLE public.research_runs (
    run_id uuid NOT NULL,
    operation_id uuid,
    root_trace_id uuid,
    workspace_key text,
    collection_id uuid NOT NULL,
    conversation_id uuid,
    conversation_request_id uuid,
    scope_manifest jsonb NOT NULL,
    scope_digest text NOT NULL,
    prompt_version text NOT NULL,
    config_version text NOT NULL,
    state_revision integer DEFAULT 0 NOT NULL,
    state jsonb,
    status text DEFAULT 'running' NOT NULL,
    stop_reason text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT research_runs_status_check CHECK ((status = ANY (ARRAY[
        'running'::text, 'completed'::text, 'partial'::text, 'clarification'::text,
        'insufficient_evidence'::text, 'interrupted'::text, 'failed'::text, 'cancelled'::text]))),
    CONSTRAINT research_runs_state_revision_check CHECK ((state_revision >= 0))
);

ALTER TABLE ONLY public.research_runs
    ADD CONSTRAINT research_runs_pkey PRIMARY KEY (run_id);

ALTER TABLE ONLY public.research_runs
    ADD CONSTRAINT research_runs_collection_fkey
    FOREIGN KEY (collection_id) REFERENCES public.collections(collection_id) ON DELETE CASCADE;

-- A conversation request runs at most one research run; replay reads the saved
-- result instead of starting a second investigation.
ALTER TABLE ONLY public.research_runs
    ADD CONSTRAINT research_runs_conversation_request_fkey
    FOREIGN KEY (conversation_id, conversation_request_id)
    REFERENCES public.conversation_requests(conversation_id, request_id);

CREATE UNIQUE INDEX research_runs_conversation_request_uq
    ON public.research_runs (conversation_id, conversation_request_id)
    WHERE (conversation_request_id IS NOT NULL);

CREATE INDEX research_runs_operation_idx ON public.research_runs USING btree (operation_id);
CREATE INDEX research_runs_workspace_idx ON public.research_runs USING btree (workspace_key);


CREATE TABLE public.research_steps (
    run_id uuid NOT NULL,
    ordinal integer NOT NULL,
    tool_execution_id uuid,
    child_trace_id uuid,
    action_kind text NOT NULL,
    summary text NOT NULL,
    issued_evidence_ids jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT research_steps_ordinal_check CHECK ((ordinal >= 0))
);

ALTER TABLE ONLY public.research_steps
    ADD CONSTRAINT research_steps_pkey PRIMARY KEY (run_id, ordinal);

ALTER TABLE ONLY public.research_steps
    ADD CONSTRAINT research_steps_run_fkey
    FOREIGN KEY (run_id) REFERENCES public.research_runs(run_id) ON DELETE CASCADE;


-- One server-issued evidence item (a single locator-scoped chunk) per run.
-- evidence_id is stable within the run and is never reassigned after compaction.
CREATE TABLE public.research_evidence (
    run_id uuid NOT NULL,
    evidence_id text NOT NULL,
    snapshot jsonb NOT NULL,
    digest text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY public.research_evidence
    ADD CONSTRAINT research_evidence_pkey PRIMARY KEY (run_id, evidence_id);

ALTER TABLE ONLY public.research_evidence
    ADD CONSTRAINT research_evidence_run_fkey
    FOREIGN KEY (run_id) REFERENCES public.research_runs(run_id) ON DELETE CASCADE;


CREATE TABLE public.research_decisions (
    run_id uuid NOT NULL,
    response_id uuid,
    render_version text NOT NULL,
    decision jsonb NOT NULL,
    digest text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY public.research_decisions
    ADD CONSTRAINT research_decisions_pkey PRIMARY KEY (run_id, render_version);

ALTER TABLE ONLY public.research_decisions
    ADD CONSTRAINT research_decisions_run_fkey
    FOREIGN KEY (run_id) REFERENCES public.research_runs(run_id) ON DELETE CASCADE;

CREATE INDEX research_decisions_response_idx ON public.research_decisions USING btree (response_id);
