-- Consolidated schema checkpoint 032, generated from immutable legacy migrations.
-- Fresh installs apply this file once; existing databases retain their legacy history.
-- Future schema changes append migration 033 and higher.
CREATE EXTENSION IF NOT EXISTS vector;
--
-- PostgreSQL database dump
--



--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--



--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--



--
-- Name: protect_artifact_run_attempt(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.protect_artifact_run_attempt() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: protect_artifact_run_excerpt(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.protect_artifact_run_excerpt() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: protect_artifact_run_plan_snapshot(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.protect_artifact_run_plan_snapshot() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: protect_artifact_run_unit_snapshot(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.protect_artifact_run_unit_snapshot() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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




--
-- Name: artifact_revisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.artifact_revisions (
    revision_id uuid NOT NULL,
    artifact_id uuid NOT NULL,
    parent_revision_id uuid,
    draft_id text,
    spec jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT artifact_revisions_spec_check CHECK ((octet_length((spec)::text) <= 16000000))
);


--
-- Name: artifact_run_plan_excerpts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.artifact_run_plan_excerpts (
    job_id uuid NOT NULL,
    excerpt_id integer NOT NULL,
    source_id uuid NOT NULL,
    chunk_id uuid NOT NULL,
    source_name text NOT NULL,
    locator_label text NOT NULL,
    text_snapshot text NOT NULL,
    excerpt_fingerprint character(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    locator_id uuid,
    CONSTRAINT artifact_run_plan_excerpts_excerpt_fingerprint_check CHECK ((excerpt_fingerprint ~ '^[a-f0-9]{64}$'::text)),
    CONSTRAINT artifact_run_plan_excerpts_excerpt_id_check CHECK (((excerpt_id >= 1) AND (excerpt_id <= 500))),
    CONSTRAINT artifact_run_plan_excerpts_locator_label_check CHECK (((length(locator_label) >= 1) AND (length(locator_label) <= 500))),
    CONSTRAINT artifact_run_plan_excerpts_source_name_check CHECK (((length(source_name) >= 1) AND (length(source_name) <= 500))),
    CONSTRAINT artifact_run_plan_excerpts_text_snapshot_check CHECK (((length(text_snapshot) >= 1) AND (length(text_snapshot) <= 100000)))
);


--
-- Name: artifact_run_plan_sources; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.artifact_run_plan_sources (
    job_id uuid NOT NULL,
    source_id uuid NOT NULL,
    collection_id uuid NOT NULL,
    file_hash text NOT NULL,
    index_revision bigint NOT NULL,
    CONSTRAINT artifact_run_plan_sources_file_hash_check CHECK (((length(file_hash) >= 1) AND (length(file_hash) <= 256))),
    CONSTRAINT artifact_run_plan_sources_index_revision_check CHECK ((index_revision >= 0))
);


--
-- Name: artifact_run_plans; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.artifact_run_plans (
    job_id uuid NOT NULL,
    workspace_key text NOT NULL,
    collection_id uuid NOT NULL,
    artifact_kind text NOT NULL,
    status text NOT NULL,
    input_fingerprint character(64) NOT NULL,
    frozen_inputs jsonb NOT NULL,
    quotas jsonb NOT NULL,
    target_artifact_id uuid,
    target_revision_id uuid,
    profile_id uuid,
    profile_revision integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT artifact_run_plans_artifact_kind_check CHECK ((artifact_kind = ANY (ARRAY['document'::text, 'workbook'::text, 'html'::text]))),
    CONSTRAINT artifact_run_plans_check CHECK (((profile_id IS NULL) = (profile_revision IS NULL))),
    CONSTRAINT artifact_run_plans_check1 CHECK (((target_artifact_id IS NULL) = (target_revision_id IS NULL))),
    CONSTRAINT artifact_run_plans_frozen_inputs_check CHECK ((octet_length((frozen_inputs)::text) <= 2000000)),
    CONSTRAINT artifact_run_plans_input_fingerprint_check CHECK ((input_fingerprint ~ '^[a-f0-9]{64}$'::text)),
    CONSTRAINT artifact_run_plans_profile_revision_check CHECK (((profile_revision IS NULL) OR (profile_revision > 0))),
    CONSTRAINT artifact_run_plans_quotas_check CHECK ((octet_length((quotas)::text) <= 4000)),
    CONSTRAINT artifact_run_plans_status_check CHECK ((status = ANY (ARRAY['ready'::text, 'running'::text, 'validated'::text, 'failed'::text, 'unknown'::text]))),
    CONSTRAINT artifact_run_plans_workspace_key_check CHECK (((length(workspace_key) >= 1) AND (length(workspace_key) <= 200)))
);


--
-- Name: artifact_run_unit_attempts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.artifact_run_unit_attempts (
    job_id uuid NOT NULL,
    unit_id uuid NOT NULL,
    attempt_no integer NOT NULL,
    outcome text NOT NULL,
    candidate_text text,
    candidate_sha256 character(64) NOT NULL,
    candidate_truncated boolean DEFAULT false NOT NULL,
    validation_issues jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT artifact_run_unit_attempts_attempt_no_check CHECK ((attempt_no > 0)),
    CONSTRAINT artifact_run_unit_attempts_candidate_sha256_check CHECK ((candidate_sha256 ~ '^[a-f0-9]{64}$'::text)),
    CONSTRAINT artifact_run_unit_attempts_candidate_text_check CHECK (((candidate_text IS NULL) OR (octet_length(candidate_text) <= 4000000))),
    CONSTRAINT artifact_run_unit_attempts_check CHECK (((outcome = 'invalid'::text) = (jsonb_array_length(validation_issues) > 0))),
    CONSTRAINT artifact_run_unit_attempts_outcome_check CHECK ((outcome = ANY (ARRAY['invalid'::text, 'validated'::text, 'failed'::text, 'unknown'::text]))),
    CONSTRAINT artifact_run_unit_attempts_validation_issues_check CHECK ((jsonb_typeof(validation_issues) = 'array'::text)),
    CONSTRAINT artifact_run_unit_attempts_validation_issues_check1 CHECK ((octet_length((validation_issues)::text) <= 16000))
);


--
-- Name: artifact_run_unit_calls; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.artifact_run_unit_calls (
    job_id uuid NOT NULL,
    unit_id uuid NOT NULL,
    attempt_no integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT artifact_run_unit_calls_attempt_no_check CHECK ((attempt_no > 0))
);


--
-- Name: artifact_run_unit_dependencies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.artifact_run_unit_dependencies (
    job_id uuid NOT NULL,
    unit_id uuid NOT NULL,
    depends_on_unit_id uuid NOT NULL,
    CONSTRAINT artifact_run_unit_dependencies_check CHECK ((unit_id <> depends_on_unit_id))
);


--
-- Name: artifact_run_unit_sources; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.artifact_run_unit_sources (
    job_id uuid NOT NULL,
    unit_id uuid NOT NULL,
    source_id uuid NOT NULL
);


--
-- Name: artifact_run_units; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.artifact_run_units (
    unit_id uuid NOT NULL,
    job_id uuid NOT NULL,
    ordinal integer NOT NULL,
    unit_key text NOT NULL,
    unit_kind text NOT NULL,
    status text NOT NULL,
    input_fingerprint character(64) NOT NULL,
    input_snapshot jsonb NOT NULL,
    profile_id uuid,
    profile_revision integer,
    checkpoint jsonb,
    checkpoint_fingerprint character(64),
    failure_code text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT artifact_run_units_check CHECK (((profile_id IS NULL) = (profile_revision IS NULL))),
    CONSTRAINT artifact_run_units_check1 CHECK (((checkpoint IS NULL) = (checkpoint_fingerprint IS NULL))),
    CONSTRAINT artifact_run_units_check2 CHECK (((status = 'validated'::text) = (checkpoint IS NOT NULL))),
    CONSTRAINT artifact_run_units_checkpoint_check CHECK (((checkpoint IS NULL) OR (octet_length((checkpoint)::text) <= 4000000))),
    CONSTRAINT artifact_run_units_checkpoint_fingerprint_check CHECK (((checkpoint_fingerprint IS NULL) OR (checkpoint_fingerprint ~ '^[a-f0-9]{64}$'::text))),
    CONSTRAINT artifact_run_units_failure_code_check CHECK (((failure_code IS NULL) OR ((length(failure_code) >= 1) AND (length(failure_code) <= 80)))),
    CONSTRAINT artifact_run_units_input_fingerprint_check CHECK ((input_fingerprint ~ '^[a-f0-9]{64}$'::text)),
    CONSTRAINT artifact_run_units_input_snapshot_check CHECK ((octet_length((input_snapshot)::text) <= 2000000)),
    CONSTRAINT artifact_run_units_ordinal_check CHECK (((ordinal >= 0) AND (ordinal <= 99))),
    CONSTRAINT artifact_run_units_profile_revision_check CHECK (((profile_revision IS NULL) OR (profile_revision > 0))),
    CONSTRAINT artifact_run_units_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'calling'::text, 'needs_repair'::text, 'validated'::text, 'unknown'::text, 'failed'::text]))),
    CONSTRAINT artifact_run_units_unit_key_check CHECK (((length(unit_key) >= 1) AND (length(unit_key) <= 120))),
    CONSTRAINT artifact_run_units_unit_kind_check CHECK ((unit_kind = ANY (ARRAY['section'::text, 'worksheet'::text, 'repair'::text, 'assembly'::text])))
);


--
-- Name: artifacts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.artifacts (
    artifact_id uuid NOT NULL,
    workspace_key text NOT NULL,
    kind text NOT NULL,
    head_revision_id uuid NOT NULL,
    original_filename text,
    original_sha256 text,
    original_path text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT artifacts_kind_check CHECK ((kind = ANY (ARRAY['document'::text, 'workbook'::text, 'html'::text])))
);


--
-- Name: auth_login_transactions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.auth_login_transactions (
    state_hash bytea NOT NULL,
    nonce text NOT NULL,
    code_verifier text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL
);


--
-- Name: auth_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.auth_sessions (
    session_hash bytea NOT NULL,
    issuer text NOT NULL,
    subject text NOT NULL,
    workspace_key text NOT NULL,
    display_name text,
    email text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone
);


--
-- Name: chunk_embeddings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chunk_embeddings (
    chunk_id uuid NOT NULL,
    model text NOT NULL,
    dim integer NOT NULL,
    embedding public.vector NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chunk_embeddings_check CHECK ((public.vector_dims(embedding) = dim))
);


--
-- Name: chunks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chunks (
    chunk_id uuid NOT NULL,
    source_id uuid NOT NULL,
    locator_id uuid NOT NULL,
    chunk_index integer NOT NULL,
    start_char integer NOT NULL,
    end_char integer NOT NULL,
    token_count integer NOT NULL,
    text text NOT NULL,
    search_vector tsvector GENERATED ALWAYS AS (to_tsvector('english'::regconfig, text)) STORED
);


--
-- Name: citations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.citations (
    response_id uuid NOT NULL,
    ordinal integer NOT NULL,
    chunk_id uuid NOT NULL,
    source_id uuid NOT NULL,
    source_name text NOT NULL,
    locator_label text NOT NULL,
    text_snapshot text NOT NULL
);


--
-- Name: claim_review_units; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.claim_review_units (
    review_id uuid NOT NULL,
    ordinal integer NOT NULL,
    answer_start integer NOT NULL,
    answer_end integer NOT NULL,
    answer_text text NOT NULL,
    coverage_kind text NOT NULL,
    model_assessment text NOT NULL,
    confidence double precision,
    evidence_spans jsonb DEFAULT '[]'::jsonb NOT NULL,
    CONSTRAINT claim_review_units_answer_start_check CHECK ((answer_start >= 0)),
    CONSTRAINT claim_review_units_answer_text_check CHECK ((length(answer_text) > 0)),
    CONSTRAINT claim_review_units_check CHECK ((answer_end > answer_start)),
    CONSTRAINT claim_review_units_check1 CHECK ((((coverage_kind = 'unreviewed_gap'::text) AND (model_assessment = 'unreviewed'::text)) OR (coverage_kind = 'claim'::text))),
    CONSTRAINT claim_review_units_confidence_check CHECK (((confidence IS NULL) OR ((confidence >= (0)::double precision) AND (confidence <= (1)::double precision)))),
    CONSTRAINT claim_review_units_coverage_kind_check CHECK ((coverage_kind = ANY (ARRAY['claim'::text, 'unreviewed_gap'::text]))),
    CONSTRAINT claim_review_units_evidence_spans_check CHECK ((octet_length((evidence_spans)::text) <= 200000)),
    CONSTRAINT claim_review_units_model_assessment_check CHECK ((model_assessment = ANY (ARRAY['supported'::text, 'contradicted'::text, 'insufficient_evidence'::text, 'conflicting_evidence'::text, 'uncertain'::text, 'abstained'::text, 'unreviewed'::text, 'invalid'::text]))),
    CONSTRAINT claim_review_units_ordinal_check CHECK ((ordinal >= 0))
);


--
-- Name: claim_reviews; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.claim_reviews (
    review_id uuid NOT NULL,
    response_id uuid NOT NULL,
    workspace_key text NOT NULL,
    operation_id uuid NOT NULL,
    profile_id uuid NOT NULL,
    profile_revision integer NOT NULL,
    provider text NOT NULL,
    model_id text NOT NULL,
    review_version integer NOT NULL,
    request_fingerprint character(64) NOT NULL,
    binding_fingerprint character(64) NOT NULL,
    input_snapshot jsonb NOT NULL,
    state text NOT NULL,
    usage_source text DEFAULT 'unknown'::text NOT NULL,
    prompt_tokens integer,
    completion_tokens integer,
    failure_code text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT claim_reviews_binding_fingerprint_check CHECK ((binding_fingerprint ~ '^[a-f0-9]{64}$'::text)),
    CONSTRAINT claim_reviews_check CHECK (((prompt_tokens IS NULL) = (completion_tokens IS NULL))),
    CONSTRAINT claim_reviews_completion_tokens_check CHECK (((completion_tokens IS NULL) OR (completion_tokens >= 0))),
    CONSTRAINT claim_reviews_input_snapshot_check CHECK ((octet_length((input_snapshot)::text) <= 1000000)),
    CONSTRAINT claim_reviews_profile_revision_check CHECK ((profile_revision > 0)),
    CONSTRAINT claim_reviews_prompt_tokens_check CHECK (((prompt_tokens IS NULL) OR (prompt_tokens >= 0))),
    CONSTRAINT claim_reviews_request_fingerprint_check CHECK ((request_fingerprint ~ '^[a-f0-9]{64}$'::text)),
    CONSTRAINT claim_reviews_review_version_check CHECK ((review_version > 0)),
    CONSTRAINT claim_reviews_state_check CHECK ((state = ANY (ARRAY['pending'::text, 'completed'::text, 'refused'::text, 'invalid'::text, 'failed'::text, 'unknown'::text, 'abandoned'::text]))),
    CONSTRAINT claim_reviews_usage_source_check CHECK ((usage_source = ANY (ARRAY['provider'::text, 'estimated'::text, 'unknown'::text]))),
    CONSTRAINT claim_reviews_workspace_key_check CHECK (((length(workspace_key) >= 1) AND (length(workspace_key) <= 200)))
);


--
-- Name: claims; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.claims (
    claim_id uuid NOT NULL,
    response_id uuid NOT NULL,
    ordinal integer NOT NULL,
    text text NOT NULL,
    citation_numbers jsonb NOT NULL,
    review_status text DEFAULT 'unreviewed'::text NOT NULL,
    CONSTRAINT claims_review_status_check CHECK ((review_status = ANY (ARRAY['unreviewed'::text, 'supported'::text, 'unsupported'::text, 'uncertain'::text])))
);


--
-- Name: collections; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.collections (
    collection_id uuid NOT NULL,
    name text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: connection_profile_evaluations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.connection_profile_evaluations (
    evaluation_id uuid NOT NULL,
    profile_id uuid NOT NULL,
    profile_revision integer NOT NULL,
    provider text NOT NULL,
    model_id text NOT NULL,
    suite_hash text NOT NULL,
    status text NOT NULL,
    metrics jsonb NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT connection_profile_evaluations_profile_revision_check CHECK ((profile_revision > 0)),
    CONSTRAINT connection_profile_evaluations_status_check CHECK ((status = ANY (ARRAY['passed'::text, 'failed'::text]))),
    CONSTRAINT connection_profile_evaluations_suite_hash_check CHECK ((length(suite_hash) = 64))
);


--
-- Name: connection_profiles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.connection_profiles (
    profile_id uuid NOT NULL,
    name text NOT NULL,
    provider text NOT NULL,
    model_id text NOT NULL,
    base_url text,
    account_id text,
    secret_ciphertext bytea,
    provider_options jsonb DEFAULT '{}'::jsonb NOT NULL,
    roles text[] DEFAULT ARRAY[]::text[] NOT NULL,
    capabilities text[] DEFAULT ARRAY[]::text[] NOT NULL,
    data_scopes text[] DEFAULT ARRAY[]::text[] NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    priority integer DEFAULT 100 NOT NULL,
    revision integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT connection_profiles_capabilities_check CHECK ((capabilities <@ ARRAY['structured_output'::text, 'tools'::text, 'streaming'::text, 'vision'::text, 'batch'::text, 'decision'::text])),
    CONSTRAINT connection_profiles_check CHECK (((provider = 'cloudflare-clef'::text) = (account_id IS NOT NULL))),
    CONSTRAINT connection_profiles_check1 CHECK (((provider <> 'hf-local'::text) OR (secret_ciphertext IS NULL))),
    CONSTRAINT connection_profiles_data_scopes_check CHECK ((data_scopes <@ ARRAY['institutional_corpus'::text, 'user_supplied_data'::text, 'private_workspace'::text])),
    CONSTRAINT connection_profiles_model_id_check CHECK (((length(model_id) >= 1) AND (length(model_id) <= 200))),
    CONSTRAINT connection_profiles_name_check CHECK (((length(name) >= 1) AND (length(name) <= 120))),
    CONSTRAINT connection_profiles_provider_check CHECK ((provider = ANY (ARRAY['openai'::text, 'anthropic'::text, 'google'::text, 'openai-compatible'::text, 'hf-local'::text, 'openai-decisions'::text, 'cloudflare-clef'::text]))),
    CONSTRAINT connection_profiles_revision_check CHECK ((revision > 0)),
    CONSTRAINT connection_profiles_roles_check CHECK ((roles <@ ARRAY['standard'::text, 'complex'::text, 'fast_generation'::text, 'decision'::text]))
);


--
-- Name: conversation_requests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.conversation_requests (
    conversation_id uuid NOT NULL,
    request_id uuid NOT NULL,
    fingerprint text NOT NULL,
    state text NOT NULL,
    input_snapshot jsonb NOT NULL,
    result jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    job_id uuid,
    CONSTRAINT conversation_requests_state_check CHECK ((state = ANY (ARRAY['pending'::text, 'completed'::text, 'failed'::text, 'abandoned'::text])))
);


--
-- Name: conversation_summaries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.conversation_summaries (
    summary_id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    workspace_key text NOT NULL,
    expected_sequence bigint NOT NULL,
    candidate_covered_through bigint NOT NULL,
    covered_through bigint,
    request_fingerprint character(64) NOT NULL,
    input_fingerprint character(64) NOT NULL,
    input_snapshot jsonb NOT NULL,
    reference_metadata jsonb NOT NULL,
    state text NOT NULL,
    summary_text text,
    failure_code text,
    profile_id uuid,
    profile_revision integer,
    operation_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT conversation_summaries_candidate_covered_through_check CHECK ((candidate_covered_through > 0)),
    CONSTRAINT conversation_summaries_check CHECK (((state = 'completed'::text) = (summary_text IS NOT NULL))),
    CONSTRAINT conversation_summaries_check1 CHECK (((state = 'completed'::text) = (covered_through IS NOT NULL))),
    CONSTRAINT conversation_summaries_check2 CHECK (((covered_through IS NULL) OR (covered_through = candidate_covered_through))),
    CONSTRAINT conversation_summaries_covered_through_check CHECK (((covered_through IS NULL) OR (covered_through > 0))),
    CONSTRAINT conversation_summaries_expected_sequence_check CHECK ((expected_sequence >= 0)),
    CONSTRAINT conversation_summaries_input_fingerprint_check CHECK ((input_fingerprint ~ '^[a-f0-9]{64}$'::text)),
    CONSTRAINT conversation_summaries_input_snapshot_check CHECK ((octet_length((input_snapshot)::text) <= 1000000)),
    CONSTRAINT conversation_summaries_profile_revision_check CHECK (((profile_revision IS NULL) OR (profile_revision > 0))),
    CONSTRAINT conversation_summaries_reference_metadata_check CHECK ((octet_length((reference_metadata)::text) <= 1000000)),
    CONSTRAINT conversation_summaries_request_fingerprint_check CHECK ((request_fingerprint ~ '^[a-f0-9]{64}$'::text)),
    CONSTRAINT conversation_summaries_state_check CHECK ((state = ANY (ARRAY['pending'::text, 'completed'::text, 'refused'::text, 'failed'::text, 'stale'::text]))),
    CONSTRAINT conversation_summaries_workspace_key_check CHECK (((length(workspace_key) >= 1) AND (length(workspace_key) <= 200)))
);


--
-- Name: conversation_turns; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.conversation_turns (
    conversation_id uuid NOT NULL,
    sequence bigint NOT NULL,
    request_id uuid NOT NULL,
    role text NOT NULL,
    text text NOT NULL,
    status text NOT NULL,
    evidence jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT conversation_turns_role_check CHECK ((role = ANY (ARRAY['user'::text, 'assistant'::text]))),
    CONSTRAINT conversation_turns_sequence_check CHECK ((sequence > 0)),
    CONSTRAINT conversation_turns_status_check CHECK ((status = ANY (ARRAY['input'::text, 'answer'::text, 'refusal'::text, 'history'::text, 'failed'::text, 'abandoned'::text])))
);


--
-- Name: conversations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.conversations (
    conversation_id uuid NOT NULL,
    workspace_key text NOT NULL,
    title text NOT NULL,
    last_sequence bigint DEFAULT 0 NOT NULL,
    pending_request_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT conversations_title_check CHECK (((length(title) >= 1) AND (length(title) <= 200)))
);


--
-- Name: courses; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.courses (
    course_id uuid NOT NULL,
    collection_id uuid NOT NULL,
    source_id uuid NOT NULL,
    locator_id uuid NOT NULL,
    code text NOT NULL,
    subject text NOT NULL,
    title text NOT NULL,
    requisites_raw text,
    parse_status text NOT NULL,
    parser_version text NOT NULL,
    CONSTRAINT courses_parse_status_check CHECK ((parse_status = ANY (ARRAY['none'::text, 'parsed'::text, 'ambiguous'::text, 'unparsed'::text])))
);


--
-- Name: decisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.decisions (
    decision_id uuid NOT NULL,
    workspace_key text NOT NULL,
    operation_id uuid NOT NULL,
    profile_id uuid NOT NULL,
    profile_revision integer NOT NULL,
    provider text NOT NULL,
    model_id text NOT NULL,
    request_fingerprint character(64) NOT NULL,
    request_json jsonb NOT NULL,
    state text NOT NULL,
    prediction boolean,
    probability double precision,
    confidence double precision,
    refused boolean DEFAULT false NOT NULL,
    usage_source text DEFAULT 'unknown'::text NOT NULL,
    prompt_tokens integer,
    completion_tokens integer,
    error_code text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT decisions_check CHECK (((prompt_tokens IS NULL) = (completion_tokens IS NULL))),
    CONSTRAINT decisions_completion_tokens_check CHECK (((completion_tokens IS NULL) OR (completion_tokens >= 0))),
    CONSTRAINT decisions_confidence_check CHECK (((confidence IS NULL) OR ((confidence >= (0)::double precision) AND (confidence <= (1)::double precision)))),
    CONSTRAINT decisions_probability_check CHECK (((probability IS NULL) OR ((probability >= (0)::double precision) AND (probability <= (1)::double precision)))),
    CONSTRAINT decisions_profile_revision_check CHECK ((profile_revision > 0)),
    CONSTRAINT decisions_prompt_tokens_check CHECK (((prompt_tokens IS NULL) OR (prompt_tokens >= 0))),
    CONSTRAINT decisions_request_fingerprint_check CHECK ((request_fingerprint ~ '^[a-f0-9]{64}$'::text)),
    CONSTRAINT decisions_state_check CHECK ((state = ANY (ARRAY['pending'::text, 'completed'::text, 'refused'::text, 'invalid'::text, 'failed'::text, 'unknown'::text, 'abandoned'::text]))),
    CONSTRAINT decisions_usage_source_check CHECK ((usage_source = ANY (ARRAY['provider'::text, 'estimated'::text, 'unknown'::text]))),
    CONSTRAINT decisions_workspace_key_check CHECK (((length(workspace_key) >= 1) AND (length(workspace_key) <= 200)))
);


--
-- Name: gap_observations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gap_observations (
    workspace_key text NOT NULL,
    trace_id uuid NOT NULL,
    gap_id uuid NOT NULL,
    event_kind text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT gap_observations_event_kind_check CHECK ((event_kind = ANY (ARRAY['retrieval_refusal'::text, 'generation_withheld'::text, 'retrieval_success'::text]))),
    CONSTRAINT gap_observations_workspace_key_check CHECK (((length(workspace_key) >= 1) AND (length(workspace_key) <= 200)))
);


--
-- Name: gap_resolutions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gap_resolutions (
    gap_id uuid NOT NULL,
    revision integer NOT NULL,
    source_ids uuid[] NOT NULL,
    note text NOT NULL,
    resolved_by text NOT NULL,
    resolved_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT gap_resolutions_note_check CHECK ((length(note) <= 1000)),
    CONSTRAINT gap_resolutions_resolved_by_check CHECK (((length(resolved_by) >= 1) AND (length(resolved_by) <= 200))),
    CONSTRAINT gap_resolutions_revision_check CHECK ((revision >= 1)),
    CONSTRAINT gap_resolutions_source_ids_check CHECK ((cardinality(source_ids) > 0))
);


--
-- Name: job_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.job_events (
    event_id bigint NOT NULL,
    job_id uuid NOT NULL,
    event_type text NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT job_events_event_type_check CHECK (((length(event_type) >= 1) AND (length(event_type) <= 80)))
);


--
-- Name: job_events_event_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.job_events_event_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: job_events_event_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.job_events_event_id_seq OWNED BY public.job_events.event_id;


--
-- Name: jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.jobs (
    job_id uuid NOT NULL,
    kind text NOT NULL,
    scope_key text NOT NULL,
    idempotency_key text NOT NULL,
    payload jsonb NOT NULL,
    result jsonb,
    error jsonb,
    status text NOT NULL,
    attempt_count integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 3 NOT NULL,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    lease_owner text,
    lease_token uuid,
    lease_expires_at timestamp with time zone,
    heartbeat_at timestamp with time zone,
    cancel_requested boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    CONSTRAINT jobs_attempt_count_check CHECK ((attempt_count >= 0)),
    CONSTRAINT jobs_check CHECK ((attempt_count <= max_attempts)),
    CONSTRAINT jobs_check1 CHECK (((status = 'running'::text) = (lease_token IS NOT NULL))),
    CONSTRAINT jobs_check2 CHECK (((status = 'running'::text) = (lease_expires_at IS NOT NULL))),
    CONSTRAINT jobs_check3 CHECK (((status = ANY (ARRAY['succeeded'::text, 'failed'::text, 'cancelled'::text])) = (finished_at IS NOT NULL))),
    CONSTRAINT jobs_idempotency_key_check CHECK (((length(idempotency_key) >= 1) AND (length(idempotency_key) <= 200))),
    CONSTRAINT jobs_kind_check CHECK (((length(kind) >= 1) AND (length(kind) <= 100))),
    CONSTRAINT jobs_max_attempts_check CHECK (((max_attempts >= 1) AND (max_attempts <= 20))),
    CONSTRAINT jobs_scope_key_check CHECK (((length(scope_key) >= 1) AND (length(scope_key) <= 200))),
    CONSTRAINT jobs_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'running'::text, 'succeeded'::text, 'failed'::text, 'cancelled'::text])))
);


