-- Degree and program audits, rule evaluations, and allocations

-- name: insert_degree_audit
INSERT INTO public.degree_audits (
    audit_id, workspace_key, program_id, rule_release_id, record_id, record_revision,
    rules_digest, overall_status, is_stale, completed_credits, in_progress_credits,
    unverified_credits, results_summary, blockers, assumptions
) VALUES (
    %(audit_id)s, %(workspace_key)s, %(program_id)s, %(rule_release_id)s, %(record_id)s, %(record_revision)s,
    %(rules_digest)s, %(overall_status)s, %(is_stale)s, %(completed_credits)s, %(in_progress_credits)s,
    %(unverified_credits)s, %(results_summary)s, %(blockers)s, %(assumptions)s
)
RETURNING audit_id, workspace_key, program_id, rule_release_id, record_id, record_revision,
          rules_digest, overall_status, is_stale, completed_credits, in_progress_credits,
          unverified_credits, results_summary, blockers, assumptions, created_at;

-- name: get_degree_audit_by_id
SELECT * FROM degree_audit_current_state WHERE audit_id = %(audit_id)s;

-- name: list_degree_audits_for_workspace
SELECT * FROM degree_audit_current_state
WHERE workspace_key = %(workspace_key)s
ORDER BY created_at DESC, audit_id;

-- name: list_auditable_programs
SELECT p.program_id, p.title, p.kind, p.current_rule_release_id AS rule_release_id
FROM programs p
JOIN program_rule_releases release ON release.release_id = p.current_rule_release_id
JOIN sources source ON source.source_id = p.source_id
LEFT JOIN source_provenance provenance ON provenance.source_id = source.source_id
WHERE p.is_active
  AND release.status = 'published'
  AND release.program_id = p.program_id
  AND source.is_current
  AND source.status = 'indexed'
  AND coalesce(provenance.lifecycle_status, 'active') NOT IN ('staged', 'deprecated', 'archived')
  AND p.source_id = release.source_id
  AND p.source_index_revision = source.index_revision
  AND p.source_sha256 = source.file_hash
  AND source.index_revision = release.source_index_revision
  AND source.file_hash = release.source_sha256
  AND p.parsed_content_digest IS NOT DISTINCT FROM release.bound_program_digest
  AND coalesce(provenance.metadata_revision, 0) = release.source_metadata_revision
ORDER BY p.title, p.program_id;

-- name: mark_degree_audit_stale
UPDATE public.degree_audits
SET is_stale = true
WHERE audit_id = %(audit_id)s
RETURNING audit_id, is_stale;

-- name: insert_audit_rule_evaluation
INSERT INTO public.audit_rule_evaluations (
    evaluation_id, audit_id, node_id, parent_node_id, node_type, title,
    status, allocated_attempt_ids, allocated_credits, required_count,
    satisfied_count, unresolved_reason, raw_position
) VALUES (
    %(evaluation_id)s, %(audit_id)s, %(node_id)s, %(parent_node_id)s, %(node_type)s, %(title)s,
    %(status)s, %(allocated_attempt_ids)s, %(allocated_credits)s, %(required_count)s,
    %(satisfied_count)s, %(unresolved_reason)s, %(raw_position)s
)
RETURNING evaluation_id, audit_id, node_id, parent_node_id, node_type, title,
          status, allocated_attempt_ids, allocated_credits, required_count,
          satisfied_count, unresolved_reason, raw_position, created_at;

-- name: list_audit_rule_evaluations
SELECT evaluation_id, audit_id, node_id, parent_node_id, node_type, title,
       status, allocated_attempt_ids, allocated_credits, required_count,
       satisfied_count, unresolved_reason, raw_position, created_at
FROM public.audit_rule_evaluations
WHERE audit_id = %(audit_id)s
ORDER BY raw_position ASC NULLS LAST, node_id ASC;

