-- name: keyword_search
SELECT c.chunk_id,
       c.source_id,
       c.chunk_index,
       ts_rank_cd(c.search_vector, to_tsquery('english', %(q)s))
       + CASE WHEN c.text ~ %(numeric_pattern)s THEN 1.0 ELSE 0.0 END AS score
FROM chunks c
JOIN sources s ON s.source_id = c.source_id
WHERE s.collection_id = %(collection_id)s
  AND s.status = 'indexed'
  AND s.is_current
  AND (s.workspace_key IS NULL OR s.workspace_key = %(workspace_key)s)
  AND (%(source_ids)s::uuid[] IS NULL OR c.source_id = ANY(%(source_ids)s::uuid[]))
  AND (c.search_vector @@ to_tsquery('english', %(q)s)
       OR (%(numeric_pattern)s::text IS NOT NULL AND c.text ~ %(numeric_pattern)s::text))
ORDER BY score DESC, c.source_id, c.chunk_index
LIMIT %(limit)s;
