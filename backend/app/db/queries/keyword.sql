-- name: keyword_search
SELECT c.chunk_id,
       c.source_id,
       c.chunk_index,
       ts_rank_cd(c.search_vector, to_tsquery('english', %(q)s)) AS score
FROM chunks c
JOIN sources s ON s.source_id = c.source_id
WHERE s.collection_id = %(collection_id)s
  AND s.status = 'indexed'
  AND c.search_vector @@ to_tsquery('english', %(q)s)
ORDER BY score DESC, c.source_id, c.chunk_index
LIMIT %(limit)s;
