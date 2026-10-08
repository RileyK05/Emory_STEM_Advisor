-- name: list_courses_with_vectors
SELECT co.course_id, co.code, co.title, co.subject,
       AVG(e.embedding) AS mean_vector
FROM courses co
JOIN sources s          ON s.source_id = co.source_id
JOIN chunks c           ON c.locator_id = co.locator_id
JOIN chunk_embeddings e ON e.chunk_id = c.chunk_id
WHERE co.collection_id = %(collection_id)s
  AND s.status = 'indexed'
  AND e.model = %(model)s
GROUP BY co.course_id, co.code, co.title, co.subject
ORDER BY co.code;

-- name: insert_sim_build
INSERT INTO sim_builds (build_id, collection_id, model, config_version, nudges_hash, is_current)
VALUES (%(build_id)s, %(collection_id)s, %(model)s, %(config_version)s, %(nudges_hash)s, false);

-- name: clear_current_build
UPDATE sim_builds SET is_current = false
WHERE collection_id = %(collection_id)s AND is_current;

-- name: set_current_build
UPDATE sim_builds SET is_current = true WHERE build_id = %(build_id)s;

-- name: get_current_build
SELECT build_id, model, config_version, nudges_hash FROM sim_builds
WHERE collection_id = %(collection_id)s AND is_current;

-- name: insert_sim_course
INSERT INTO sim_courses (build_id, course_id, embedding)
VALUES (%(build_id)s, %(course_id)s, %(embedding)s);

-- name: insert_sim_edge
INSERT INTO sim_edges (build_id, course_a, course_b, weight)
VALUES (%(build_id)s, %(course_a)s, %(course_b)s, %(weight)s);

-- name: insert_sim_cluster
INSERT INTO sim_clusters (
    cluster_id, build_id, parent_cluster_id, level, label, medoid_course_id, centroid, size
)
VALUES (
    %(cluster_id)s, %(build_id)s, %(parent_cluster_id)s, %(level)s, %(label)s,
    %(medoid_course_id)s, %(centroid)s, %(size)s
);

-- name: insert_sim_cluster_member
INSERT INTO sim_cluster_members (cluster_id, course_id)
VALUES (%(cluster_id)s, %(course_id)s);

-- name: list_current_clusters
SELECT c.cluster_id, c.parent_cluster_id, c.level, c.label, c.medoid_course_id,
       c.centroid, c.size
FROM sim_clusters c
JOIN sim_builds b ON b.build_id = c.build_id AND b.is_current
WHERE b.collection_id = %(collection_id)s
ORDER BY c.level, c.cluster_id;

-- name: list_cluster_courses
SELECT co.course_id, co.code, co.title, sc.embedding
FROM sim_cluster_members m
JOIN sim_clusters c ON c.cluster_id = m.cluster_id
JOIN courses co     ON co.course_id = m.course_id
JOIN sim_courses sc ON sc.build_id = c.build_id AND sc.course_id = m.course_id
WHERE m.cluster_id = %(cluster_id)s
ORDER BY co.code;

-- name: list_cluster_edges
SELECT e.course_a, e.course_b, e.weight
FROM sim_clusters c
JOIN sim_edges e            ON e.build_id = c.build_id
JOIN sim_cluster_members ma ON ma.cluster_id = c.cluster_id AND ma.course_id = e.course_a
JOIN sim_cluster_members mb ON mb.cluster_id = c.cluster_id AND mb.course_id = e.course_b
WHERE c.cluster_id = %(cluster_id)s
ORDER BY e.course_a, e.course_b;

-- name: list_current_edges
SELECT e.course_a, e.course_b, e.weight
FROM sim_edges e
JOIN sim_builds b ON b.build_id = e.build_id AND b.is_current
WHERE b.collection_id = %(collection_id)s
ORDER BY e.course_a, e.course_b;