--
-- Name: locators; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.locators (
    locator_id uuid NOT NULL,
    source_id uuid NOT NULL,
    ordinal integer NOT NULL,
    locator_type text NOT NULL,
    label text NOT NULL,
    start_char integer NOT NULL,
    end_char integer NOT NULL,
    CONSTRAINT locators_check CHECK ((end_char > start_char))
);


--
-- Name: model_calls; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.model_calls (
    model_call_id uuid NOT NULL,
    trace_id uuid,
    provider text NOT NULL,
    model_id text,
    status text NOT NULL,
    finish_reason text,
    request_id text,
    prompt_tokens bigint,
    completion_tokens bigint,
    usage_source text NOT NULL,
    latency_ms integer NOT NULL,
    error_type text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    operation_id uuid,
    sampling_policy text,
    reasoning_effort text,
    requested_output_tokens integer,
    requested_timeout_seconds double precision,
    requested_temperature double precision,
    profile_id uuid,
    profile_revision integer,
    decision_id uuid,
    claim_review_id uuid,
    CONSTRAINT model_calls_profile_revision_check CHECK ((profile_revision > 0)),
    CONSTRAINT model_calls_requested_output_tokens_check CHECK ((requested_output_tokens > 0)),
    CONSTRAINT model_calls_requested_timeout_seconds_check CHECK ((requested_timeout_seconds > (0)::double precision)),
    CONSTRAINT model_calls_sampling_policy_check CHECK ((sampling_policy = ANY (ARRAY['greedy'::text, 'model_default'::text]))),
    CONSTRAINT model_calls_status_check CHECK ((status = ANY (ARRAY['completed'::text, 'failed'::text]))),
    CONSTRAINT model_calls_usage_source_check CHECK ((usage_source = ANY (ARRAY['provider'::text, 'estimated'::text, 'unknown'::text])))
);


