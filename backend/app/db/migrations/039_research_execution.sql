-- Private, durable tool/check results and rejected proposals for research audit.
ALTER TABLE public.research_steps ADD COLUMN details jsonb;
