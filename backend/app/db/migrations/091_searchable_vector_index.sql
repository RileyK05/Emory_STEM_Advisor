-- Only searchable vectors in the HNSW index.
--
-- The index held every vector, but search can return only chunks of indexed,
-- current sources that are not hidden: 11.8k of 52.9k on the dev data. HNSW hands
-- back its 200 nearest rows before those filters run, so on average 57 survived
-- and the embed seam found 87% of the true top 40. A partial index over
-- searchable rows found 99%, at 48 MB instead of 174 MB.
--
-- `searchable` mirrors the search filters (sources.status = 'indexed',
-- sources.is_current, NOT chunks.search_hidden). The triggers keep it in step on
-- every write path, including reactivating an old version, so no caller has to
-- remember it. Collection, workspace and source-scope filters stay in the query.
--
-- The old index is dropped before the backfill so the UPDATE doesn't maintain it
-- row by row.

DROP INDEX public.chunk_embeddings_cosine_768_hnsw_idx;

ALTER TABLE public.chunk_embeddings
    ADD COLUMN searchable boolean NOT NULL DEFAULT false;

UPDATE public.chunk_embeddings e
SET searchable = true
FROM public.chunks c
JOIN public.sources s ON s.source_id = c.source_id
WHERE c.chunk_id = e.chunk_id
  AND s.status = 'indexed' AND s.is_current AND NOT c.search_hidden;

-- A new vector takes its chunk's and source's current state.
CREATE FUNCTION public.chunk_embedding_set_searchable() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.searchable := coalesce((
        SELECT s.status = 'indexed' AND s.is_current AND NOT c.search_hidden
        FROM public.chunks c
        JOIN public.sources s ON s.source_id = c.source_id
        WHERE c.chunk_id = NEW.chunk_id
    ), false);
    RETURN NEW;
END;
$$;

CREATE TRIGGER chunk_embedding_searchable
    BEFORE INSERT ON public.chunk_embeddings
    FOR EACH ROW EXECUTE FUNCTION public.chunk_embedding_set_searchable();

-- Publishing, superseding, reactivating or failing a source moves its vectors.
CREATE FUNCTION public.source_set_vectors_searchable() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE public.chunk_embeddings e
    SET searchable = (NEW.status = 'indexed' AND NEW.is_current AND NOT c.search_hidden)
    FROM public.chunks c
    WHERE c.source_id = NEW.source_id
      AND e.chunk_id = c.chunk_id
      AND e.searchable IS DISTINCT FROM
          (NEW.status = 'indexed' AND NEW.is_current AND NOT c.search_hidden);
    RETURN NULL;
END;
$$;

CREATE TRIGGER source_vectors_searchable
    AFTER UPDATE OF status, is_current ON public.sources
    FOR EACH ROW
    WHEN (OLD.status IS DISTINCT FROM NEW.status OR OLD.is_current IS DISTINCT FROM NEW.is_current)
    EXECUTE FUNCTION public.source_set_vectors_searchable();

-- Hiding or showing a chunk (repeated term cards) moves its vectors.
CREATE FUNCTION public.chunk_set_vectors_searchable() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    visible boolean;
BEGIN
    SELECT s.status = 'indexed' AND s.is_current AND NOT NEW.search_hidden
    INTO visible
    FROM public.sources s
    WHERE s.source_id = NEW.source_id;
    UPDATE public.chunk_embeddings
    SET searchable = coalesce(visible, false)
    WHERE chunk_id = NEW.chunk_id
      AND searchable IS DISTINCT FROM coalesce(visible, false);
    RETURN NULL;
END;
$$;

CREATE TRIGGER chunk_vectors_searchable
    AFTER UPDATE OF search_hidden ON public.chunks
    FOR EACH ROW
    WHEN (OLD.search_hidden IS DISTINCT FROM NEW.search_hidden)
    EXECUTE FUNCTION public.chunk_set_vectors_searchable();

CREATE INDEX chunk_embeddings_cosine_768_hnsw_idx
    ON public.chunk_embeddings
    USING hnsw (((embedding)::public.vector(768)) public.vector_cosine_ops)
    WHERE (dim = 768 AND searchable);