--
-- Name: operation_calls; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.operation_calls (
    operation_id uuid NOT NULL,
    attempt_no integer NOT NULL,
    prompt_reserved_tokens integer NOT NULL,
    output_reserved_tokens integer NOT NULL,
    measured_prompt_tokens integer,
    measured_completion_tokens integer,
    status text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    accounted_at timestamp with time zone,
    CONSTRAINT operation_calls_attempt_no_check CHECK ((attempt_no > 0)),
    CONSTRAINT operation_calls_check CHECK (((measured_prompt_tokens IS NULL) = (measured_completion_tokens IS NULL))),
    CONSTRAINT operation_calls_measured_completion_tokens_check CHECK (((measured_completion_tokens IS NULL) OR (measured_completion_tokens >= 0))),
    CONSTRAINT operation_calls_measured_prompt_tokens_check CHECK (((measured_prompt_tokens IS NULL) OR (measured_prompt_tokens >= 0))),
    CONSTRAINT operation_calls_output_reserved_tokens_check CHECK ((output_reserved_tokens > 0)),
    CONSTRAINT operation_calls_prompt_reserved_tokens_check CHECK ((prompt_reserved_tokens >= 0)),
    CONSTRAINT operation_calls_status_check CHECK ((status = ANY (ARRAY['reserved'::text, 'completed'::text, 'failed'::text, 'unknown'::text, 'cancelled'::text])))
);


--
-- Name: operations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.operations (
    operation_id uuid NOT NULL,
    workspace_key text NOT NULL,
    started_at timestamp with time zone NOT NULL,
    deadline_at timestamp with time zone NOT NULL,
    max_calls integer NOT NULL,
    call_count integer DEFAULT 0 NOT NULL,
    total_token_budget integer NOT NULL,
    reserved_tokens integer DEFAULT 0 NOT NULL,
    accounted_tokens integer DEFAULT 0 NOT NULL,
    per_call_output_cap integer NOT NULL,
    per_call_timeout_seconds double precision NOT NULL,
    CONSTRAINT operations_accounted_tokens_check CHECK ((accounted_tokens >= 0)),
    CONSTRAINT operations_call_count_check CHECK ((call_count >= 0)),
    CONSTRAINT operations_check CHECK ((deadline_at > started_at)),
    CONSTRAINT operations_check1 CHECK ((call_count <= max_calls)),
    CONSTRAINT operations_max_calls_check CHECK (((max_calls >= 1) AND (max_calls <= 100))),
    CONSTRAINT operations_per_call_output_cap_check CHECK ((per_call_output_cap > 0)),
    CONSTRAINT operations_per_call_timeout_seconds_check CHECK ((per_call_timeout_seconds > (0)::double precision)),
    CONSTRAINT operations_reserved_tokens_check CHECK ((reserved_tokens >= 0)),
    CONSTRAINT operations_total_token_budget_check CHECK ((total_token_budget > 0)),
    CONSTRAINT operations_workspace_key_check CHECK (((length(workspace_key) >= 1) AND (length(workspace_key) <= 200)))
);


