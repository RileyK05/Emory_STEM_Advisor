-- name: keyword_search
WITH matching_programs AS MATERIALIZED (
    SELECT p.program_id, p.source_id, p.source_index_revision
    FROM programs p
    WHERE %(program_query)s::text IS NOT NULL
      AND p.collection_id = %(collection_id)s
      AND p.is_active
      AND p.binding_status IN ('bound', 'evidence_unresolved')
      AND to_tsvector('english', %(program_query)s)
          @@ phraseto_tsquery('english', regexp_replace(p.title, '\s*\(.*$', ''))
),
program_chunks AS MATERIALIZED (
    SELECT DISTINCT e.chunk_id
    FROM matching_programs mp
    JOIN sources s ON s.source_id = mp.source_id
                  AND s.index_revision = mp.source_index_revision
    JOIN program_item_evidence e ON e.program_id = mp.program_id
    UNION
    SELECT DISTINCT e.chunk_id
    FROM matching_programs mp
    JOIN sources s ON s.source_id = mp.source_id
                  AND s.index_revision = mp.source_index_revision
    JOIN program_field_evidence e ON e.program_id = mp.program_id
),
parsed_title_queries AS MATERIALIZED (
    SELECT to_tsquery('english', tq) AS query_ts
    FROM unnest(%(title_queries)s::text[]) AS tq
),
any_title_query AS MATERIALIZED (
    SELECT string_agg('(' || tq || ')', ' | ') AS combined_raw
    FROM unnest(%(title_queries)s::text[]) AS tq
),
parsed_any_title AS MATERIALIZED (
    SELECT CASE WHEN combined_raw IS NOT NULL AND combined_raw != ''
                THEN to_tsquery('english', combined_raw)
                ELSE NULL END AS any_ts
    FROM any_title_query
),
parsed_q AS MATERIALIZED (
    SELECT to_tsquery('english', %(q)s) AS q_ts
),
parsed_specific_q AS MATERIALIZED (
    SELECT CASE WHEN %(specific_q)s::text IS NOT NULL
                THEN to_tsquery('english', %(specific_q)s)
                ELSE NULL END AS specific_ts
),
parsed_role_q AS MATERIALIZED (
    SELECT CASE WHEN %(role_query)s::text IS NOT NULL
                THEN plainto_tsquery('english', %(role_query)s)
                ELSE NULL END AS role_ts
),
parsed_full_q AS MATERIALIZED (
    SELECT to_tsvector('english', %(full_query)s) AS full_vector
),
matched AS MATERIALIZED (
    SELECT c.chunk_id,
           c.source_id,
           c.chunk_index,
           c.locator_id,
           (
               ts_rank(c.search_vector, pq.q_ts)
               + CASE WHEN c.text ~ %(numeric_pattern)s THEN 1.0 ELSE 0.0 END
           ) AS lexical,
           (pc.chunk_id IS NOT NULL) AS program_match,
           (prq.role_ts IS NOT NULL AND pv.roles_vector @@ prq.role_ts) AS role_match,
           CASE
               WHEN psq.specific_ts IS NULL THEN false
               ELSE c.search_vector @@ psq.specific_ts
           END AS specific,
           (%(course_code_header_pattern)s::text IS NOT NULL
            AND c.text ~* %(course_code_header_pattern)s::text) AS course_code_heading,
           (%(numeric_pattern)s::text IS NOT NULL
            AND c.text ~ %(numeric_pattern)s::text) AS numeric_match
    FROM chunks c
    CROSS JOIN parsed_q pq
    CROSS JOIN parsed_specific_q psq
    CROSS JOIN parsed_role_q prq
    JOIN sources s ON s.source_id = c.source_id
    JOIN locators l ON l.locator_id = c.locator_id
    LEFT JOIN source_provenance pv ON pv.source_id = s.source_id
    LEFT JOIN program_chunks pc ON pc.chunk_id = c.chunk_id
    WHERE s.collection_id = %(collection_id)s
      AND s.status = 'indexed'
      AND s.is_current
      AND (s.workspace_key IS NULL OR s.workspace_key = %(workspace_key)s)
      AND (%(source_ids)s::uuid[] IS NULL OR c.source_id = ANY(%(source_ids)s::uuid[]))
      AND NOT c.search_hidden
      AND (
           pc.chunk_id IS NOT NULL
           OR c.search_vector @@ pq.q_ts
           OR l.label_vector @@ pq.q_ts
           OR (%(numeric_pattern)s::text IS NOT NULL AND c.text ~ %(numeric_pattern)s::text)
      )
),
locator_vectors AS MATERIALIZED (
    SELECT m.locator_id,
           (
               coalesce(pv.title_vector, ''::tsvector) ||
               coalesce(l.label_vector, ''::tsvector) ||
               coalesce(co.title_vector, ''::tsvector) ||
               coalesce(s.filename_vector, ''::tsvector)
           ) AS title_vector,
           co.title AS course_title
    FROM (SELECT DISTINCT locator_id, source_id FROM matched) m
    JOIN sources s ON s.source_id = m.source_id
    JOIN locators l ON l.locator_id = m.locator_id
    LEFT JOIN source_provenance pv ON pv.source_id = m.source_id
    LEFT JOIN courses co ON co.locator_id = m.locator_id
),
title_scores AS MATERIALIZED (
    SELECT lv.locator_id,
           CASE
               WHEN pat.any_ts IS NOT NULL AND lv.title_vector @@ pat.any_ts THEN
                   (SELECT count(*) FROM parsed_title_queries ptq WHERE lv.title_vector @@ ptq.query_ts)
               ELSE 0
           END AS title_matches,
           CASE
               WHEN lv.course_title IS NOT NULL THEN
                   coalesce(pfq.full_vector @@ phraseto_tsquery('english', lv.course_title), false)
               ELSE false
           END AS names_course
    FROM locator_vectors lv
    CROSS JOIN parsed_any_title pat
    CROSS JOIN parsed_full_q pfq
)
SELECT m.chunk_id, m.source_id, m.chunk_index, m.program_match,
       (
           CASE WHEN m.program_match THEN 4.0
                WHEN m.role_match AND t.title_matches > 0 THEN 3.5
                WHEN m.course_code_heading OR t.names_course THEN 3.0
                WHEN m.numeric_match THEN 2.0
                WHEN t.title_matches >= 2 THEN 2.0
                WHEN m.specific AND t.title_matches > 0 THEN 1.5
                WHEN m.specific THEN 1.0
                WHEN t.title_matches > 0 THEN 0.5
                ELSE 0.0 END
           + m.lexical / (1.0 + m.lexical)
       )::float8 AS score
FROM matched m
JOIN title_scores t ON t.locator_id = m.locator_id
ORDER BY score DESC, m.source_id, m.chunk_index
LIMIT %(limit)s;
