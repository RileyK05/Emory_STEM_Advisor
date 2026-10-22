-- Attribute retrieval traces to the workspace that requested them so privacy
-- deletion can remove the stored question, response context, and citations.
-- Rows created by evaluation or other global reads remain NULL.

ALTER TABLE public.retrieval_traces
    ADD COLUMN IF NOT EXISTS workspace_key text;

ALTER TABLE public.retrieval_traces
    ADD CONSTRAINT retrieval_traces_workspace_key_check CHECK (
        workspace_key IS NULL OR char_length(workspace_key) BETWEEN 1 AND 200
    );

CREATE INDEX IF NOT EXISTS retrieval_traces_workspace_created_idx
    ON public.retrieval_traces (workspace_key, created_at DESC);