--
-- Name: prereq_mentions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.prereq_mentions (
    course_id uuid NOT NULL,
    kind text NOT NULL,
    target_code text NOT NULL,
    target_course_id uuid,
    "position" integer NOT NULL,
    CONSTRAINT prereq_mentions_kind_check CHECK ((kind = ANY (ARRAY['prerequisite'::text, 'corequisite'::text, 'recommended'::text])))
);


--
-- Name: prereq_nodes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.prereq_nodes (
    node_id uuid NOT NULL,
    course_id uuid NOT NULL,
    kind text NOT NULL,
    parent_node_id uuid,
    "position" integer NOT NULL,
    node_type text NOT NULL,
    target_code text,
    condition_text text,
    CONSTRAINT prereq_nodes_check CHECK (((node_type = 'course'::text) = (target_code IS NOT NULL))),
    CONSTRAINT prereq_nodes_check1 CHECK (((node_type = 'condition'::text) = (condition_text IS NOT NULL))),
    CONSTRAINT prereq_nodes_node_type_check CHECK ((node_type = ANY (ARRAY['all'::text, 'any'::text, 'course'::text, 'condition'::text])))
);


--
-- Name: program_course_mentions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.program_course_mentions (
    program_id uuid NOT NULL,
    "position" integer NOT NULL,
    target_code text NOT NULL,
    target_course_id uuid
);


--
-- Name: program_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.program_items (
    program_id uuid NOT NULL,
    "position" integer NOT NULL,
    section text,
    text text NOT NULL,
    choose integer,
    CONSTRAINT program_items_choose_check CHECK (((choose IS NULL) OR (choose > 0)))
);


--
-- Name: programs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.programs (
    program_id uuid NOT NULL,
    collection_id uuid NOT NULL,
    title text NOT NULL,
    kind text NOT NULL,
    degree text,
    hours text,
    courses_required text,
    department text,
    catalog text NOT NULL,
    source_url text NOT NULL,
    first_page integer NOT NULL,
    last_page integer NOT NULL,
    parser_version text NOT NULL,
    CONSTRAINT programs_kind_check CHECK ((kind = ANY (ARRAY['major'::text, 'minor'::text, 'other'::text])))
);


