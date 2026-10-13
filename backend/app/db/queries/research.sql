-- Research advising persistence (migration 037).
--
-- A research run owns one logical investigation: its pinned scope manifest, its
-- bounded working state, the evidence it actually read, and the validated final
-- decision. All of it is workspace/turn private, never global student memory.
--
-- Two rules these queries exist to support:
--   * Evidence IDs are server-issued and stable within a run. Replaying a cached
--     tool result must restore the same IDs, so evidence insertion is
--     idempotent on (run_id, evidence_id) and the caller verifies the digest
--     rather than silently overwriting a different one.
--   * State updates use optimistic concurrency on state_revision, so a
--     superseded tool round cannot overwrite a newer one.

-- name: insert_research_run
INSERT INTO research_runs (
    run_id, operation_id, root_trace_id, workspace_key, collection_id,
    conversation_id, conversation_request_id, scope_manifest, scope_digest,
    prompt_version, config_version, state_revision, state, status, stop_reason
) VALUES (
    %(run_id)s, %(operation_id)s, %(root_trace_id)s, %(workspace_key)s, %(collection_id)s,
    %(conversation_id)s, %(conversation_request_id)s, %(scope_manifest)s, %(scope_digest)s,
    %(prompt_version)s, %(config_version)s, %(state_revision)s, %(state)s,
    %(status)s, %(stop_reason)s
)
RETURNING run_id, created_at;

-- name: get_research_run
SELECT run_id, operation_id, root_trace_id, workspace_key, collection_id,
       conversation_id, conversation_request_id, scope_manifest, scope_digest,
       prompt_version, config_version, state_revision, state, status, stop_reason,
       created_at, updated_at
FROM research_runs
WHERE run_id = %(run_id)s;

-- Replay guard: a settled conversation request returns its recorded run instead
-- of starting a second investigation.
-- name: get_research_run_for_conversation_request
SELECT run_id, operation_id, root_trace_id, scope_manifest, scope_digest,
       prompt_version, config_version, state_revision, state, status, stop_reason,
       created_at, updated_at
FROM research_runs
WHERE conversation_id = %(conversation_id)s
  AND conversation_request_id = %(request_id)s;

-- name: update_research_state
UPDATE research_runs
SET state = %(state)s,
    state_revision = state_revision + 1,
    updated_at = now()
WHERE run_id = %(run_id)s
  AND state_revision = %(expected_state_revision)s
  AND status = 'running'
RETURNING state_revision, state;

-- name: set_research_run_status
UPDATE research_runs
SET status = %(status)s,
    stop_reason = %(stop_reason)s,
    updated_at = now()
WHERE run_id = %(run_id)s
  AND status = 'running'
RETURNING run_id, status, stop_reason;

-- name: insert_research_step
INSERT INTO research_steps (
    run_id, ordinal, tool_execution_id, child_trace_id, action_kind, summary,
    issued_evidence_ids
) VALUES (
    %(run_id)s, %(ordinal)s, %(tool_execution_id)s, %(child_trace_id)s,
    %(action_kind)s, %(summary)s, %(issued_evidence_ids)s
)
ON CONFLICT (run_id, ordinal) DO NOTHING
RETURNING run_id, ordinal;

-- name: list_research_steps
SELECT run_id, ordinal, tool_execution_id, child_trace_id, action_kind, summary,
       issued_evidence_ids, details, created_at
FROM research_steps
WHERE run_id = %(run_id)s
ORDER BY ordinal;

-- name: update_research_step_details
UPDATE research_steps SET details = %(details)s
WHERE run_id = %(run_id)s AND ordinal = %(ordinal)s AND details IS NULL;

-- name: research_lead_courses
SELECT code, title, canonical_code, source_id, locator_id
FROM courses
WHERE collection_id = %(collection_id)s AND source_id = ANY(%(source_ids)s::uuid[])
  AND locator_id = ANY(%(locator_ids)s::uuid[])
ORDER BY code;

