-- name: keyword_search
-- Exact bound program evidence, requested verified checklist metadata, course
-- headings and numeric references outrank topical matches. Lexical rank stays
-- bounded inside each band; single generic title words confer no title band.
SELECT chunk_id, source_id, chunk_index, program_match,
       (
           CASE WHEN program_match THEN 4.0
                WHEN role_match AND title_matches > 0 THEN 3.5
                WHEN course_code_heading THEN 3.0
                WHEN numeric_match THEN 2.0
                WHEN title_matches >= 2 THEN 2.0
                WHEN specific THEN 1.0
                WHEN title_matches > 0 THEN 0.5
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
            AND c.text ~ %(numeric_pattern)s::text) AS numeric_match,
           (SELECT count(*) FROM unnest(%(title_queries)s::text[]) AS title_query
            WHERE to_tsvector('english', coalesce(pv.title, '') || ' ' || coalesce(l.label, '') || ' ' ||
                  regexp_replace(s.filename, '[-_.]', ' ', 'g'))
                  @@ to_tsquery('english', title_query)) AS title_matches
    FROM chunks c
    JOIN sources s ON s.source_id = c.source_id
    JOIN locators l ON l.locator_id = c.locator_id
    LEFT JOIN source_provenance pv ON pv.source_id = s.source_id
    LEFT JOIN LATERAL (
        SELECT true AS matched
        FROM programs p
        WHERE p.collection_id = s.collection_id AND p.source_id = s.source_id
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
      AND (pm.matched OR c.search_vector @@ to_tsquery('english', %(q)s)
           OR (%(numeric_pattern)s::text IS NOT NULL AND c.text ~ %(numeric_pattern)s::text))
) matched
ORDER BY score DESC, source_id, chunk_index
LIMIT %(limit)s;
