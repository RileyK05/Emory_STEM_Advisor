-- 045_roles_and_access_grants.sql
-- Extend user roles to student, advisor, admin, auditor and add expiring resource access grants.

ALTER TABLE public.app_users DROP CONSTRAINT app_users_role_check;
ALTER TABLE public.app_users ADD CONSTRAINT app_users_role_check
    CHECK (role IN ('student', 'advisor', 'admin', 'auditor'));

CREATE TABLE public.resource_grants (
    grant_id uuid NOT NULL,
    grant_type text NOT NULL,
    actor_user_id uuid NOT NULL,
    target_workspace_key text NOT NULL,
    resource_type text NOT NULL,
    resource_id text,
    allowed_actions text[] DEFAULT '{read}'::text[] NOT NULL,
    reason text NOT NULL,
    approved_by_user_id uuid NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    revoked_at timestamp with time zone,
    revoked_by_user_id uuid,
    CONSTRAINT resource_grants_pkey PRIMARY KEY (grant_id),
    CONSTRAINT resource_grants_actor_fkey FOREIGN KEY (actor_user_id) REFERENCES public.app_users(user_id) ON DELETE CASCADE,
    CONSTRAINT resource_grants_approved_by_fkey FOREIGN KEY (approved_by_user_id) REFERENCES public.app_users(user_id) ON DELETE CASCADE,
    CONSTRAINT resource_grants_revoked_by_fkey FOREIGN KEY (revoked_by_user_id) REFERENCES public.app_users(user_id) ON DELETE SET NULL,
    CONSTRAINT resource_grants_type_check CHECK (grant_type IN ('advisor_access', 'support_access')),
    CONSTRAINT resource_grants_resource_type_check CHECK (resource_type IN ('conversation', 'plan', 'all_records')),
    CONSTRAINT resource_grants_reason_check CHECK (length(reason) BETWEEN 5 AND 1000),
    CONSTRAINT resource_grants_target_workspace_check CHECK (length(target_workspace_key) BETWEEN 1 AND 200),
    CONSTRAINT resource_grants_no_self_approval CHECK (actor_user_id <> approved_by_user_id OR grant_type = 'advisor_access')
);

CREATE INDEX resource_grants_lookup_idx ON public.resource_grants (actor_user_id, target_workspace_key, expires_at)
    WHERE revoked_at IS NULL;
CREATE INDEX resource_grants_target_idx ON public.resource_grants (target_workspace_key);
CREATE INDEX resource_grants_created_idx ON public.resource_grants (created_at DESC);

