-- Search speed: stored tsvectors for provenance roles and locator labels, source/course titles and filenames, plus HNSW cosine index on 768-dim embeddings.

ALTER TABLE public.source_provenance
    ADD COLUMN roles_vector tsvector
    GENERATED ALWAYS AS (to_tsvector('english'::regconfig, COALESCE(document_roles::text, ''::text))) STORED;

CREATE INDEX source_provenance_roles_vector_idx ON public.source_provenance USING gin (roles_vector);

ALTER TABLE public.source_provenance
    ADD COLUMN title_vector tsvector
    GENERATED ALWAYS AS (to_tsvector('english'::regconfig, COALESCE(title, ''::text))) STORED;

ALTER TABLE public.courses
    ADD COLUMN title_vector tsvector
    GENERATED ALWAYS AS (to_tsvector('english'::regconfig, COALESCE(title, ''::text))) STORED;

ALTER TABLE public.sources
    ADD COLUMN filename_vector tsvector
    GENERATED ALWAYS AS (to_tsvector('english'::regconfig, regexp_replace(COALESCE(filename, ''::text), '[-_.]'::text, ' '::text, 'g'::text))) STORED;

ALTER TABLE public.locators
    ADD COLUMN label_vector tsvector
    GENERATED ALWAYS AS (to_tsvector('english'::regconfig, COALESCE(label, ''::text))) STORED;

CREATE INDEX locators_label_vector_idx ON public.locators USING gin (label_vector);

CREATE INDEX chunk_embeddings_cosine_768_hnsw_idx
    ON public.chunk_embeddings
    USING hnsw (((embedding)::public.vector(768)) public.vector_cosine_ops)
    WHERE (dim = 768);
