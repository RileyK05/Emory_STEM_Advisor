-- Courses that leave the current authoritative catalog are retained as retired
-- identities so historical citations, prerequisite references and program
-- evidence never point at a silently deleted row. Current reads filter on this
-- tag; retired rows remain available for explicit historical lookup.

ALTER TABLE public.courses
    ADD COLUMN lifecycle text DEFAULT 'current' NOT NULL,
    ADD CONSTRAINT courses_lifecycle_check CHECK (lifecycle IN ('current', 'retired'));

CREATE INDEX courses_lifecycle_idx ON public.courses (collection_id, lifecycle);