-- name: research_search_programs
SELECT p.program_id,
       ts_rank_cd(setweight(to_tsvector('english', p.title || ' ' || coalesce(p.department, '')), 'A')
           || setweight(to_tsvector('english', coalesce(string_agg(i.text, ' '), '')), 'D'),
           websearch_to_tsquery('english', %(query)s), 32) AS score
FROM programs p LEFT JOIN program_items i ON i.program_id = p.program_id
WHERE p.collection_id = %(collection_id)s
  AND p.source_id = ANY(%(source_ids)s::uuid[])
  AND (p.title ILIKE ANY(%(patterns)s::text[])
       OR p.department ILIKE ANY(%(patterns)s::text[])
       OR EXISTS (SELECT 1 FROM program_items i WHERE i.program_id = p.program_id
                  AND i.text ILIKE ANY(%(patterns)s::text[])))
GROUP BY p.program_id
ORDER BY score DESC, p.title
LIMIT 100;

-- name: research_programs_for_chunks
SELECT program_id, chunk_id FROM program_item_evidence
WHERE chunk_id = ANY(%(chunk_ids)s::uuid[])
UNION
SELECT program_id, chunk_id FROM program_field_evidence
WHERE chunk_id = ANY(%(chunk_ids)s::uuid[]);

-- name: finish_conversation_research
UPDATE research_runs SET status = %(status)s, stop_reason = %(reason)s, updated_at = now()
WHERE conversation_id = %(conversation_id)s AND conversation_request_id = %(request_id)s
  AND workspace_key = %(workspace_key)s AND status = 'running';

-- name: research_current_program_pins
SELECT program_id, source_id, source_index_revision, parsed_content_digest, binding_status
FROM programs WHERE program_id = ANY(%(program_ids)s::uuid[])
ORDER BY program_id FOR SHARE;

-- name: mark_research_trace
UPDATE retrieval_traces SET kind = 'research' WHERE trace_id = %(trace_id)s;

-- Idempotent: a replayed tool result must restore the same evidence identity.
-- A changed digest for an existing (run_id, evidence_id) is a bug, not a merge,
-- so nothing is overwritten here; the store compares and fails closed.
-- name: insert_research_evidence
INSERT INTO research_evidence (run_id, evidence_id, snapshot, digest)
VALUES (%(run_id)s, %(evidence_id)s, %(snapshot)s, %(digest)s)
ON CONFLICT (run_id, evidence_id) DO NOTHING
RETURNING run_id, evidence_id;

-- name: get_research_evidence
SELECT run_id, evidence_id, snapshot, digest, created_at
FROM research_evidence
WHERE run_id = %(run_id)s AND evidence_id = %(evidence_id)s;

-- name: list_research_evidence
SELECT run_id, evidence_id, snapshot, digest, created_at
FROM research_evidence
WHERE run_id = %(run_id)s
ORDER BY created_at, evidence_id;

-- name: insert_research_decision
INSERT INTO research_decisions (run_id, response_id, render_version, decision, digest)
VALUES (%(run_id)s, %(response_id)s, %(render_version)s, %(decision)s, %(digest)s)
RETURNING run_id, render_version;

-- name: list_research_decisions
SELECT run_id, response_id, render_version, decision, digest, created_at
FROM research_decisions
WHERE run_id = %(run_id)s
ORDER BY created_at;


-- Scoped reads (research_repository). Every read is limited to the run's pinned
-- source manifest by the caller passing that manifest's source IDs; the WHERE
-- clauses below re-check current/indexed/workspace state on every call, so a
-- source that changed or left scope since the run started reads as absent.

-- name: list_research_scope_sources
SELECT s.source_id, s.index_revision, s.filename, s.content_type, s.origin_key,
       s.workspace_key, p.source_category, p.classification_origin, p.publisher,
       p.source_url, p.edition, p.published_at, p.acquired_at, p.geography, p.occupation_id
FROM sources s
LEFT JOIN source_provenance p ON p.source_id = s.source_id
WHERE s.collection_id = %(collection_id)s
  AND s.status = 'indexed'
  AND s.is_current
  AND (s.workspace_key IS NULL OR s.workspace_key = %(workspace_key)s)
  AND (%(source_ids)s::uuid[] IS NULL OR s.source_id = ANY(%(source_ids)s::uuid[]))
