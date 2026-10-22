-- Program comparison scenarios. Mention overlap is stored as codes, never as credit.

-- name: find_program_comparison
SELECT comparison_id
FROM program_comparisons
WHERE workspace_key = %(workspace_key)s
  AND record_id = %(record_id)s
  AND record_revision = %(record_revision)s
  AND program_set_digest = %(program_set_digest)s
  AND overlay_digest = %(overlay_digest)s;

-- name: insert_program_comparison
INSERT INTO program_comparisons (
    comparison_id, workspace_key, record_id, record_revision, program_set_digest,
    overlay, overlay_digest, sharing_policy, mention_kind, shared_mentions,
    credit_savings, coverage_comparable, ranking_blocked_reason
) VALUES (
    %(comparison_id)s, %(workspace_key)s, %(record_id)s, %(record_revision)s, %(program_set_digest)s,
    %(overlay)s, %(overlay_digest)s, %(sharing_policy)s, %(mention_kind)s, %(shared_mentions)s,
    %(credit_savings)s, %(coverage_comparable)s, %(ranking_blocked_reason)s
)
RETURNING comparison_id, created_at;

-- name: insert_program_comparison_member
INSERT INTO program_comparison_members (
    comparison_id, position, program_id, rule_release_id, audit_id, overlay_status, unresolved_count
) VALUES (
    %(comparison_id)s, %(position)s, %(program_id)s, %(rule_release_id)s, %(audit_id)s,
    %(overlay_status)s, %(unresolved_count)s
);

-- name: get_program_comparison
SELECT c.comparison_id, c.workspace_key, c.record_id, c.record_revision,
       c.sharing_policy, c.mention_kind, c.shared_mentions, c.credit_savings,
       c.coverage_comparable, c.ranking_blocked_reason, c.created_at,
       m.position, m.program_id, m.rule_release_id, m.audit_id, m.overlay_status,
       m.unresolved_count, a.overall_status AS saved_status,
       a.completed_credits AS saved_completed_credits, p.title AS program_title
FROM program_comparisons c
JOIN program_comparison_members m ON m.comparison_id = c.comparison_id
JOIN degree_audits a ON a.audit_id = m.audit_id
JOIN programs p ON p.program_id = m.program_id
WHERE c.comparison_id = %(comparison_id)s
ORDER BY m.position;

-- name: list_program_comparisons
SELECT comparison_id, workspace_key, record_id, record_revision, sharing_policy,
       mention_kind, shared_mentions, credit_savings, coverage_comparable,
       ranking_blocked_reason, created_at
FROM program_comparisons
WHERE workspace_key = %(workspace_key)s
ORDER BY created_at DESC, comparison_id;
