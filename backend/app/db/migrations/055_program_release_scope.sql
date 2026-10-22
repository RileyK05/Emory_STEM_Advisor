-- Pin the reviewed projection and metadata, not only its source bytes.
ALTER TABLE public.program_rule_releases
    ADD COLUMN bound_program_digest text,
    ADD COLUMN source_metadata_revision integer NOT NULL DEFAULT 0;

-- Pending legacy drafts cannot establish which parsed projection was reviewed.
UPDATE public.program_rule_releases SET status = 'archived'
WHERE status IN ('draft', 'reviewing', 'approved');

CREATE VIEW public.degree_audit_current_state AS
SELECT a.audit_id, a.workspace_key, a.program_id, a.rule_release_id,
       a.record_id, a.record_revision, a.rules_digest, a.overall_status,
       (a.is_stale OR NOT p.is_active
        OR p.current_rule_release_id IS DISTINCT FROM a.rule_release_id
        OR record.record_revision IS DISTINCT FROM a.record_revision
        OR release.status <> 'published'
        OR NOT coalesce(source.is_current, false) OR source.status IS DISTINCT FROM 'indexed'
        OR coalesce(provenance.lifecycle_status, 'active') IN ('staged', 'deprecated', 'archived')
        OR p.source_id IS DISTINCT FROM release.source_id
        OR source.index_revision IS DISTINCT FROM release.source_index_revision
        OR source.file_hash IS DISTINCT FROM release.source_sha256
        OR p.parsed_content_digest IS DISTINCT FROM release.bound_program_digest
        OR coalesce(provenance.metadata_revision, 0) <> release.source_metadata_revision
       ) AS is_stale,
       a.completed_credits, a.in_progress_credits, a.unverified_credits,
       a.results_summary, a.blockers, a.assumptions, a.created_at,
       p.title AS program_title, p.kind AS program_kind, p.current_rule_release_id,
       record.record_revision AS current_record_revision
FROM public.degree_audits a
JOIN public.programs p ON p.program_id = a.program_id
JOIN public.student_academic_records record ON record.record_id = a.record_id
JOIN public.program_rule_releases release ON release.release_id = a.rule_release_id
LEFT JOIN public.sources source ON source.source_id = release.source_id
LEFT JOIN public.source_provenance provenance ON provenance.source_id = source.source_id;
