-- Reviewed, versioned admin maintenance of course identities.
--
-- A course row keeps two independent facts: where it came from (authority) and
-- whether it is currently offered (lifecycle, added in 072). An admin edit is a
-- recorded change, not a mutable row patch: drafts are inert, publication is one
-- transaction, and a later baseline import never overwrites an admin-controlled
-- identity -- it queues a conflict instead.

ALTER TABLE public.courses
    ADD COLUMN authority text NOT NULL DEFAULT 'catalog',
    ADD COLUMN admin_revision integer NOT NULL DEFAULT 0,
    ADD CONSTRAINT courses_authority_check CHECK (
        authority IN ('atlas', 'catalog', 'admin_correction', 'admin_created')),
    ADD CONSTRAINT courses_admin_revision_check CHECK (admin_revision >= 0);

CREATE INDEX courses_authority_idx ON public.courses (collection_id, authority);

CREATE TABLE public.course_changes (
    change_id uuid NOT NULL,
    collection_id uuid NOT NULL,
    course_id uuid NOT NULL,
    code text NOT NULL,
    change_kind text NOT NULL,
    status text NOT NULL,
    -- The admin_revision the editor observed. Optimistic concurrency: a stale
    -- value is rejected rather than merged.
    expected_revision integer NOT NULL,
    -- The admin_revision produced by publication (NULL until published).
    published_revision integer,
    -- Authority and values the correction was based on. A later import compares
    -- its baseline against this, never against the edited row.
    base_authority text,
    base_value jsonb NOT NULL DEFAULT '{}'::jsonb,
    proposed_value jsonb NOT NULL DEFAULT '{}'::jsonb,
    reason text NOT NULL,
    actor_user_id uuid,
    published_by_user_id uuid,
    conflict_note text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    published_at timestamp with time zone,
    CONSTRAINT course_changes_pkey PRIMARY KEY (change_id),
    CONSTRAINT course_changes_kind_check CHECK (
        change_kind IN ('create', 'update', 'retire', 'restore', 'baseline_conflict')),
    CONSTRAINT course_changes_status_check CHECK (
        status IN ('draft', 'published', 'rejected', 'superseded', 'conflicted')),
    CONSTRAINT course_changes_revision_check CHECK (
        expected_revision >= 0
        AND (published_revision IS NULL OR published_revision > 0)),
    CONSTRAINT course_changes_reason_check CHECK (
        length(reason) >= 1 AND length(reason) <= 2000),
    CONSTRAINT course_changes_published_check CHECK (
        status <> 'published'
        OR (published_at IS NOT NULL AND published_revision IS NOT NULL)),
    CONSTRAINT course_changes_code_check CHECK (
        length(code) >= 1 AND length(code) <= 120),
    CONSTRAINT course_changes_collection_id_fkey FOREIGN KEY (collection_id)
        REFERENCES public.collections(collection_id) ON DELETE CASCADE
);

CREATE INDEX course_changes_course_idx
    ON public.course_changes (collection_id, code, created_at DESC);
CREATE INDEX course_changes_status_idx
    ON public.course_changes (collection_id, status, created_at DESC);
-- One open draft per identity: two admins may not stage competing edits that
-- could publish over one another.
CREATE UNIQUE INDEX course_changes_open_draft_idx
    ON public.course_changes (collection_id, code)
    WHERE status = 'draft';
