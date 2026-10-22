-- Keep published evidence/history when a program projection is refreshed.
ALTER TABLE public.programs ADD COLUMN is_active boolean NOT NULL DEFAULT true;
CREATE INDEX programs_active_collection_idx ON public.programs (collection_id)
    WHERE is_active;

ALTER TABLE public.program_rule_releases
    DROP CONSTRAINT program_rule_releases_program_id_fkey,
    DROP CONSTRAINT program_rule_releases_source_id_fkey,
    ADD CONSTRAINT program_rule_releases_program_id_fkey
        FOREIGN KEY (program_id) REFERENCES public.programs(program_id) ON DELETE RESTRICT,
    ADD CONSTRAINT program_rule_releases_source_id_fkey
        FOREIGN KEY (source_id) REFERENCES public.sources(source_id) ON DELETE RESTRICT;

ALTER TABLE public.degree_audits
    DROP CONSTRAINT degree_audits_program_id_fkey,
    DROP CONSTRAINT degree_audits_rule_release_id_fkey,
    DROP CONSTRAINT degree_audits_overall_status_check,
    ADD CONSTRAINT degree_audits_program_id_fkey
        FOREIGN KEY (program_id) REFERENCES public.programs(program_id) ON DELETE RESTRICT,
    ADD CONSTRAINT degree_audits_rule_release_id_fkey
        FOREIGN KEY (rule_release_id) REFERENCES public.program_rule_releases(release_id)
        ON DELETE RESTRICT,
    ADD CONSTRAINT degree_audits_overall_status_check
        CHECK (overall_status IN ('satisfied', 'in_progress_satisfied',
            'conditional_satisfied', 'outstanding', 'unresolved'));

ALTER TABLE public.audit_rule_evaluations
    DROP CONSTRAINT audit_rule_evaluations_status_check,
    ADD CONSTRAINT audit_rule_evaluations_status_check
        CHECK (status IN ('satisfied', 'in_progress_satisfied', 'conditional_satisfied',
            'outstanding', 'unresolved', 'not_selected'));

-- An earlier satisfied result may rely on the incomplete version-one solver.
UPDATE public.degree_audits SET is_stale = true;

ALTER TABLE public.resource_grants
    ADD CONSTRAINT resource_grants_actions_check
        CHECK (revoked_at IS NOT NULL OR (cardinality(allowed_actions) > 0
            AND allowed_actions <@ ARRAY['read', 'write', 'verify']::text[]))
        NOT VALID,
    ADD CONSTRAINT resource_grants_support_read_only
        CHECK (revoked_at IS NOT NULL OR grant_type <> 'support_access'
            OR allowed_actions = ARRAY['read']::text[])
        NOT VALID;
-- Revoke incompatible legacy grants; do not silently upgrade their authority.
UPDATE public.resource_grants SET revoked_at = coalesce(revoked_at, now())
WHERE cardinality(allowed_actions) = 0
    OR NOT (allowed_actions <@ ARRAY['read', 'write', 'verify']::text[])
    OR (grant_type = 'support_access' AND allowed_actions <> ARRAY['read']::text[]);
ALTER TABLE public.resource_grants VALIDATE CONSTRAINT resource_grants_actions_check;
ALTER TABLE public.resource_grants VALIDATE CONSTRAINT resource_grants_support_read_only;