--
-- Name: provider_batch_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.provider_batch_items (
    batch_id uuid NOT NULL,
    workspace_key text NOT NULL,
    correlation_id text NOT NULL,
    operation_id uuid NOT NULL,
    request_fingerprint character(64) NOT NULL,
    request_json jsonb NOT NULL,
    state text NOT NULL,
    result_json jsonb,
    error_code text,
    usage_source text DEFAULT 'unknown'::text NOT NULL,
    prompt_tokens integer,
    completion_tokens integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    consumer_data jsonb DEFAULT '{}'::jsonb NOT NULL,
    publication_state text DEFAULT 'pending'::text NOT NULL,
    published_artifact_id uuid,
    published_revision_id uuid,
    CONSTRAINT provider_batch_item_consumer_data_size_check CHECK ((octet_length((consumer_data)::text) <= 2000000)),
    CONSTRAINT provider_batch_item_publication_ids_check CHECK (((published_artifact_id IS NULL) = (published_revision_id IS NULL))),
    CONSTRAINT provider_batch_item_publication_state_check CHECK (((publication_state = 'published'::text) = (published_artifact_id IS NOT NULL))),
    CONSTRAINT provider_batch_items_check CHECK (((prompt_tokens IS NULL) = (completion_tokens IS NULL))),
    CONSTRAINT provider_batch_items_completion_tokens_check CHECK (((completion_tokens IS NULL) OR (completion_tokens >= 0))),
    CONSTRAINT provider_batch_items_correlation_id_check CHECK (((length(correlation_id) >= 1) AND (length(correlation_id) <= 256))),
    CONSTRAINT provider_batch_items_prompt_tokens_check CHECK (((prompt_tokens IS NULL) OR (prompt_tokens >= 0))),
    CONSTRAINT provider_batch_items_publication_state_check CHECK ((publication_state = ANY (ARRAY['pending'::text, 'published'::text, 'withheld'::text]))),
    CONSTRAINT provider_batch_items_request_fingerprint_check CHECK ((request_fingerprint ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT provider_batch_items_result_json_check CHECK (((result_json IS NULL) OR (octet_length((result_json)::text) <= 1000000))),
    CONSTRAINT provider_batch_items_state_check CHECK ((state = ANY (ARRAY['reserved'::text, 'received'::text, 'completed'::text, 'failed'::text, 'cancelled'::text, 'unknown'::text]))),
    CONSTRAINT provider_batch_items_usage_source_check CHECK ((usage_source = ANY (ARRAY['provider'::text, 'estimated'::text, 'unknown'::text]))),
    CONSTRAINT provider_batch_items_workspace_key_check CHECK (((length(workspace_key) >= 1) AND (length(workspace_key) <= 200)))
);


--
-- Name: provider_batches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.provider_batches (
    batch_id uuid NOT NULL,
    workspace_key text NOT NULL,
    provider text NOT NULL,
    model_id text NOT NULL,
    profile_id uuid,
    profile_revision integer,
    request_fingerprint character(64) NOT NULL,
    operation_limits jsonb NOT NULL,
    state text NOT NULL,
    provider_batch_id text,
    input_file_id text,
    submission_token uuid,
    submission_lease_until timestamp with time zone,
    cancel_requested boolean DEFAULT false NOT NULL,
    total_count integer,
    completed_count integer,
    failed_count integer,
    failure_code text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    CONSTRAINT provider_batches_check CHECK (((profile_id IS NULL) = (profile_revision IS NULL))),
    CONSTRAINT provider_batches_check1 CHECK (((state = 'submitting'::text) = (submission_token IS NOT NULL))),
    CONSTRAINT provider_batches_check2 CHECK (((state = 'submitting'::text) = (submission_lease_until IS NOT NULL))),
    CONSTRAINT provider_batches_completed_count_check CHECK (((completed_count IS NULL) OR (completed_count >= 0))),
    CONSTRAINT provider_batches_failed_count_check CHECK (((failed_count IS NULL) OR (failed_count >= 0))),
    CONSTRAINT provider_batches_model_id_check CHECK (((length(model_id) >= 1) AND (length(model_id) <= 200))),
    CONSTRAINT provider_batches_profile_revision_check CHECK (((profile_revision IS NULL) OR (profile_revision > 0))),
    CONSTRAINT provider_batches_provider_check CHECK (((length(provider) >= 1) AND (length(provider) <= 80))),
    CONSTRAINT provider_batches_request_fingerprint_check CHECK ((request_fingerprint ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT provider_batches_state_check CHECK ((state = ANY (ARRAY['prepared'::text, 'submitting'::text, 'pending'::text, 'validating'::text, 'in_progress'::text, 'finalizing'::text, 'completed'::text, 'failed'::text, 'expired'::text, 'cancel_requested'::text, 'cancelling'::text, 'cancelled'::text, 'unknown'::text]))),
    CONSTRAINT provider_batches_total_count_check CHECK (((total_count IS NULL) OR (total_count >= 0))),
    CONSTRAINT provider_batches_workspace_key_check CHECK (((length(workspace_key) >= 1) AND (length(workspace_key) <= 200)))
);


--
-- Name: responses; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.responses (
    response_id uuid NOT NULL,
    trace_id uuid NOT NULL,
    status text NOT NULL,
    text text NOT NULL,
    reason text,
    prompt_version text NOT NULL,
    context_snapshot jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT responses_status_check CHECK ((status = ANY (ARRAY['answer'::text, 'refusal'::text, 'failed'::text])))
);


--
-- Name: retrieval_gaps; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.retrieval_gaps (
    gap_id uuid NOT NULL,
    workspace_key text NOT NULL,
    collection_id uuid NOT NULL,
    query text NOT NULL,
    normalized_query text NOT NULL,
    query_key character(64) NOT NULL,
    scope_source_ids uuid[],
    state text DEFAULT 'open'::text NOT NULL,
    revision integer DEFAULT 1 NOT NULL,
    occurrence_count integer DEFAULT 0 NOT NULL,
    recurrence_count integer DEFAULT 0 NOT NULL,
    successful_recurrence_count integer DEFAULT 0 NOT NULL,
    first_seen_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    last_seen_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    resolution_source_ids uuid[],
    resolution_note text,
    resolved_by text,
    resolved_at timestamp with time zone,
    CONSTRAINT retrieval_gaps_check CHECK ((((state = 'open'::text) AND (resolution_source_ids IS NULL) AND (resolution_note IS NULL) AND (resolved_by IS NULL) AND (resolved_at IS NULL)) OR ((state = 'resolved'::text) AND (resolution_source_ids IS NOT NULL) AND (cardinality(resolution_source_ids) > 0) AND (resolved_by IS NOT NULL) AND (resolved_at IS NOT NULL)))),
    CONSTRAINT retrieval_gaps_normalized_query_check CHECK (((length(normalized_query) >= 1) AND (length(normalized_query) <= 20000))),
    CONSTRAINT retrieval_gaps_occurrence_count_check CHECK ((occurrence_count >= 0)),
    CONSTRAINT retrieval_gaps_query_check CHECK (((length(query) >= 1) AND (length(query) <= 20000))),
    CONSTRAINT retrieval_gaps_query_key_check CHECK ((query_key ~ '^[a-f0-9]{64}$'::text)),
    CONSTRAINT retrieval_gaps_recurrence_count_check CHECK ((recurrence_count >= 0)),
    CONSTRAINT retrieval_gaps_resolution_note_check CHECK (((resolution_note IS NULL) OR (length(resolution_note) <= 1000))),
    CONSTRAINT retrieval_gaps_resolved_by_check CHECK (((resolved_by IS NULL) OR ((length(resolved_by) >= 1) AND (length(resolved_by) <= 200)))),
    CONSTRAINT retrieval_gaps_revision_check CHECK ((revision >= 1)),
    CONSTRAINT retrieval_gaps_state_check CHECK ((state = ANY (ARRAY['open'::text, 'resolved'::text]))),
    CONSTRAINT retrieval_gaps_successful_recurrence_count_check CHECK ((successful_recurrence_count >= 0)),
    CONSTRAINT retrieval_gaps_workspace_key_check CHECK (((length(workspace_key) >= 1) AND (length(workspace_key) <= 200)))
);


--
-- Name: retrieval_traces; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.retrieval_traces (
    trace_id uuid NOT NULL,
    collection_id uuid NOT NULL,
    query text NOT NULL,
    normalized_query text NOT NULL,
    config_version integer NOT NULL,
    embed_model text,
    refused boolean NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: sim_builds; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sim_builds (
    build_id uuid NOT NULL,
    collection_id uuid NOT NULL,
    model text NOT NULL,
    config_version integer NOT NULL,
    is_current boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    nudges_hash text
);


--
-- Name: sim_cluster_members; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sim_cluster_members (
    cluster_id uuid NOT NULL,
    course_id uuid NOT NULL
);


--
-- Name: sim_clusters; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sim_clusters (
    cluster_id uuid NOT NULL,
    build_id uuid NOT NULL,
    parent_cluster_id uuid,
    level integer NOT NULL,
    label text NOT NULL,
    medoid_course_id uuid NOT NULL,
    centroid public.vector NOT NULL,
    size integer NOT NULL
);


--
-- Name: sim_courses; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sim_courses (
    build_id uuid NOT NULL,
    course_id uuid NOT NULL,
    embedding public.vector NOT NULL
);


--
-- Name: sim_edges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sim_edges (
    build_id uuid NOT NULL,
    course_a uuid NOT NULL,
    course_b uuid NOT NULL,
    weight double precision NOT NULL,
    CONSTRAINT sim_edges_check CHECK ((course_a < course_b))
);


--
-- Name: sources; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sources (
    source_id uuid NOT NULL,
    collection_id uuid NOT NULL,
    filename text NOT NULL,
    content_type text NOT NULL,
    file_hash text NOT NULL,
    raw_path text NOT NULL,
    status text NOT NULL,
    error_message text,
    chunker_version text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    warnings jsonb DEFAULT '[]'::jsonb NOT NULL,
    workspace_key text,
    index_revision bigint DEFAULT 0 NOT NULL,
    is_current boolean DEFAULT true NOT NULL,
    origin_key text,
    CONSTRAINT sources_index_revision_check CHECK ((index_revision >= 0)),
    CONSTRAINT sources_status_check CHECK ((status = ANY (ARRAY['uploaded'::text, 'parsed'::text, 'chunked'::text, 'indexed'::text, 'failed'::text]))),
    CONSTRAINT sources_workspace_key_check CHECK (((workspace_key IS NULL) OR ((length(workspace_key) >= 1) AND (length(workspace_key) <= 200))))
);


--
-- Name: tool_execution_operations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tool_execution_operations (
    operation_id uuid NOT NULL,
    workspace_key text NOT NULL,
    scope_fingerprint character(64) NOT NULL,
    max_tool_calls integer NOT NULL,
    claimed_calls integer DEFAULT 0 NOT NULL,
    CONSTRAINT tool_execution_operations_check CHECK ((claimed_calls <= max_tool_calls)),
    CONSTRAINT tool_execution_operations_claimed_calls_check CHECK ((claimed_calls >= 0)),
    CONSTRAINT tool_execution_operations_max_tool_calls_check CHECK (((max_tool_calls >= 0) AND (max_tool_calls <= 100))),
    CONSTRAINT tool_execution_operations_scope_fingerprint_check CHECK ((scope_fingerprint ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT tool_execution_operations_workspace_key_check CHECK (((length(workspace_key) >= 1) AND (length(workspace_key) <= 200)))
);


--
-- Name: tool_executions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tool_executions (
    operation_id uuid NOT NULL,
    workspace_key text NOT NULL,
    call_id text NOT NULL,
    tool_id text NOT NULL,
    arguments_sha256 character(64) NOT NULL,
    status text NOT NULL,
    result_json text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    scope_fingerprint character(64) DEFAULT '0000000000000000000000000000000000000000000000000000000000000000'::bpchar NOT NULL,
    CONSTRAINT tool_executions_arguments_sha256_check CHECK ((arguments_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT tool_executions_call_id_check CHECK (((length(call_id) >= 1) AND (length(call_id) <= 256))),
    CONSTRAINT tool_executions_check CHECK (((status = 'completed'::text) = (result_json IS NOT NULL))),
    CONSTRAINT tool_executions_check1 CHECK (((status = 'completed'::text) = (completed_at IS NOT NULL))),
    CONSTRAINT tool_executions_result_json_check CHECK (((result_json IS NULL) OR (octet_length(result_json) <= 64000))),
    CONSTRAINT tool_executions_scope_fingerprint_check CHECK ((scope_fingerprint ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT tool_executions_status_check CHECK ((status = ANY (ARRAY['executing'::text, 'completed'::text, 'unknown'::text]))),
    CONSTRAINT tool_executions_tool_id_check CHECK (((length(tool_id) >= 1) AND (length(tool_id) <= 128))),
    CONSTRAINT tool_executions_workspace_key_check CHECK (((length(workspace_key) >= 1) AND (length(workspace_key) <= 200)))
);


--
-- Name: trace_contributions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trace_contributions (
    trace_id uuid NOT NULL,
    chunk_id uuid NOT NULL,
    seams text[] NOT NULL,
    normalized_score double precision NOT NULL,
    source_slot integer NOT NULL,
    rank integer NOT NULL
);


--
-- Name: trace_seams; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trace_seams (
    trace_id uuid NOT NULL,
    seam text NOT NULL,
    enabled boolean NOT NULL,
    state text NOT NULL,
    reason text,
    candidate_count integer NOT NULL
);


--
-- Name: job_events event_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.job_events ALTER COLUMN event_id SET DEFAULT nextval('public.job_events_event_id_seq'::regclass);


--
-- Name: artifact_revisions artifact_revisions_artifact_id_draft_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_revisions
    ADD CONSTRAINT artifact_revisions_artifact_id_draft_id_key UNIQUE (artifact_id, draft_id);


--
-- Name: artifact_revisions artifact_revisions_artifact_id_revision_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_revisions
    ADD CONSTRAINT artifact_revisions_artifact_id_revision_id_key UNIQUE (artifact_id, revision_id);


--
-- Name: artifact_revisions artifact_revisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_revisions
    ADD CONSTRAINT artifact_revisions_pkey PRIMARY KEY (revision_id);


--
-- Name: artifact_run_plan_excerpts artifact_run_plan_excerpts_job_id_chunk_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_run_plan_excerpts
    ADD CONSTRAINT artifact_run_plan_excerpts_job_id_chunk_id_key UNIQUE (job_id, chunk_id);


--
-- Name: artifact_run_plan_excerpts artifact_run_plan_excerpts_locator_id_present; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.artifact_run_plan_excerpts
    ADD CONSTRAINT artifact_run_plan_excerpts_locator_id_present CHECK ((locator_id IS NOT NULL)) NOT VALID;


--
-- Name: artifact_run_plan_excerpts artifact_run_plan_excerpts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_run_plan_excerpts
    ADD CONSTRAINT artifact_run_plan_excerpts_pkey PRIMARY KEY (job_id, excerpt_id);


--
-- Name: artifact_run_plan_sources artifact_run_plan_sources_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_run_plan_sources
    ADD CONSTRAINT artifact_run_plan_sources_pkey PRIMARY KEY (job_id, source_id);


--
-- Name: artifact_run_plans artifact_run_plans_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_run_plans
    ADD CONSTRAINT artifact_run_plans_pkey PRIMARY KEY (job_id);


--
-- Name: artifact_run_unit_attempts artifact_run_unit_attempts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_run_unit_attempts
    ADD CONSTRAINT artifact_run_unit_attempts_pkey PRIMARY KEY (job_id, unit_id, attempt_no);


--
-- Name: artifact_run_unit_calls artifact_run_unit_calls_job_id_attempt_no_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_run_unit_calls
    ADD CONSTRAINT artifact_run_unit_calls_job_id_attempt_no_key UNIQUE (job_id, attempt_no);


--
-- Name: artifact_run_unit_calls artifact_run_unit_calls_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_run_unit_calls
    ADD CONSTRAINT artifact_run_unit_calls_pkey PRIMARY KEY (job_id, unit_id, attempt_no);


--
-- Name: artifact_run_unit_dependencies artifact_run_unit_dependencies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_run_unit_dependencies
    ADD CONSTRAINT artifact_run_unit_dependencies_pkey PRIMARY KEY (job_id, unit_id, depends_on_unit_id);


--
-- Name: artifact_run_unit_sources artifact_run_unit_sources_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_run_unit_sources
    ADD CONSTRAINT artifact_run_unit_sources_pkey PRIMARY KEY (job_id, unit_id, source_id);


--
-- Name: artifact_run_units artifact_run_units_job_id_ordinal_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_run_units
    ADD CONSTRAINT artifact_run_units_job_id_ordinal_key UNIQUE (job_id, ordinal);


--
-- Name: artifact_run_units artifact_run_units_job_id_unit_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_run_units
    ADD CONSTRAINT artifact_run_units_job_id_unit_key_key UNIQUE (job_id, unit_key);


--
-- Name: artifact_run_units artifact_run_units_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_run_units
    ADD CONSTRAINT artifact_run_units_pkey PRIMARY KEY (job_id, unit_id);


--
-- Name: artifacts artifacts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifacts
    ADD CONSTRAINT artifacts_pkey PRIMARY KEY (artifact_id);


--
-- Name: auth_login_transactions auth_login_transactions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auth_login_transactions
    ADD CONSTRAINT auth_login_transactions_pkey PRIMARY KEY (state_hash);


--
-- Name: auth_sessions auth_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auth_sessions
    ADD CONSTRAINT auth_sessions_pkey PRIMARY KEY (session_hash);


--
-- Name: chunk_embeddings chunk_embeddings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chunk_embeddings
    ADD CONSTRAINT chunk_embeddings_pkey PRIMARY KEY (chunk_id, model);


--
-- Name: chunks chunks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chunks
    ADD CONSTRAINT chunks_pkey PRIMARY KEY (chunk_id);


--
-- Name: chunks chunks_source_id_chunk_index_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chunks
    ADD CONSTRAINT chunks_source_id_chunk_index_key UNIQUE (source_id, chunk_index);


--
-- Name: citations citations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.citations
    ADD CONSTRAINT citations_pkey PRIMARY KEY (response_id, ordinal);


--
-- Name: claim_review_units claim_review_units_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claim_review_units
    ADD CONSTRAINT claim_review_units_pkey PRIMARY KEY (review_id, ordinal);


--
-- Name: claim_reviews claim_reviews_operation_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claim_reviews
    ADD CONSTRAINT claim_reviews_operation_id_key UNIQUE (operation_id);


--
-- Name: claim_reviews claim_reviews_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claim_reviews
    ADD CONSTRAINT claim_reviews_pkey PRIMARY KEY (review_id);


--
-- Name: claims claims_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claims
    ADD CONSTRAINT claims_pkey PRIMARY KEY (claim_id);


--
-- Name: claims claims_response_id_ordinal_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claims
    ADD CONSTRAINT claims_response_id_ordinal_key UNIQUE (response_id, ordinal);


--
-- Name: collections collections_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collections
    ADD CONSTRAINT collections_name_key UNIQUE (name);


--
-- Name: collections collections_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collections
    ADD CONSTRAINT collections_pkey PRIMARY KEY (collection_id);


--
-- Name: connection_profile_evaluations connection_profile_evaluations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.connection_profile_evaluations
    ADD CONSTRAINT connection_profile_evaluations_pkey PRIMARY KEY (evaluation_id);


--
-- Name: connection_profiles connection_profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.connection_profiles
    ADD CONSTRAINT connection_profiles_pkey PRIMARY KEY (profile_id);


--
-- Name: conversation_requests conversation_requests_job_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_requests
    ADD CONSTRAINT conversation_requests_job_id_key UNIQUE (job_id);


--
-- Name: conversation_requests conversation_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_requests
    ADD CONSTRAINT conversation_requests_pkey PRIMARY KEY (conversation_id, request_id);


--
-- Name: conversation_summaries conversation_summaries_operation_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_summaries
    ADD CONSTRAINT conversation_summaries_operation_id_key UNIQUE (operation_id);


--
-- Name: conversation_summaries conversation_summaries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_summaries
    ADD CONSTRAINT conversation_summaries_pkey PRIMARY KEY (summary_id);


--
-- Name: conversation_turns conversation_turns_conversation_id_request_id_role_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_turns
    ADD CONSTRAINT conversation_turns_conversation_id_request_id_role_key UNIQUE (conversation_id, request_id, role);


--
-- Name: conversation_turns conversation_turns_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_turns
    ADD CONSTRAINT conversation_turns_pkey PRIMARY KEY (conversation_id, sequence);


--
-- Name: conversations conversations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT conversations_pkey PRIMARY KEY (conversation_id);


--
-- Name: courses courses_collection_id_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.courses
    ADD CONSTRAINT courses_collection_id_code_key UNIQUE (collection_id, code);


--
-- Name: courses courses_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.courses
    ADD CONSTRAINT courses_pkey PRIMARY KEY (course_id);


--
-- Name: decisions decisions_operation_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decisions
    ADD CONSTRAINT decisions_operation_id_key UNIQUE (operation_id);


--
-- Name: decisions decisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decisions
    ADD CONSTRAINT decisions_pkey PRIMARY KEY (decision_id);


--
-- Name: gap_observations gap_observations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gap_observations
    ADD CONSTRAINT gap_observations_pkey PRIMARY KEY (workspace_key, trace_id, event_kind);


--
-- Name: gap_resolutions gap_resolutions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gap_resolutions
    ADD CONSTRAINT gap_resolutions_pkey PRIMARY KEY (gap_id, revision);


--
-- Name: job_events job_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.job_events
    ADD CONSTRAINT job_events_pkey PRIMARY KEY (event_id);


--
-- Name: jobs jobs_lease_token_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.jobs
    ADD CONSTRAINT jobs_lease_token_key UNIQUE (lease_token);


--
-- Name: jobs jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.jobs
    ADD CONSTRAINT jobs_pkey PRIMARY KEY (job_id);


--
-- Name: jobs jobs_scope_key_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.jobs
    ADD CONSTRAINT jobs_scope_key_idempotency_key_key UNIQUE (scope_key, idempotency_key);


--
-- Name: locators locators_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.locators
    ADD CONSTRAINT locators_pkey PRIMARY KEY (locator_id);


--
-- Name: locators locators_source_id_ordinal_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.locators
    ADD CONSTRAINT locators_source_id_ordinal_key UNIQUE (source_id, ordinal);


--
-- Name: model_calls model_calls_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_calls
    ADD CONSTRAINT model_calls_pkey PRIMARY KEY (model_call_id);


--
-- Name: operation_calls operation_calls_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operation_calls
    ADD CONSTRAINT operation_calls_pkey PRIMARY KEY (operation_id, attempt_no);


--
-- Name: operations operations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operations
    ADD CONSTRAINT operations_pkey PRIMARY KEY (operation_id);


--
-- Name: prereq_mentions prereq_mentions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.prereq_mentions
    ADD CONSTRAINT prereq_mentions_pkey PRIMARY KEY (course_id, kind, target_code);


--
-- Name: prereq_nodes prereq_nodes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.prereq_nodes
    ADD CONSTRAINT prereq_nodes_pkey PRIMARY KEY (node_id);


--
-- Name: program_course_mentions program_course_mentions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.program_course_mentions
    ADD CONSTRAINT program_course_mentions_pkey PRIMARY KEY (program_id, "position", target_code);


--
-- Name: program_items program_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.program_items
    ADD CONSTRAINT program_items_pkey PRIMARY KEY (program_id, "position");


--
-- Name: programs programs_collection_id_title_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.programs
    ADD CONSTRAINT programs_collection_id_title_key UNIQUE (collection_id, title);


--
-- Name: programs programs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.programs
    ADD CONSTRAINT programs_pkey PRIMARY KEY (program_id);


--
-- Name: provider_batch_items provider_batch_items_operation_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.provider_batch_items
    ADD CONSTRAINT provider_batch_items_operation_id_key UNIQUE (operation_id);


--
-- Name: provider_batch_items provider_batch_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.provider_batch_items
    ADD CONSTRAINT provider_batch_items_pkey PRIMARY KEY (batch_id, correlation_id);


--
-- Name: provider_batches provider_batches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.provider_batches
    ADD CONSTRAINT provider_batches_pkey PRIMARY KEY (batch_id);


--
-- Name: responses responses_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.responses
    ADD CONSTRAINT responses_pkey PRIMARY KEY (response_id);


--
-- Name: retrieval_gaps retrieval_gaps_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.retrieval_gaps
    ADD CONSTRAINT retrieval_gaps_pkey PRIMARY KEY (gap_id);


--
-- Name: retrieval_traces retrieval_traces_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.retrieval_traces
    ADD CONSTRAINT retrieval_traces_pkey PRIMARY KEY (trace_id);


--
-- Name: sim_builds sim_builds_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sim_builds
    ADD CONSTRAINT sim_builds_pkey PRIMARY KEY (build_id);


--
-- Name: sim_cluster_members sim_cluster_members_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sim_cluster_members
    ADD CONSTRAINT sim_cluster_members_pkey PRIMARY KEY (cluster_id, course_id);


--
-- Name: sim_clusters sim_clusters_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sim_clusters
    ADD CONSTRAINT sim_clusters_pkey PRIMARY KEY (cluster_id);


--
-- Name: sim_courses sim_courses_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sim_courses
    ADD CONSTRAINT sim_courses_pkey PRIMARY KEY (build_id, course_id);


--
-- Name: sim_edges sim_edges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sim_edges
    ADD CONSTRAINT sim_edges_pkey PRIMARY KEY (build_id, course_a, course_b);


--
-- Name: sources sources_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sources
    ADD CONSTRAINT sources_pkey PRIMARY KEY (source_id);


--
-- Name: tool_execution_operations tool_execution_operations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tool_execution_operations
    ADD CONSTRAINT tool_execution_operations_pkey PRIMARY KEY (operation_id);


--
-- Name: tool_executions tool_executions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tool_executions
    ADD CONSTRAINT tool_executions_pkey PRIMARY KEY (operation_id, call_id);


--
-- Name: trace_contributions trace_contributions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trace_contributions
    ADD CONSTRAINT trace_contributions_pkey PRIMARY KEY (trace_id, chunk_id);


--
-- Name: trace_seams trace_seams_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trace_seams
    ADD CONSTRAINT trace_seams_pkey PRIMARY KEY (trace_id, seam);


--
-- Name: artifact_run_plans_workspace_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX artifact_run_plans_workspace_created_idx ON public.artifact_run_plans USING btree (workspace_key, created_at DESC);


--
-- Name: artifact_run_unit_attempts_state_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX artifact_run_unit_attempts_state_idx ON public.artifact_run_unit_attempts USING btree (job_id, unit_id, outcome, attempt_no);


--
-- Name: artifact_run_units_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX artifact_run_units_status_idx ON public.artifact_run_units USING btree (job_id, status, ordinal);


--
-- Name: artifacts_workspace_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX artifacts_workspace_idx ON public.artifacts USING btree (workspace_key, created_at);


--
-- Name: auth_login_transactions_expiry_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX auth_login_transactions_expiry_idx ON public.auth_login_transactions USING btree (expires_at);


--
-- Name: auth_sessions_expiry_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX auth_sessions_expiry_idx ON public.auth_sessions USING btree (expires_at) WHERE (revoked_at IS NULL);


--
-- Name: auth_sessions_workspace_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX auth_sessions_workspace_idx ON public.auth_sessions USING btree (workspace_key);


--
-- Name: chunk_embeddings_model_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX chunk_embeddings_model_idx ON public.chunk_embeddings USING btree (model, dim);


--
-- Name: chunks_locator_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX chunks_locator_idx ON public.chunks USING btree (locator_id);


--
-- Name: chunks_search_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX chunks_search_idx ON public.chunks USING gin (search_vector);


--
-- Name: claim_reviews_workspace_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX claim_reviews_workspace_created_idx ON public.claim_reviews USING btree (workspace_key, created_at DESC);


--
-- Name: connection_profile_eval_current_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX connection_profile_eval_current_idx ON public.connection_profile_evaluations USING btree (profile_id, profile_revision, suite_hash, recorded_at DESC);


--
-- Name: connection_profiles_name_lower_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX connection_profiles_name_lower_idx ON public.connection_profiles USING btree (lower(name));


--
-- Name: connection_profiles_routes_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX connection_profiles_routes_idx ON public.connection_profiles USING btree (enabled, priority, profile_id);


--
-- Name: conversation_requests_job_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX conversation_requests_job_idx ON public.conversation_requests USING btree (job_id) WHERE (job_id IS NOT NULL);


--
-- Name: conversation_summaries_latest_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX conversation_summaries_latest_idx ON public.conversation_summaries USING btree (conversation_id, covered_through DESC) WHERE (state = 'completed'::text);


--
-- Name: conversation_summaries_workspace_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX conversation_summaries_workspace_idx ON public.conversation_summaries USING btree (workspace_key, conversation_id, created_at DESC);


--
-- Name: conversations_workspace_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX conversations_workspace_idx ON public.conversations USING btree (workspace_key, updated_at DESC);


--
-- Name: decisions_workspace_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX decisions_workspace_created_idx ON public.decisions USING btree (workspace_key, created_at DESC);


--
-- Name: gap_observations_gap_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX gap_observations_gap_idx ON public.gap_observations USING btree (gap_id, created_at);


--
-- Name: job_events_order_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX job_events_order_idx ON public.job_events USING btree (job_id, event_id);


--
-- Name: jobs_expired_lease_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX jobs_expired_lease_idx ON public.jobs USING btree (lease_expires_at, job_id) WHERE (status = 'running'::text);


--
-- Name: jobs_ready_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX jobs_ready_idx ON public.jobs USING btree (available_at, created_at, job_id) WHERE (status = 'queued'::text);


--
-- Name: model_calls_claim_review_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX model_calls_claim_review_idx ON public.model_calls USING btree (claim_review_id) WHERE (claim_review_id IS NOT NULL);


--
-- Name: model_calls_decision_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX model_calls_decision_idx ON public.model_calls USING btree (decision_id) WHERE (decision_id IS NOT NULL);


--
-- Name: model_calls_operation_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX model_calls_operation_idx ON public.model_calls USING btree (operation_id) WHERE (operation_id IS NOT NULL);


--
-- Name: model_calls_trace_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX model_calls_trace_idx ON public.model_calls USING btree (trace_id);


--
-- Name: operation_calls_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX operation_calls_status_idx ON public.operation_calls USING btree (operation_id, status, attempt_no);


--
-- Name: operations_workspace_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX operations_workspace_idx ON public.operations USING btree (workspace_key, started_at);


--
-- Name: prereq_mentions_target_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX prereq_mentions_target_idx ON public.prereq_mentions USING btree (target_course_id);


--
-- Name: program_course_mentions_code_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX program_course_mentions_code_idx ON public.program_course_mentions USING btree (target_code);


--
-- Name: program_course_mentions_course_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX program_course_mentions_course_idx ON public.program_course_mentions USING btree (target_course_id);


--
-- Name: provider_batch_items_operation_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX provider_batch_items_operation_idx ON public.provider_batch_items USING btree (operation_id);


--
-- Name: provider_batches_remote_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX provider_batches_remote_id_idx ON public.provider_batches USING btree (provider, provider_batch_id) WHERE (provider_batch_id IS NOT NULL);


--
-- Name: provider_batches_workspace_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX provider_batches_workspace_idx ON public.provider_batches USING btree (workspace_key, created_at DESC);


--
-- Name: responses_trace_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX responses_trace_idx ON public.responses USING btree (trace_id);


--
-- Name: retrieval_gaps_identity_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX retrieval_gaps_identity_uq ON public.retrieval_gaps USING btree (workspace_key, collection_id, query_key);


--
-- Name: retrieval_gaps_workspace_oldest_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX retrieval_gaps_workspace_oldest_idx ON public.retrieval_gaps USING btree (workspace_key, state, first_seen_at, gap_id);


--
-- Name: sim_builds_current; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX sim_builds_current ON public.sim_builds USING btree (collection_id) WHERE is_current;


--
-- Name: sim_clusters_build; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX sim_clusters_build ON public.sim_clusters USING btree (build_id);


--
-- Name: sim_clusters_parent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX sim_clusters_parent ON public.sim_clusters USING btree (parent_cluster_id);


--
-- Name: sources_current_origin_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX sources_current_origin_uq ON public.sources USING btree (collection_id, COALESCE(workspace_key, ''::text), origin_key) WHERE (is_current AND (origin_key IS NOT NULL));


--
-- Name: sources_workspace_hash_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX sources_workspace_hash_uq ON public.sources USING btree (collection_id, COALESCE(workspace_key, ''::text), file_hash);


--
-- Name: sources_workspace_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX sources_workspace_idx ON public.sources USING btree (workspace_key, collection_id, status);


--
-- Name: tool_executions_workspace_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX tool_executions_workspace_idx ON public.tool_executions USING btree (workspace_key, operation_id);


--
-- Name: artifact_run_unit_attempts artifact_run_attempt_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER artifact_run_attempt_immutable BEFORE UPDATE ON public.artifact_run_unit_attempts FOR EACH ROW EXECUTE FUNCTION public.protect_artifact_run_attempt();


--
-- Name: artifact_run_plan_excerpts artifact_run_excerpt_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER artifact_run_excerpt_immutable BEFORE UPDATE ON public.artifact_run_plan_excerpts FOR EACH ROW EXECUTE FUNCTION public.protect_artifact_run_excerpt();


--
-- Name: artifact_run_plans artifact_run_plan_snapshot_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER artifact_run_plan_snapshot_immutable BEFORE UPDATE ON public.artifact_run_plans FOR EACH ROW EXECUTE FUNCTION public.protect_artifact_run_plan_snapshot();


--
-- Name: artifact_run_units artifact_run_unit_snapshot_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER artifact_run_unit_snapshot_immutable BEFORE UPDATE ON public.artifact_run_units FOR EACH ROW EXECUTE FUNCTION public.protect_artifact_run_unit_snapshot();


--
-- Name: artifact_revisions artifact_revisions_artifact_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_revisions
    ADD CONSTRAINT artifact_revisions_artifact_id_fkey FOREIGN KEY (artifact_id) REFERENCES public.artifacts(artifact_id) ON DELETE CASCADE;


--
-- Name: artifact_revisions artifact_revisions_artifact_id_parent_revision_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_revisions
    ADD CONSTRAINT artifact_revisions_artifact_id_parent_revision_id_fkey FOREIGN KEY (artifact_id, parent_revision_id) REFERENCES public.artifact_revisions(artifact_id, revision_id);


--
-- Name: artifact_run_plan_excerpts artifact_run_plan_excerpts_job_id_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_run_plan_excerpts
    ADD CONSTRAINT artifact_run_plan_excerpts_job_id_source_id_fkey FOREIGN KEY (job_id, source_id) REFERENCES public.artifact_run_plan_sources(job_id, source_id) ON DELETE CASCADE;


--
-- Name: artifact_run_plan_sources artifact_run_plan_sources_collection_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_run_plan_sources
    ADD CONSTRAINT artifact_run_plan_sources_collection_id_fkey FOREIGN KEY (collection_id) REFERENCES public.collections(collection_id) ON DELETE RESTRICT;


--
-- Name: artifact_run_plan_sources artifact_run_plan_sources_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_run_plan_sources
    ADD CONSTRAINT artifact_run_plan_sources_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.artifact_run_plans(job_id) ON DELETE CASCADE;


--
-- Name: artifact_run_plan_sources artifact_run_plan_sources_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_run_plan_sources
    ADD CONSTRAINT artifact_run_plan_sources_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.sources(source_id) ON DELETE RESTRICT;


--
-- Name: artifact_run_plans artifact_run_plans_collection_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_run_plans
    ADD CONSTRAINT artifact_run_plans_collection_id_fkey FOREIGN KEY (collection_id) REFERENCES public.collections(collection_id) ON DELETE RESTRICT;


--
-- Name: artifact_run_plans artifact_run_plans_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_run_plans
    ADD CONSTRAINT artifact_run_plans_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.jobs(job_id) ON DELETE CASCADE;


--
-- Name: artifact_run_plans artifact_run_plans_profile_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_run_plans
    ADD CONSTRAINT artifact_run_plans_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES public.connection_profiles(profile_id) ON DELETE RESTRICT;


--
-- Name: artifact_run_unit_attempts artifact_run_unit_attempts_job_id_unit_id_attempt_no_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_run_unit_attempts
    ADD CONSTRAINT artifact_run_unit_attempts_job_id_unit_id_attempt_no_fkey FOREIGN KEY (job_id, unit_id, attempt_no) REFERENCES public.artifact_run_unit_calls(job_id, unit_id, attempt_no) ON DELETE CASCADE;


--
-- Name: artifact_run_unit_calls artifact_run_unit_calls_job_id_attempt_no_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_run_unit_calls
    ADD CONSTRAINT artifact_run_unit_calls_job_id_attempt_no_fkey FOREIGN KEY (job_id, attempt_no) REFERENCES public.operation_calls(operation_id, attempt_no) ON DELETE RESTRICT;


--
-- Name: artifact_run_unit_calls artifact_run_unit_calls_job_id_unit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_run_unit_calls
    ADD CONSTRAINT artifact_run_unit_calls_job_id_unit_id_fkey FOREIGN KEY (job_id, unit_id) REFERENCES public.artifact_run_units(job_id, unit_id) ON DELETE CASCADE;


--
-- Name: artifact_run_unit_dependencies artifact_run_unit_dependencies_job_id_depends_on_unit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_run_unit_dependencies
    ADD CONSTRAINT artifact_run_unit_dependencies_job_id_depends_on_unit_id_fkey FOREIGN KEY (job_id, depends_on_unit_id) REFERENCES public.artifact_run_units(job_id, unit_id) ON DELETE CASCADE;


--
-- Name: artifact_run_unit_dependencies artifact_run_unit_dependencies_job_id_unit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_run_unit_dependencies
    ADD CONSTRAINT artifact_run_unit_dependencies_job_id_unit_id_fkey FOREIGN KEY (job_id, unit_id) REFERENCES public.artifact_run_units(job_id, unit_id) ON DELETE CASCADE;


--
-- Name: artifact_run_unit_sources artifact_run_unit_sources_job_id_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_run_unit_sources
    ADD CONSTRAINT artifact_run_unit_sources_job_id_source_id_fkey FOREIGN KEY (job_id, source_id) REFERENCES public.artifact_run_plan_sources(job_id, source_id) ON DELETE RESTRICT;


--
-- Name: artifact_run_unit_sources artifact_run_unit_sources_job_id_unit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_run_unit_sources
    ADD CONSTRAINT artifact_run_unit_sources_job_id_unit_id_fkey FOREIGN KEY (job_id, unit_id) REFERENCES public.artifact_run_units(job_id, unit_id) ON DELETE CASCADE;


--
-- Name: artifact_run_units artifact_run_units_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_run_units
    ADD CONSTRAINT artifact_run_units_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.artifact_run_plans(job_id) ON DELETE CASCADE;


--
-- Name: artifact_run_units artifact_run_units_profile_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_run_units
    ADD CONSTRAINT artifact_run_units_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES public.connection_profiles(profile_id) ON DELETE RESTRICT;


--
-- Name: artifacts artifacts_head_revision_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifacts
    ADD CONSTRAINT artifacts_head_revision_fk FOREIGN KEY (artifact_id, head_revision_id) REFERENCES public.artifact_revisions(artifact_id, revision_id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: chunk_embeddings chunk_embeddings_chunk_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chunk_embeddings
    ADD CONSTRAINT chunk_embeddings_chunk_id_fkey FOREIGN KEY (chunk_id) REFERENCES public.chunks(chunk_id) ON DELETE CASCADE;


--
-- Name: chunks chunks_locator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chunks
    ADD CONSTRAINT chunks_locator_id_fkey FOREIGN KEY (locator_id) REFERENCES public.locators(locator_id) ON DELETE CASCADE;


--
-- Name: chunks chunks_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chunks
    ADD CONSTRAINT chunks_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.sources(source_id) ON DELETE CASCADE;


--
-- Name: citations citations_response_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.citations
    ADD CONSTRAINT citations_response_id_fkey FOREIGN KEY (response_id) REFERENCES public.responses(response_id) ON DELETE CASCADE;


--
-- Name: claim_review_units claim_review_units_review_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claim_review_units
    ADD CONSTRAINT claim_review_units_review_id_fkey FOREIGN KEY (review_id) REFERENCES public.claim_reviews(review_id) ON DELETE CASCADE;


--
-- Name: claim_reviews claim_reviews_operation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claim_reviews
    ADD CONSTRAINT claim_reviews_operation_id_fkey FOREIGN KEY (operation_id) REFERENCES public.operations(operation_id);


--
-- Name: claim_reviews claim_reviews_profile_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claim_reviews
    ADD CONSTRAINT claim_reviews_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES public.connection_profiles(profile_id) ON DELETE RESTRICT;


--
-- Name: claim_reviews claim_reviews_response_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claim_reviews
    ADD CONSTRAINT claim_reviews_response_id_fkey FOREIGN KEY (response_id) REFERENCES public.responses(response_id) ON DELETE CASCADE;


--
-- Name: claims claims_response_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claims
    ADD CONSTRAINT claims_response_id_fkey FOREIGN KEY (response_id) REFERENCES public.responses(response_id) ON DELETE CASCADE;


--
-- Name: connection_profile_evaluations connection_profile_evaluations_profile_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.connection_profile_evaluations
    ADD CONSTRAINT connection_profile_evaluations_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES public.connection_profiles(profile_id) ON DELETE CASCADE;


--
-- Name: conversation_requests conversation_requests_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_requests
    ADD CONSTRAINT conversation_requests_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.conversations(conversation_id) ON DELETE CASCADE;


--
-- Name: conversation_requests conversation_requests_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_requests
    ADD CONSTRAINT conversation_requests_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.jobs(job_id);


--
-- Name: conversation_summaries conversation_summaries_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_summaries
    ADD CONSTRAINT conversation_summaries_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.conversations(conversation_id) ON DELETE CASCADE;


--
-- Name: conversation_turns conversation_turns_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_turns
    ADD CONSTRAINT conversation_turns_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.conversations(conversation_id) ON DELETE CASCADE;


--
-- Name: conversation_turns conversation_turns_conversation_id_request_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_turns
    ADD CONSTRAINT conversation_turns_conversation_id_request_id_fkey FOREIGN KEY (conversation_id, request_id) REFERENCES public.conversation_requests(conversation_id, request_id);


--
-- Name: courses courses_collection_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.courses
    ADD CONSTRAINT courses_collection_id_fkey FOREIGN KEY (collection_id) REFERENCES public.collections(collection_id) ON DELETE CASCADE;


--
-- Name: courses courses_locator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.courses
    ADD CONSTRAINT courses_locator_id_fkey FOREIGN KEY (locator_id) REFERENCES public.locators(locator_id) ON DELETE CASCADE;


--
-- Name: courses courses_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.courses
    ADD CONSTRAINT courses_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.sources(source_id) ON DELETE CASCADE;


--
-- Name: decisions decisions_operation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decisions
    ADD CONSTRAINT decisions_operation_id_fkey FOREIGN KEY (operation_id) REFERENCES public.operations(operation_id);


--
-- Name: decisions decisions_profile_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decisions
    ADD CONSTRAINT decisions_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES public.connection_profiles(profile_id) ON DELETE RESTRICT;


--
-- Name: gap_observations gap_observations_gap_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gap_observations
    ADD CONSTRAINT gap_observations_gap_id_fkey FOREIGN KEY (gap_id) REFERENCES public.retrieval_gaps(gap_id) ON DELETE CASCADE;


--
-- Name: gap_observations gap_observations_trace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gap_observations
    ADD CONSTRAINT gap_observations_trace_id_fkey FOREIGN KEY (trace_id) REFERENCES public.retrieval_traces(trace_id) ON DELETE CASCADE;


--
-- Name: gap_resolutions gap_resolutions_gap_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.gap_resolutions
    ADD CONSTRAINT gap_resolutions_gap_id_fkey FOREIGN KEY (gap_id) REFERENCES public.retrieval_gaps(gap_id) ON DELETE CASCADE;


--
-- Name: job_events job_events_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.job_events
    ADD CONSTRAINT job_events_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.jobs(job_id) ON DELETE CASCADE;


--
-- Name: locators locators_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.locators
    ADD CONSTRAINT locators_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.sources(source_id) ON DELETE CASCADE;


--
-- Name: model_calls model_calls_claim_review_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_calls
    ADD CONSTRAINT model_calls_claim_review_id_fkey FOREIGN KEY (claim_review_id) REFERENCES public.claim_reviews(review_id) ON DELETE CASCADE;


--
-- Name: model_calls model_calls_decision_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_calls
    ADD CONSTRAINT model_calls_decision_id_fkey FOREIGN KEY (decision_id) REFERENCES public.decisions(decision_id) ON DELETE CASCADE;


--
-- Name: model_calls model_calls_operation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_calls
    ADD CONSTRAINT model_calls_operation_id_fkey FOREIGN KEY (operation_id) REFERENCES public.operations(operation_id) ON DELETE SET NULL;


--
-- Name: model_calls model_calls_trace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_calls
    ADD CONSTRAINT model_calls_trace_id_fkey FOREIGN KEY (trace_id) REFERENCES public.retrieval_traces(trace_id) ON DELETE CASCADE;


--
-- Name: operation_calls operation_calls_operation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operation_calls
    ADD CONSTRAINT operation_calls_operation_id_fkey FOREIGN KEY (operation_id) REFERENCES public.operations(operation_id) ON DELETE CASCADE;


--
-- Name: prereq_mentions prereq_mentions_course_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.prereq_mentions
    ADD CONSTRAINT prereq_mentions_course_id_fkey FOREIGN KEY (course_id) REFERENCES public.courses(course_id) ON DELETE CASCADE;


--
-- Name: prereq_mentions prereq_mentions_target_course_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.prereq_mentions
    ADD CONSTRAINT prereq_mentions_target_course_id_fkey FOREIGN KEY (target_course_id) REFERENCES public.courses(course_id) ON DELETE SET NULL;


--
-- Name: prereq_nodes prereq_nodes_course_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.prereq_nodes
    ADD CONSTRAINT prereq_nodes_course_id_fkey FOREIGN KEY (course_id) REFERENCES public.courses(course_id) ON DELETE CASCADE;


--
-- Name: prereq_nodes prereq_nodes_parent_node_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.prereq_nodes
    ADD CONSTRAINT prereq_nodes_parent_node_id_fkey FOREIGN KEY (parent_node_id) REFERENCES public.prereq_nodes(node_id) ON DELETE CASCADE;


--
-- Name: program_course_mentions program_course_mentions_program_id_position_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.program_course_mentions
    ADD CONSTRAINT program_course_mentions_program_id_position_fkey FOREIGN KEY (program_id, "position") REFERENCES public.program_items(program_id, "position") ON DELETE CASCADE;


--
-- Name: program_course_mentions program_course_mentions_target_course_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.program_course_mentions
    ADD CONSTRAINT program_course_mentions_target_course_id_fkey FOREIGN KEY (target_course_id) REFERENCES public.courses(course_id) ON DELETE SET NULL;


--
-- Name: program_items program_items_program_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.program_items
    ADD CONSTRAINT program_items_program_id_fkey FOREIGN KEY (program_id) REFERENCES public.programs(program_id) ON DELETE CASCADE;


--
-- Name: programs programs_collection_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.programs
    ADD CONSTRAINT programs_collection_id_fkey FOREIGN KEY (collection_id) REFERENCES public.collections(collection_id) ON DELETE CASCADE;


--
-- Name: provider_batch_items provider_batch_items_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.provider_batch_items
    ADD CONSTRAINT provider_batch_items_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES public.provider_batches(batch_id) ON DELETE CASCADE;


--
-- Name: provider_batch_items provider_batch_items_operation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.provider_batch_items
    ADD CONSTRAINT provider_batch_items_operation_id_fkey FOREIGN KEY (operation_id) REFERENCES public.operations(operation_id);


--
-- Name: responses responses_trace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.responses
    ADD CONSTRAINT responses_trace_id_fkey FOREIGN KEY (trace_id) REFERENCES public.retrieval_traces(trace_id) ON DELETE CASCADE;


--
-- Name: retrieval_gaps retrieval_gaps_collection_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.retrieval_gaps
    ADD CONSTRAINT retrieval_gaps_collection_id_fkey FOREIGN KEY (collection_id) REFERENCES public.collections(collection_id) ON DELETE CASCADE;


--
-- Name: retrieval_traces retrieval_traces_collection_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.retrieval_traces
    ADD CONSTRAINT retrieval_traces_collection_id_fkey FOREIGN KEY (collection_id) REFERENCES public.collections(collection_id) ON DELETE CASCADE;


--
-- Name: sim_builds sim_builds_collection_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sim_builds
    ADD CONSTRAINT sim_builds_collection_id_fkey FOREIGN KEY (collection_id) REFERENCES public.collections(collection_id) ON DELETE CASCADE;


--
-- Name: sim_cluster_members sim_cluster_members_cluster_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sim_cluster_members
    ADD CONSTRAINT sim_cluster_members_cluster_id_fkey FOREIGN KEY (cluster_id) REFERENCES public.sim_clusters(cluster_id) ON DELETE CASCADE;


--
-- Name: sim_cluster_members sim_cluster_members_course_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sim_cluster_members
    ADD CONSTRAINT sim_cluster_members_course_id_fkey FOREIGN KEY (course_id) REFERENCES public.courses(course_id) ON DELETE CASCADE;


--
-- Name: sim_clusters sim_clusters_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sim_clusters
    ADD CONSTRAINT sim_clusters_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.sim_builds(build_id) ON DELETE CASCADE;


--
-- Name: sim_clusters sim_clusters_medoid_course_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sim_clusters
    ADD CONSTRAINT sim_clusters_medoid_course_id_fkey FOREIGN KEY (medoid_course_id) REFERENCES public.courses(course_id) ON DELETE CASCADE;


--
-- Name: sim_clusters sim_clusters_parent_cluster_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sim_clusters
    ADD CONSTRAINT sim_clusters_parent_cluster_id_fkey FOREIGN KEY (parent_cluster_id) REFERENCES public.sim_clusters(cluster_id) ON DELETE CASCADE;


--
-- Name: sim_courses sim_courses_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sim_courses
    ADD CONSTRAINT sim_courses_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.sim_builds(build_id) ON DELETE CASCADE;


--
-- Name: sim_courses sim_courses_course_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sim_courses
    ADD CONSTRAINT sim_courses_course_id_fkey FOREIGN KEY (course_id) REFERENCES public.courses(course_id) ON DELETE CASCADE;


--
-- Name: sim_edges sim_edges_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sim_edges
    ADD CONSTRAINT sim_edges_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.sim_builds(build_id) ON DELETE CASCADE;


--
-- Name: sim_edges sim_edges_course_a_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sim_edges
    ADD CONSTRAINT sim_edges_course_a_fkey FOREIGN KEY (course_a) REFERENCES public.courses(course_id) ON DELETE CASCADE;


--
-- Name: sim_edges sim_edges_course_b_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sim_edges
    ADD CONSTRAINT sim_edges_course_b_fkey FOREIGN KEY (course_b) REFERENCES public.courses(course_id) ON DELETE CASCADE;


--
-- Name: sources sources_collection_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sources
    ADD CONSTRAINT sources_collection_id_fkey FOREIGN KEY (collection_id) REFERENCES public.collections(collection_id) ON DELETE CASCADE;


--
-- Name: tool_execution_operations tool_execution_operations_operation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tool_execution_operations
    ADD CONSTRAINT tool_execution_operations_operation_id_fkey FOREIGN KEY (operation_id) REFERENCES public.operations(operation_id) ON DELETE CASCADE;


--
-- Name: tool_executions tool_executions_operation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tool_executions
    ADD CONSTRAINT tool_executions_operation_id_fkey FOREIGN KEY (operation_id) REFERENCES public.operations(operation_id) ON DELETE CASCADE;


--
-- Name: trace_contributions trace_contributions_trace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trace_contributions
    ADD CONSTRAINT trace_contributions_trace_id_fkey FOREIGN KEY (trace_id) REFERENCES public.retrieval_traces(trace_id) ON DELETE CASCADE;


--
-- Name: trace_seams trace_seams_trace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trace_seams
    ADD CONSTRAINT trace_seams_trace_id_fkey FOREIGN KEY (trace_id) REFERENCES public.retrieval_traces(trace_id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--
