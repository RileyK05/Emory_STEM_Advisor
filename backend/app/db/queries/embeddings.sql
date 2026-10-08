-- name: upsert_chunk_embedding
INSERT INTO chunk_embeddings (chunk_id, model, dim, embedding)
VALUES (%(chunk_id)s, %(model)s, %(dim)s, %(embedding)s)
ON CONFLICT (chunk_id, model) DO UPDATE SET
    dim       = EXCLUDED.dim,
    embedding = EXCLUDED.embedding;

-- name: embed_search
SELECT c.chunk_id,
       c.source_id,
       c.chunk_index,
       -(e.embedding <#> %(q)s) AS dot
FROM chunk_embeddings e
JOIN chunks c  ON c.chunk_id = e.chunk_id
JOIN sources s ON s.source_id = c.source_id
WHERE e.model = %(model)s
  AND e.dim = %(dim)s
  AND s.collection_id = %(collection_id)s
  AND s.status = 'indexed'
  AND -(e.embedding <#> %(q)s) >= %(min_similarity)s
ORDER BY dot DESC, c.source_id, c.chunk_index
LIMIT %(limit)s;
