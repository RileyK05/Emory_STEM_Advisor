-- One generation settlement per workspace, request, and bucket.
-- A retry of the same request identity does not consume the bucket again.

CREATE TABLE public.quota_settlements (
    workspace_key text NOT NULL,
    request_id uuid NOT NULL,
    bucket_type text NOT NULL,
    delta_count integer NOT NULL,
    delta_amount double precision DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT quota_settlements_pkey PRIMARY KEY (workspace_key, request_id, bucket_type),
    CONSTRAINT quota_settlements_bucket_check CHECK (
        bucket_type IN ('queries', 'tokens', 'cost_usd')
    ),
    CONSTRAINT quota_settlements_count_check CHECK (delta_count >= 0),
    CONSTRAINT quota_settlements_workspace_check CHECK (
        length(workspace_key) BETWEEN 1 AND 200
    )
);
