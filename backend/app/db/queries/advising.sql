-- Advising snapshot queries.
--
-- Scope is the exact set of cited chunks, resolved to locator identity.
--
-- Retrieval relabels a cited cross-listed course with all of its official codes
-- ("AAS 325 / REL 325") while the atlas locator keeps the single code, so
-- matching on the rendered display label silently dropped those courses and
-- reported `requirements_evidence_missing`. `advising_chunk_locators` therefore
-- prefers the cited chunk's own locator_id. The label is used only when no chunk
-- row survives for that citation (historical evidence after a re-ingest, and
-- synthetic citations in tests), which is the same compatibility rule the trace
-- contract uses: `trace_contributions.chunk_id` deliberately has no foreign key.
-- The scoped queries take the already-resolved `locator_ids` and never look at a
-- display label.

-- name: advising_chunk_locators
SELECT cited.chunk_id,
       cited.source_id,
       COALESCE(ch.locator_id, loc.locator_id) AS locator_id
FROM unnest(%(chunk_ids)s::uuid[], %(source_ids)s::uuid[], %(locator_labels)s::text[])
     AS cited(chunk_id, source_id, locator_label)
LEFT JOIN chunks ch ON ch.chunk_id = cited.chunk_id
LEFT JOIN locators loc ON loc.source_id = cited.source_id
                      AND loc.label = cited.locator_label
WHERE COALESCE(ch.locator_id, loc.locator_id) IS NOT NULL;

-- name: list_advising_courses_for_scope
WITH scope AS (
    SELECT DISTINCT src.source_id, src.file_hash, cited.locator_id
    FROM unnest(%(source_ids)s::uuid[], %(locator_ids)s::uuid[])
         AS cited(source_id, locator_id)
    JOIN sources src ON src.source_id = cited.source_id
    WHERE src.collection_id = %(collection_id)s
      AND src.content_type = 'application/x-course-atlas+jsonl'
      AND src.status = 'indexed'
      AND src.is_current
)
SELECT co.course_id, co.code, co.title, co.source_id, co.locator_id,
       loc.label AS locator_label,
       co.requisites_raw, co.parse_status, co.subject, co.lifecycle,
       hold.hold_id AS requirement_hold_id,
       hold.conflict_reason AS requirement_hold_reason,
       hold.master_file_hash AS requirement_hold_master_file_hash,
       hold.evidence_capture_sha256 AS requirement_hold_capture_sha256,
       hold.evidence_capture_path AS requirement_hold_capture_path,
       hold.evidence_source_id AS requirement_hold_source_id,
       hold.evidence_index_revision AS requirement_hold_index_revision,
       hold.evidence_capture_set_hash AS requirement_hold_capture_set_hash,
       EXISTS (
           SELECT 1 FROM chunks evidence
           WHERE evidence.source_id = co.source_id AND evidence.locator_id = co.locator_id
             AND regexp_replace(evidence.text,
                 '\m(counts[[:space:]]+as|it[[:space:]]+is)[[:space:]]+(a[[:space:]]+)?(pre|co)[ -]?requisites?\M'
                 '(?=[[:space:]]+for[[:space:]]+(many[[:space:]]+)?(other\M|[0-9]{3}-level\M))'
                 '|\mno[[:space:]]+(pre|co)[ -]?requisites?[[:space:]]+(are[[:space:]]+)?(necessary|required)\M'
                 '(?=[[:space:]]*([.!?]|$))'
                 '|\mhas[[:space:]]+no[[:space:]]+(pre|co)[ -]?requisites?[[:space:]]*([.!?]|$)',
                 '', 'gi') ~* ('\m(pre|co)[ -]?requisites?\M'
                 '|\mpre[[:space:]]+and[[:space:]]+co[ -]?requisites?\M'
                 '|\msuccessfully[[:space:]]+completed[[:space:]]+[a-z]{2,}(_ox)?[[:space:]]*[0-9]{3}'
                 '|\mmust[[:space:]]+have[[:space:]]+completed[[:space:]]+[a-z]{2,}(_ox)?[[:space:]]*[0-9]{3}'
                 '|\m(must[[:space:]]+have[[:space:]]+permission[[:space:]]+of[[:space:]]+(the[[:space:]]+)?instructor'
                 '|(written[[:space:]]+)?permission[[:space:]]+of[[:space:]]+(the[[:space:]]+)?instructor[[:space:]]+(is[[:space:]]+)?required'
                 '|requires?[[:space:]]+(the[[:space:]]+)?(written[[:space:]]+)?permission[[:space:]]+of[[:space:]]+(the[[:space:]]+)?instructor'
                 '|and/or[[:space:]]+(the[[:space:]]+)?instructor[[:space:]]+permission)\M')
       ) AS description_has_requirements
