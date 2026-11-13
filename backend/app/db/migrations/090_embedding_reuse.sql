-- Embedding reuse: each vector records a SHA-256 of the exact text it embedded, so
-- an import whose chunks repeat text that another source already embedded with the
-- same model copies that vector instead of running the model again.
--
-- Existing rows stay NULL. Backfilling them rewrites every row (about 85 s on the
-- dev data) while ADD COLUMN's lock blocks reads; each source gains keys the next
-- time it is indexed instead.

ALTER TABLE public.chunk_embeddings ADD COLUMN text_sha256 bytea;

CREATE INDEX chunk_embeddings_text_sha256_idx
    ON public.chunk_embeddings (model, text_sha256)
    WHERE (text_sha256 IS NOT NULL);
