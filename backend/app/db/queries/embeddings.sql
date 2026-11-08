-- name: upsert_chunk_embedding
INSERT INTO chunk_embeddings (chunk_id, model, dim, embedding)
VALUES (%(chunk_id)s, %(model)s, %(dim)s, %(embedding)s)
ON CONFLICT (chunk_id, model) DO UPDATE SET
    dim       = EXCLUDED.dim,
    embedding = EXCLUDED.embedding;

-- name: set_hnsw_ef_search
SELECT set_config('hnsw.ef_search', %(ef_search)s::text, true);

-- name: embed_search_768
WITH ann AS (
    SELECT e.chunk_id,
           ((e.embedding)::public.vector(768)) <=> ((%(q)s)::public.vector(768)) AS dist
    FROM chunk_embeddings e
    WHERE e.dim = 768 AND e.model = %(model)s
    ORDER BY ((e.embedding)::public.vector(768)) <=> ((%(q)s)::public.vector(768))
    LIMIT %(ann_limit)s
)
SELECT c.chunk_id,
       c.source_id,
       c.chunk_index,
       (1.0 - a.dist)::float8 AS dot
FROM ann a
JOIN chunks c  ON c.chunk_id = a.chunk_id
JOIN sources s ON s.source_id = c.source_id
WHERE s.collection_id = %(collection_id)s
  AND s.status = 'indexed'
  AND s.is_current
  AND (s.workspace_key IS NULL OR s.workspace_key = %(workspace_key)s)
  AND (%(source_ids)s::uuid[] IS NULL OR c.source_id = ANY(%(source_ids)s::uuid[]))
  AND NOT c.search_hidden
  AND (1.0 - a.dist) >= %(min_similarity)s
ORDER BY a.dist ASC, c.source_id, c.chunk_index
LIMIT %(limit)s;

-- name: embed_search
SELECT c.chunk_id,
       c.source_id,
       c.chunk_index,
       (1.0 - (e.embedding <=> %(q)s))::float8 AS dot
FROM chunk_embeddings e
JOIN chunks c  ON c.chunk_id = e.chunk_id
JOIN sources s ON s.source_id = c.source_id
WHERE e.model = %(model)s
  AND e.dim = %(dim)s
  AND s.collection_id = %(collection_id)s
  AND s.status = 'indexed'
  AND s.is_current
  AND (s.workspace_key IS NULL OR s.workspace_key = %(workspace_key)s)
  AND (%(source_ids)s::uuid[] IS NULL OR c.source_id = ANY(%(source_ids)s::uuid[]))
  AND NOT c.search_hidden
  AND (1.0 - (e.embedding <=> %(q)s)) >= %(min_similarity)s
ORDER BY (e.embedding <=> %(q)s) ASC, c.source_id, c.chunk_index
LIMIT %(limit)s;