ORDER BY s.source_id;

-- Share-locks the sources a read touches. Reindex, supersession and removal all
-- update the source row before they change its chunks, so while the reader's
-- transaction holds these locks its revision check and content reads agree.
-- name: research_current_source_revisions
SELECT s.source_id, s.index_revision
FROM sources s
WHERE s.source_id = ANY(%(source_ids)s::uuid[])
  AND s.collection_id = %(collection_id)s
  AND s.status = 'indexed'
  AND s.is_current
  AND (s.workspace_key IS NULL OR s.workspace_key = %(workspace_key)s)
ORDER BY s.source_id
FOR SHARE OF s;

-- Programs whose binding names a source in the run's manifest. The caller keeps
-- only those bound to the manifest's exact revision; unbound projections never
-- appear, so their unscoped text cannot be read as evidence.
-- name: list_research_programs
SELECT program_id, title, kind, degree, hours, courses_required, department,
       catalog, source_id, source_index_revision, parsed_content_digest, binding_status
FROM programs
WHERE collection_id = %(collection_id)s
  AND binding_status IN ('bound', 'evidence_unresolved')
  AND source_id = ANY(%(source_ids)s::uuid[])
ORDER BY kind, title, program_id;

-- name: research_chunks_by_ids
SELECT c.chunk_id, c.source_id, c.chunk_index, c.locator_id, l.label AS locator_label,
       c.text
FROM chunks c
JOIN locators l ON l.locator_id = c.locator_id
WHERE c.chunk_id = ANY(%(chunk_ids)s::uuid[])
  AND c.source_id = ANY(%(source_ids)s::uuid[]);

-- name: research_courses_by_codes
SELECT co.course_id, co.code, co.title, co.requisites_raw, co.parse_status,
       co.canonical_code, co.source_id, co.locator_id
FROM courses co
WHERE co.collection_id = %(collection_id)s
  AND co.code = ANY(%(codes)s::text[])
  AND co.source_id = ANY(%(source_ids)s::uuid[])
ORDER BY co.code;

-- name: research_chunks_for_locators
SELECT c.chunk_id, c.source_id, c.chunk_index, c.locator_id, l.label AS locator_label,
       c.text
FROM chunks c
JOIN locators l ON l.locator_id = c.locator_id
WHERE c.locator_id = ANY(%(locator_ids)s::uuid[])
  AND c.source_id = ANY(%(source_ids)s::uuid[])
ORDER BY c.source_id, c.chunk_index;

-- name: research_document_sections
SELECT l.locator_id, l.ordinal, l.locator_type, l.label,
       min(c.chunk_index) AS first_chunk, count(c.chunk_id) AS chunk_count
FROM locators l
JOIN chunks c ON c.locator_id = l.locator_id
WHERE l.source_id = %(source_id)s
GROUP BY l.locator_id, l.ordinal, l.locator_type, l.label
ORDER BY l.ordinal;

-- name: research_document_chunks
SELECT c.chunk_id, c.source_id, c.chunk_index, c.locator_id, l.label AS locator_label,
       c.text
FROM chunks c
JOIN locators l ON l.locator_id = c.locator_id
WHERE c.source_id = %(source_id)s
  AND c.chunk_index >= %(from_chunk)s
ORDER BY c.chunk_index
LIMIT %(limit)s;


-- Research store transactions (research_store).

-- Serializes evidence-ID assignment and step ordinals for one run.
-- name: lock_research_run
SELECT run_id, workspace_key, collection_id, scope_manifest, status, state_revision
FROM research_runs
WHERE run_id = %(run_id)s
FOR UPDATE;

-- Replay: the step a tool execution already recorded, with its issued IDs.
-- name: get_research_step_for_tool_execution
SELECT run_id, ordinal, tool_execution_id, issued_evidence_ids
FROM research_steps
WHERE run_id = %(run_id)s
  AND tool_execution_id = %(tool_execution_id)s;

-- name: next_research_step_ordinal
SELECT COALESCE(max(ordinal) + 1, 0) AS ordinal
FROM research_steps
WHERE run_id = %(run_id)s;
