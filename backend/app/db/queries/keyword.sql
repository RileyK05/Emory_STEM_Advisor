-- name: keyword_search
-- Exact course-code heading hits rank above topical matches; a longer
-- topical token then outranks shorter repetition. Lexical rank stays bounded
-- within each band, and numeric-reference matching remains part of that rank.
SELECT chunk_id, source_id, chunk_index,
       (
           CASE WHEN course_code_heading THEN 2.0
                WHEN specific THEN 1.0
                ELSE 0.0 END
           + lexical / (1.0 + lexical)
       )::float8 AS score
FROM (
    SELECT c.chunk_id,
           c.source_id,
           c.chunk_index,
           (
               ts_rank_cd(c.search_vector, to_tsquery('english', %(q)s))
               + CASE WHEN c.text ~ %(numeric_pattern)s THEN 1.0 ELSE 0.0 END
           ) AS lexical,
           CASE
               WHEN %(specific_q)s::text IS NULL THEN false
               ELSE c.search_vector @@ to_tsquery('english', %(specific_q)s)
           END AS specific,
           (%(course_code_header_pattern)s::text IS NOT NULL
            AND c.text ~* %(course_code_header_pattern)s::text) AS course_code_heading
    FROM chunks c
    JOIN sources s ON s.source_id = c.source_id
    WHERE s.collection_id = %(collection_id)s
      AND s.status = 'indexed'
      AND s.is_current
      AND (s.workspace_key IS NULL OR s.workspace_key = %(workspace_key)s)
      AND (%(source_ids)s::uuid[] IS NULL OR c.source_id = ANY(%(source_ids)s::uuid[]))
      AND (c.search_vector @@ to_tsquery('english', %(q)s)
           OR (%(numeric_pattern)s::text IS NOT NULL AND c.text ~ %(numeric_pattern)s::text))
) matched
ORDER BY score DESC, source_id, chunk_index
LIMIT %(limit)s;
