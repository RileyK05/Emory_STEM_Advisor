-- 046_privacy_exports_and_deletions.sql
-- Tables for student data export, privacy deletions, and replay/restore suppression tombstones.

CREATE TABLE public.privacy_exports (
    export_id uuid NOT NULL,
    workspace_key text NOT NULL,
    user_id uuid,
    status text NOT NULL,
    manifest jsonb,
    download_token text,
    expires_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    error text,
    CONSTRAINT privacy_exports_pkey PRIMARY KEY (export_id),
    CONSTRAINT privacy_exports_user_fkey FOREIGN KEY (user_id) REFERENCES public.app_users(user_id) ON DELETE SET NULL,
    CONSTRAINT privacy_exports_status_check CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    CONSTRAINT privacy_exports_workspace_check CHECK (length(workspace_key) BETWEEN 1 AND 200)
);

CREATE INDEX privacy_exports_workspace_idx ON public.privacy_exports (workspace_key, created_at DESC);
CREATE INDEX privacy_exports_download_token_idx ON public.privacy_exports (download_token) WHERE download_token IS NOT NULL;

CREATE TABLE public.privacy_deletions (
    deletion_id uuid NOT NULL,
    workspace_key text NOT NULL,
    user_id uuid,
    status text NOT NULL,
    fenced_at timestamp with time zone,
    stage text,
    deleted_counts jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    error text,
    CONSTRAINT privacy_deletions_pkey PRIMARY KEY (deletion_id),
    CONSTRAINT privacy_deletions_user_fkey FOREIGN KEY (user_id) REFERENCES public.app_users(user_id) ON DELETE SET NULL,
    CONSTRAINT privacy_deletions_status_check CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    CONSTRAINT privacy_deletions_workspace_check CHECK (length(workspace_key) BETWEEN 1 AND 200)
);

CREATE INDEX privacy_deletions_workspace_idx ON public.privacy_deletions (workspace_key, created_at DESC);

CREATE TABLE public.deletion_tombstones (
    tombstone_id uuid NOT NULL,
    workspace_key text NOT NULL,
    resource_type text NOT NULL,
    resource_id text NOT NULL,
    deleted_at timestamp with time zone DEFAULT now() NOT NULL,
    reason text NOT NULL,
    CONSTRAINT deletion_tombstones_pkey PRIMARY KEY (tombstone_id),
    CONSTRAINT deletion_tombstones_unique UNIQUE (workspace_key, resource_type, resource_id),
    CONSTRAINT deletion_tombstones_workspace_check CHECK (length(workspace_key) BETWEEN 1 AND 200),
    CONSTRAINT deletion_tombstones_reason_check CHECK (length(reason) BETWEEN 1 AND 500)
);

CREATE INDEX deletion_tombstones_lookup_idx ON public.deletion_tombstones (workspace_key, resource_type, resource_id);

