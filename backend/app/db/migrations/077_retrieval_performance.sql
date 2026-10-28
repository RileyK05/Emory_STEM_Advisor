-- Retrieval performance: keep per-stage retrieval timings with the trace.
--
-- The first keyword-plan experiment (a locator-label GIN index plus a candidate
-- UNION) improved a narrow course lookup but regressed broad policy/program
-- queries, so it was not kept. The trace column remains useful for measuring
-- the next change against real requests.
ALTER TABLE public.retrieval_traces
    ADD COLUMN timings jsonb DEFAULT '{}'::jsonb NOT NULL;
