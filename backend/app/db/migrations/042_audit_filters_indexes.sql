-- Indexes for admin audit filtering, keyset cursor pagination, and snapshot export.
--
-- Keyset cursor ordered by (created_at DESC, audit_id DESC) ensures deterministic,
-- duplicate-free and gap-free pagination across concurrent inserts.
-- Action and target indexes accelerate scoped security audits.

CREATE INDEX IF NOT EXISTS admin_audit_keyset_idx
    ON public.admin_audit (created_at DESC, audit_id DESC);

CREATE INDEX IF NOT EXISTS admin_audit_action_idx
    ON public.admin_audit (action, created_at DESC);

CREATE INDEX IF NOT EXISTS admin_audit_target_idx
    ON public.admin_audit (target_type, target_id, created_at DESC);

