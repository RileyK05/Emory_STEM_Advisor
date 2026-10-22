-- Preserve the text of each successfully indexed source revision.

ALTER TABLE public.sources
    ADD COLUMN indexed_content_revision bigint,
    ADD CONSTRAINT sources_indexed_content_revision_check
        CHECK (indexed_content_revision IS NULL OR indexed_content_revision >= 0);

UPDATE public.sources
SET indexed_content_revision = index_revision
WHERE status = 'indexed' AND indexed_content_revision IS NULL;

CREATE TABLE public.source_revision_evidence (
    source_id uuid NOT NULL REFERENCES public.sources(source_id) ON DELETE CASCADE,
    index_revision bigint NOT NULL,
    chunk_id uuid NOT NULL,
    chunk_index integer NOT NULL,
    locator_label text NOT NULL,
    text text NOT NULL,
    text_digest text NOT NULL,
    PRIMARY KEY (source_id, index_revision, chunk_id),
    CONSTRAINT source_revision_evidence_revision_check CHECK (index_revision >= 0),
    CONSTRAINT source_revision_evidence_digest_check CHECK (length(text_digest) = 64)
);

CREATE INDEX source_revision_evidence_revision_idx
    ON public.source_revision_evidence (source_id, index_revision, chunk_index);