FROM scope sc
JOIN courses co ON co.source_id = sc.source_id AND co.locator_id = sc.locator_id
JOIN locators loc ON loc.locator_id = co.locator_id AND loc.source_id = co.source_id
LEFT JOIN atlas_requirement_holds hold
       ON hold.source_id = co.source_id
      AND hold.course_code = co.code
      AND hold.master_file_hash = sc.file_hash
      AND hold.status = 'active'
WHERE co.collection_id = %(collection_id)s
ORDER BY co.code, co.source_id, loc.label;

-- name: list_advising_nodes_for_scope
WITH scope AS (
    SELECT DISTINCT src.source_id, cited.locator_id
    FROM unnest(%(source_ids)s::uuid[], %(locator_ids)s::uuid[])
         AS cited(source_id, locator_id)
    JOIN sources src ON src.source_id = cited.source_id
    WHERE src.collection_id = %(collection_id)s
      AND src.content_type = 'application/x-course-atlas+jsonl'
      AND src.status = 'indexed'
      AND src.is_current
)
SELECT co.course_id, n.node_id, n.kind, n.parent_node_id, n.position,
       n.node_type, n.target_code, n.condition_text
FROM scope sc
JOIN courses co ON co.source_id = sc.source_id AND co.locator_id = sc.locator_id
JOIN locators loc ON loc.locator_id = co.locator_id AND loc.source_id = co.source_id
JOIN prereq_nodes n ON n.course_id = co.course_id
WHERE co.collection_id = %(collection_id)s
ORDER BY co.course_id, n.kind, n.parent_node_id NULLS FIRST, n.position, n.node_id;

-- name: list_advising_topic_paths_for_scope
WITH RECURSIVE scope AS (
    SELECT DISTINCT src.source_id, cited.locator_id
    FROM unnest(%(source_ids)s::uuid[], %(locator_ids)s::uuid[])
         AS cited(source_id, locator_id)
    JOIN sources src ON src.source_id = cited.source_id
    WHERE src.collection_id = %(collection_id)s
      AND src.content_type = 'application/x-course-atlas+jsonl'
      AND src.status = 'indexed'
      AND src.is_current
),
grounded_courses AS (
    SELECT co.course_id, co.code
    FROM scope sc
    JOIN courses co ON co.source_id = sc.source_id AND co.locator_id = sc.locator_id
    JOIN locators loc ON loc.locator_id = co.locator_id AND loc.source_id = co.source_id
    WHERE co.collection_id = %(collection_id)s
),
leaf_topics AS (
    SELECT DISTINCT ON (gc.course_id)
           gc.course_id, gc.code, cl.cluster_id, cl.parent_cluster_id,
           cl.level, cl.label, cl.size, cl.kind
    FROM grounded_courses gc
    JOIN sim_cluster_members mem ON mem.course_id = gc.course_id
    JOIN sim_clusters cl ON cl.cluster_id = mem.cluster_id
                        AND cl.kind IN ('department', 'topic')
    JOIN sim_builds b ON b.build_id = cl.build_id
                      AND b.collection_id = %(collection_id)s
                      AND b.is_current AND b.graph_version = 2
    ORDER BY gc.course_id, cl.level DESC, cl.cluster_id
),
lineage AS (
    SELECT leaf.course_id, leaf.code, leaf.cluster_id AS leaf_cluster_id,
           leaf.size AS total_size, leaf.cluster_id, leaf.parent_cluster_id,
           leaf.level, leaf.label, leaf.kind, ARRAY[leaf.cluster_id] AS visited_ids
    FROM leaf_topics leaf
    UNION ALL
    SELECT path.course_id, path.code, path.leaf_cluster_id, path.total_size,
           parent.cluster_id, parent.parent_cluster_id, parent.level,
           parent.label, parent.kind, path.visited_ids || parent.cluster_id
    FROM lineage path
    JOIN sim_clusters parent ON parent.cluster_id = path.parent_cluster_id
    WHERE parent.kind IN ('topic', 'department')
      AND NOT parent.cluster_id = ANY(path.visited_ids)
      AND EXISTS (
          SELECT 1 FROM sim_builds b
          WHERE b.build_id = parent.build_id
            AND b.collection_id = %(collection_id)s
            AND b.is_current AND b.graph_version = 2
      )
)
SELECT course_id, code, leaf_cluster_id AS cluster_id, total_size,
       array_agg(label ORDER BY level) FILTER (WHERE kind IN ('department', 'topic')) AS path
