-- Institutional administration and scoped debug overrides.
--
-- app_users is the durable identity/role record. OIDC sessions remain opaque
-- and are joined by issuer/subject; the session table never stores a role, so a
-- demotion or disable takes effect on the next request.
--
-- debug_sessions are admin-owned, expiring override sets. The token is stored
-- only as a digest. Overrides never affect another workspace unless the admin
-- explicitly uses the token in that workspace's debug client.

CREATE TABLE public.app_users (
    user_id uuid NOT NULL,
    issuer text NOT NULL,
    subject text NOT NULL,
    workspace_key text NOT NULL,
    display_name text,
    email text,
    role text DEFAULT 'student' NOT NULL,
    status text DEFAULT 'active' NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone,
    CONSTRAINT app_users_pkey PRIMARY KEY (user_id),
    CONSTRAINT app_users_identity_key UNIQUE (issuer, subject),
    CONSTRAINT app_users_issuer_check CHECK (length(issuer) BETWEEN 1 AND 2048),
    CONSTRAINT app_users_subject_check CHECK (length(subject) BETWEEN 1 AND 2048),
    CONSTRAINT app_users_workspace_check CHECK (length(workspace_key) BETWEEN 1 AND 200),
    CONSTRAINT app_users_role_check CHECK (role IN ('student', 'admin')),
    CONSTRAINT app_users_status_check CHECK (status IN ('active', 'disabled'))
);

CREATE INDEX app_users_workspace_idx ON public.app_users (workspace_key);
CREATE INDEX app_users_role_status_idx ON public.app_users (role, status);
CREATE INDEX app_users_email_idx ON public.app_users (lower(email));

CREATE TABLE public.admin_audit (
    audit_id uuid NOT NULL,
    actor_user_id uuid,
    actor_workspace text NOT NULL,
    action text NOT NULL,
    target_type text NOT NULL,
    target_id text,
    detail jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT admin_audit_pkey PRIMARY KEY (audit_id),
    CONSTRAINT admin_audit_actor_user_fkey FOREIGN KEY (actor_user_id)
        REFERENCES public.app_users (user_id),
    CONSTRAINT admin_audit_action_check CHECK (length(action) BETWEEN 1 AND 100),
    CONSTRAINT admin_audit_target_type_check CHECK (length(target_type) BETWEEN 1 AND 100),
    CONSTRAINT admin_audit_detail_check CHECK (jsonb_typeof(detail) = 'object')
);

CREATE INDEX admin_audit_created_idx ON public.admin_audit (created_at DESC);
CREATE INDEX admin_audit_actor_idx ON public.admin_audit (actor_user_id, created_at DESC);

CREATE TABLE public.debug_sessions (
    debug_session_id uuid NOT NULL,
    owner_user_id uuid,
    owner_workspace text NOT NULL,
    token_hash bytea NOT NULL,
    overrides jsonb DEFAULT '{}'::jsonb NOT NULL,
    system_prompt text,
    expires_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    revoked_at timestamp with time zone,
    CONSTRAINT debug_sessions_pkey PRIMARY KEY (debug_session_id),
    CONSTRAINT debug_sessions_owner_user_fkey FOREIGN KEY (owner_user_id)
        REFERENCES public.app_users (user_id),
    CONSTRAINT debug_sessions_token_hash_key UNIQUE (token_hash),
    CONSTRAINT debug_sessions_owner_workspace_check CHECK (length(owner_workspace) BETWEEN 1 AND 200),
    CONSTRAINT debug_sessions_overrides_check CHECK (jsonb_typeof(overrides) = 'object'),
    CONSTRAINT debug_sessions_system_prompt_check CHECK (
        system_prompt IS NULL OR length(system_prompt) <= 20000)
);

CREATE INDEX debug_sessions_owner_idx ON public.debug_sessions (owner_user_id, created_at DESC);
CREATE INDEX debug_sessions_expiry_idx ON public.debug_sessions (expires_at)
    WHERE revoked_at IS NULL;
