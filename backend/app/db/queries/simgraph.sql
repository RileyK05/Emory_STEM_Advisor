-- name: list_courses_with_vectors
SELECT co.course_id, co.code, co.title, co.subject,
       AVG(e.embedding) AS mean_vector
FROM courses co
JOIN sources s          ON s.source_id = co.source_id
JOIN chunks c           ON c.locator_id = co.locator_id
JOIN chunk_embeddings e ON e.chunk_id = c.chunk_id
WHERE co.collection_id = %(collection_id)s
  AND co.lifecycle = 'current'
  AND s.status = 'indexed'
  AND s.is_current
  AND e.model = %(model)s
GROUP BY co.course_id, co.code, co.title, co.subject
ORDER BY co.code;

-- name: insert_sim_build
INSERT INTO sim_builds (build_id, collection_id, model, config_version, nudges_hash, is_current, content_hash)
VALUES (%(build_id)s, %(collection_id)s, %(model)s, %(config_version)s, %(nudges_hash)s, false,
        %(content_hash)s);

-- name: clear_current_build
UPDATE sim_builds SET is_current = false
WHERE collection_id = %(collection_id)s AND is_current;

-- name: set_current_build
UPDATE sim_builds SET is_current = true WHERE build_id = %(build_id)s;

-- name: get_current_build
SELECT build_id, model, config_version, nudges_hash, graph_version, departments_hash, content_hash
FROM sim_builds
WHERE collection_id = %(collection_id)s AND is_current AND graph_version = 2;

-- name: get_current_atlas_source_hash
SELECT file_hash FROM sources
WHERE collection_id = %(collection_id)s
  AND content_type = 'application/x-course-atlas+jsonl'
  AND is_current
LIMIT 1;

-- name: retire_stale_topic_build
-- A build is served as current only while the catalog it snapshotted is the
-- published one. A changed (or unbound) catalog retires it, never silently
-- continues serving yesterday's groups.
UPDATE sim_builds SET is_current = false
WHERE collection_id = %(collection_id)s AND is_current AND graph_version = 2
  AND content_hash IS DISTINCT FROM (
      SELECT s.file_hash FROM sources s
      WHERE s.collection_id = %(collection_id)s
        AND s.content_type = 'application/x-course-atlas+jsonl'
        AND s.is_current
      LIMIT 1
  );

-- name: retire_topic_build_for_atlas
UPDATE sim_builds SET is_current = false
WHERE collection_id = %(collection_id)s AND is_current AND graph_version = 2
  AND content_hash IS DISTINCT FROM %(file_hash)s;

-- name: insert_sim_course
INSERT INTO sim_courses (build_id, course_id, embedding)
VALUES (%(build_id)s, %(course_id)s, %(embedding)s);

-- name: set_sim_departments_hash
UPDATE sim_builds SET departments_hash = %(departments_hash)s WHERE build_id = %(build_id)s;

-- name: insert_sim_cluster
INSERT INTO sim_clusters (
    cluster_id, build_id, parent_cluster_id, level, label, medoid_course_id, centroid, size,
    kind, department_key, medoid_code, medoid_title
)
VALUES (
    %(cluster_id)s, %(build_id)s, %(parent_cluster_id)s, %(level)s, %(label)s,
    %(medoid_course_id)s, %(centroid)s, %(size)s, %(kind)s, %(department_key)s,
    %(medoid_code)s, %(medoid_title)s
);

-- name: insert_sim_cluster_member
INSERT INTO sim_cluster_members (cluster_id, course_id, code, title)
VALUES (%(cluster_id)s, %(course_id)s, %(code)s, %(title)s);

-- name: list_current_clusters
SELECT c.cluster_id, c.parent_cluster_id, c.level, c.label, c.medoid_course_id,
       c.medoid_code, c.medoid_title, c.centroid, c.size, c.kind, c.department_key
FROM sim_clusters c
JOIN sim_builds b ON b.build_id = c.build_id AND b.is_current
WHERE b.collection_id = %(collection_id)s AND b.graph_version = 2
ORDER BY c.level, c.cluster_id;

-- name: list_cluster_courses
-- Read the build's own course snapshot, never the live catalog: a course that
-- left the catalog must not remove rows from a saved tree.
SELECT m.course_id, m.code, m.title, sc.embedding
FROM sim_cluster_members m
JOIN sim_clusters c ON c.cluster_id = m.cluster_id
JOIN sim_courses sc ON sc.build_id = c.build_id AND sc.course_id = m.course_id
WHERE m.cluster_id = %(cluster_id)s
ORDER BY m.code;
