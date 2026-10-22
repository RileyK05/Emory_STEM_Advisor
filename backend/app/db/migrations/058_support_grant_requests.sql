-- Support-access requests are approved by a different administrator.
-- Staged cross-workspace results stay unpublished until the grant is rechecked.

CREATE TABLE public.support_grant_requests (
    request_id uuid NOT NULL,
    requester_user_id uuid NOT NULL,
    actor_user_id uuid NOT NULL,
    target_workspace_key text NOT NULL,
    resource_type text NOT NULL,
    resource_id text,
    allowed_actions text[] DEFAULT '{read}'::text[] NOT NULL,
    reason text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    status text DEFAULT 'pending' NOT NULL,
    revision integer DEFAULT 1 NOT NULL,
    decision_note text,
    decided_by_user_id uuid,
    decided_at timestamp with time zone,
    grant_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT support_grant_requests_pkey PRIMARY KEY (request_id),
    CONSTRAINT support_grant_requests_requester_fkey FOREIGN KEY (requester_user_id)
        REFERENCES public.app_users(user_id) ON DELETE CASCADE,
    CONSTRAINT support_grant_requests_actor_fkey FOREIGN KEY (actor_user_id)
        REFERENCES public.app_users(user_id) ON DELETE CASCADE,
    CONSTRAINT support_grant_requests_decided_by_fkey FOREIGN KEY (decided_by_user_id)
        REFERENCES public.app_users(user_id) ON DELETE SET NULL,
    CONSTRAINT support_grant_requests_grant_fkey FOREIGN KEY (grant_id)
        REFERENCES public.resource_grants(grant_id) ON DELETE SET NULL,
    CONSTRAINT support_grant_requests_status_check CHECK (status IN ('pending', 'approved', 'rejected')),
    CONSTRAINT support_grant_requests_resource_type_check
        CHECK (resource_type IN ('conversation', 'plan', 'all_records')),
    CONSTRAINT support_grant_requests_reason_check CHECK (length(reason) BETWEEN 5 AND 1000),
    CONSTRAINT support_grant_requests_workspace_check
        CHECK (length(target_workspace_key) BETWEEN 1 AND 200),
    CONSTRAINT support_grant_requests_revision_check CHECK (revision >= 1),
    CONSTRAINT support_grant_requests_read_only_check CHECK (allowed_actions = '{read}'::text[])
);

CREATE INDEX support_grant_requests_status_idx
    ON public.support_grant_requests (status, created_at DESC);

CREATE TABLE public.granted_result_staging (
    staging_id uuid NOT NULL,
    grant_id uuid NOT NULL,
    actor_user_id uuid NOT NULL,
    target_workspace_key text NOT NULL,
    resource_type text NOT NULL,
    resource_id text NOT NULL,
    status text DEFAULT 'staged' NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    published_at timestamp with time zone,
    CONSTRAINT granted_result_staging_pkey PRIMARY KEY (staging_id),
    CONSTRAINT granted_result_staging_grant_fkey FOREIGN KEY (grant_id)
        REFERENCES public.resource_grants(grant_id) ON DELETE CASCADE,
    CONSTRAINT granted_result_staging_actor_fkey FOREIGN KEY (actor_user_id)
        REFERENCES public.app_users(user_id) ON DELETE CASCADE,
    CONSTRAINT granted_result_staging_status_check CHECK (status IN ('staged', 'published', 'withheld')),
    CONSTRAINT granted_result_staging_resource_check
        CHECK (resource_type IN ('conversation', 'plan', 'all_records'))
);

CREATE INDEX granted_result_staging_grant_idx ON public.granted_result_staging (grant_id, status);