FROM lineage
GROUP BY course_id, code, leaf_cluster_id, total_size
ORDER BY code;

-- name: get_requirement_hold_master
SELECT source_id, collection_id, content_type, file_hash, status, is_current
FROM sources
WHERE source_id = %(source_id)s
FOR UPDATE;

-- name: held_atlas_context_chunks
-- Source reads and historical citations stay available for review. Automatic
-- generation must not receive a curator-held master rule through another seam.
SELECT DISTINCT ch.chunk_id, co.code AS course_code
FROM chunks ch
JOIN sources src ON src.source_id = ch.source_id
JOIN courses co ON co.source_id = ch.source_id AND co.locator_id = ch.locator_id
JOIN atlas_requirement_holds hold
  ON hold.source_id = src.source_id AND hold.course_code = co.code
 AND hold.master_file_hash = src.file_hash AND hold.status = 'active'
WHERE ch.chunk_id = ANY(%(chunk_ids)s::uuid[])
  AND src.collection_id = %(collection_id)s
  AND src.status = 'indexed' AND src.is_current;

-- name: get_requirement_hold_course
SELECT course_id
FROM courses
WHERE source_id = %(source_id)s AND code = %(course_code)s
FOR KEY SHARE;

-- name: insert_atlas_requirement_hold
INSERT INTO atlas_requirement_holds (
    hold_id, source_id, course_code, master_file_hash, conflict_reason,
    evidence_capture_sha256, evidence_capture_path, evidence_source_id,
    evidence_index_revision, evidence_capture_set_hash, created_by
) VALUES (
    %(hold_id)s, %(source_id)s, %(course_code)s, %(master_file_hash)s, %(conflict_reason)s,
    %(evidence_capture_sha256)s, %(evidence_capture_path)s, %(evidence_source_id)s,
    %(evidence_index_revision)s, %(evidence_capture_set_hash)s, %(created_by)s
)
ON CONFLICT (source_id, course_code, master_file_hash) WHERE status = 'active' DO NOTHING
RETURNING *;

-- name: get_active_atlas_requirement_hold
SELECT *
FROM atlas_requirement_holds
WHERE source_id = %(source_id)s AND course_code = %(course_code)s
  AND master_file_hash = %(master_file_hash)s AND status = 'active';

-- name: release_atlas_requirement_hold
UPDATE atlas_requirement_holds
SET status = 'released', released_by = %(released_by)s,
    released_at = now(), release_note = %(release_note)s
WHERE hold_id = %(hold_id)s
  AND source_id = %(source_id)s
  AND course_code = %(course_code)s
  AND master_file_hash = %(master_file_hash)s
  AND status = 'active'
RETURNING *;
