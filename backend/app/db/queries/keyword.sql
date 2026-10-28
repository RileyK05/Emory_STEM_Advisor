-- name: keyword_search
WITH matched AS MATERIALIZED (
    SELECT c.chunk_id,
           c.source_id,
           c.chunk_index,
           c.locator_id,
           (
               ts_rank_cd(c.search_vector, to_tsquery('english', %(q)s))
               + CASE WHEN c.text ~ %(numeric_pattern)s THEN 1.0 ELSE 0.0 END
           ) AS lexical,
           coalesce(pm.matched, false) AS program_match,
           (%(role_query)s::text IS NOT NULL AND
            to_tsvector('english', coalesce(pv.document_roles::text, ''))
                @@ plainto_tsquery('english', %(role_query)s)) AS role_match,
           CASE
               WHEN %(specific_q)s::text IS NULL THEN false
               ELSE c.search_vector @@ to_tsquery('english', %(specific_q)s)
           END AS specific,
           (%(course_code_header_pattern)s::text IS NOT NULL
            AND c.text ~* %(course_code_header_pattern)s::text) AS course_code_heading,
           (%(numeric_pattern)s::text IS NOT NULL
            AND c.text ~ %(numeric_pattern)s::text) AS numeric_match
    FROM chunks c
    JOIN sources s ON s.source_id = c.source_id
    JOIN locators l ON l.locator_id = c.locator_id
    LEFT JOIN source_provenance pv ON pv.source_id = s.source_id
    LEFT JOIN LATERAL (
        SELECT true AS matched
        FROM programs p
        WHERE %(program_query)s::text IS NOT NULL
          AND p.collection_id = s.collection_id AND p.source_id = s.source_id
          AND p.is_active AND p.source_index_revision = s.index_revision
          AND p.binding_status IN ('bound', 'evidence_unresolved')
          AND to_tsvector('english', %(program_query)s)
              @@ phraseto_tsquery('english', p.title)
          AND (EXISTS (SELECT 1 FROM program_item_evidence e
                       WHERE e.program_id = p.program_id AND e.chunk_id = c.chunk_id)
               OR EXISTS (SELECT 1 FROM program_field_evidence e
                          WHERE e.program_id = p.program_id AND e.chunk_id = c.chunk_id))
        LIMIT 1
    ) pm ON true
    WHERE s.collection_id = %(collection_id)s
      AND s.status = 'indexed'
      AND s.is_current
      AND (s.workspace_key IS NULL OR s.workspace_key = %(workspace_key)s)
      AND (%(source_ids)s::uuid[] IS NULL OR c.source_id = ANY(%(source_ids)s::uuid[]))
      AND (pm.matched
           OR (c.search_vector || to_tsvector('english', coalesce(l.label, '')))
              @@ to_tsquery('english', %(q)s)
           OR (%(numeric_pattern)s::text IS NOT NULL AND c.text ~ %(numeric_pattern)s::text))
),
title_scores AS MATERIALIZED (
    -- The title vector depends on the source and locator, not the chunk. Build
    -- it once per candidate locator and compare each title query against that
    -- vector instead of recomputing to_tsvector inside the unnest loop.
    SELECT m.locator_id,
           (SELECT count(*) FROM unnest(%(title_queries)s::text[]) AS title_query
            WHERE tv.title_vector @@ to_tsquery('english', title_query)) AS title_matches
    FROM (SELECT DISTINCT locator_id, source_id FROM matched) m
    JOIN sources s ON s.source_id = m.source_id
    JOIN locators l ON l.locator_id = m.locator_id
    LEFT JOIN source_provenance pv ON pv.source_id = m.source_id
    CROSS JOIN LATERAL (
        SELECT to_tsvector('english', coalesce(pv.title, '') || ' ' || coalesce(l.label, '') || ' ' ||
               regexp_replace(s.filename, '[-_.]', ' ', 'g')) AS title_vector
    ) tv
)
SELECT m.chunk_id, m.source_id, m.chunk_index, m.program_match,
       (
           CASE WHEN m.program_match THEN 4.0
                WHEN m.role_match AND t.title_matches > 0 THEN 3.5
                WHEN m.course_code_heading THEN 3.0
                WHEN m.numeric_match THEN 2.0
                WHEN t.title_matches >= 2 THEN 2.0
                WHEN m.specific THEN 1.0
                WHEN t.title_matches > 0 THEN 0.5
                ELSE 0.0 END
           + m.lexical / (1.0 + m.lexical)
       )::float8 AS score
FROM matched m
JOIN title_scores t ON t.locator_id = m.locator_id
ORDER BY score DESC, m.source_id, m.chunk_index
LIMIT %(limit)s;
